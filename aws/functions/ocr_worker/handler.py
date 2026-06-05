"""OCR Worker Lambda

入力: SQS ocr-queue
  body = {"job_id": "...", "crop_id": "...", "crop_key": "crops/<job_id>/<crop_id>.jpg"}

処理:
  1. S3 から crop bytes 取得
  2. Gemini OCR でタイトル抽出
  3. DDB crops テーブルに titles 書込、status=ocr_done
  4. jobs テーブル ocr_done を ATOMIC INCR、戻り値で crop_total と比較
  5. 全件完了なら lookup_queue にメッセージ投入
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr

BUCKET = os.environ["BUCKET"]
JOBS_TABLE = os.environ["JOBS_TABLE"]
CROPS_TABLE = os.environ["CROPS_TABLE"]
LOOKUP_QUEUE_URL = os.environ["LOOKUP_QUEUE_URL"]
SECRET_ARN = os.environ["SECRET_GEMINI_ARN"]
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite-preview")

s3 = boto3.client("s3")
sqs = boto3.client("sqs")
sm = boto3.client("secretsmanager")
ddb = boto3.resource("dynamodb")
jobs_table = ddb.Table(JOBS_TABLE)
crops_table = ddb.Table(CROPS_TABLE)

_gemini_key: str | None = None


def get_gemini_key() -> str:
    global _gemini_key
    if _gemini_key is None:
        resp = sm.get_secret_value(SecretId=SECRET_ARN)
        secret = json.loads(resp["SecretString"])
        _gemini_key = secret["GEMINI_API_KEY"]
    return _gemini_key


OCR_PROMPT = """\
この画像は本が入った箱、または本棚の一区画を切り出したものです。
背表紙から読み取れる本のタイトルだけをすべて抽出し、JSON object だけを返してください。

形式:
{
  "books": [
    {
      "title": "書名"
    }
  ]
}

ルール:
- 1冊につき1エントリ
- タイトルが読めない本は除外
- 著者名、出版社、ISBNは抽出しない
- タイトル以外の文字を無理に混ぜない
- 説明文や Markdown は不要。JSON object だけ返す
"""


def strip_json_md(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1].strip()
            if text.startswith("json"):
                text = text[4:].strip()
    return text


def parse_titles(response_text: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(strip_json_md(response_text or ""))
    except json.JSONDecodeError:
        return []
    books = data.get("books", data if isinstance(data, list) else [])
    out = []
    for b in books:
        if isinstance(b, dict):
            t = b.get("title")
            if t and str(t).strip():
                out.append({"title": str(t).strip()})
    return out


def gemini_ocr(image_bytes: bytes, mime: str) -> list[dict[str, Any]]:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=get_gemini_key())
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type=mime),
            OCR_PROMPT,
        ],
    )
    return parse_titles(response.text or "")


def process_one(job_id: str, crop_id: str, crop_key: str) -> None:
    print(f"[ocr] job_id={job_id} crop_id={crop_id}")
    obj = s3.get_object(Bucket=BUCKET, Key=crop_key)
    raw = obj["Body"].read()
    mime = "image/jpeg" if crop_key.endswith((".jpg", ".jpeg")) else "image/png"

    error = None
    titles: list[dict[str, Any]] = []
    try:
        titles = gemini_ocr(raw, mime)
    except Exception as e:
        error = str(e)
        print(f"[ocr] gemini error: {error}", file=sys.stderr)

    update_expr = "SET titles = :t, #s = :s"
    eav: dict[str, Any] = {":t": titles, ":s": "ocr_done"}
    ean = {"#s": "status"}
    if error:
        update_expr += ", ocr_error = :e"
        eav[":e"] = error
    crops_table.update_item(
        Key={"job_id": job_id, "crop_id": crop_id},
        UpdateExpression=update_expr,
        ExpressionAttributeNames=ean,
        ExpressionAttributeValues=eav,
    )

    # ATOMIC INCR jobs.ocr_done
    out = jobs_table.update_item(
        Key={"job_id": job_id},
        UpdateExpression="ADD ocr_done :one",
        ExpressionAttributeValues={":one": 1},
        ReturnValues="ALL_NEW",
    )
    item = out.get("Attributes", {})
    done = int(item.get("ocr_done", 0))
    total = int(item.get("crop_total", 0))
    print(f"[ocr] progress {done}/{total}")
    if total > 0 and done >= total:
        # 最後の OCR が lookup_queue に投入
        sqs.send_message(
            QueueUrl=LOOKUP_QUEUE_URL,
            MessageBody=json.dumps({"job_id": job_id}),
        )
        jobs_table.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET #s = :s",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": "lookup_pending"},
        )


def handler(event, context):
    for rec in event.get("Records", []):
        body = json.loads(rec["body"])
        process_one(body["job_id"], body["crop_id"], body["crop_key"])
    return {"ok": True}
