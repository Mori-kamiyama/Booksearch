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
import time
import uuid
from typing import Any

import boto3
from botocore.exceptions import ClientError

BUCKET = os.environ["BUCKET"]
JOBS_TABLE = os.environ["JOBS_TABLE"]
CROPS_TABLE = os.environ["CROPS_TABLE"]
OUTBOX_TABLE = os.environ["OUTBOX_TABLE"]
SECRET_ARN = os.environ["SECRET_GEMINI_ARN"]
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite-preview")

s3 = boto3.client("s3")
sm = boto3.client("secretsmanager")
ddb = boto3.resource("dynamodb")
jobs_table = ddb.Table(JOBS_TABLE)
crops_table = ddb.Table(CROPS_TABLE)
ddb_client = boto3.client("dynamodb")
OCR_LEASE_SECONDS = int(os.environ.get("OCR_LEASE_SECONDS", "180"))

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


def claim_crop(job_id: str, crop_id: str) -> str | None:
    token, now = uuid.uuid4().hex, int(time.time())
    try:
        crops_table.update_item(
            Key={"job_id": job_id, "crop_id": crop_id},
            UpdateExpression="SET #s = :processing, ocr_lease_token = :token, ocr_lease_until = :until",
            ConditionExpression="#s = :pending OR (#s = :processing AND ocr_lease_until < :now)",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":pending": "ocr_pending", ":processing": "ocr_processing", ":token": token, ":until": now + OCR_LEASE_SECONDS, ":now": now},
        )
        return token
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return None
        raise


def release_crop(job_id: str, crop_id: str, token: str) -> None:
    """A transient Gemini failure remains retryable after this SQS delivery."""
    try:
        crops_table.update_item(Key={"job_id": job_id, "crop_id": crop_id}, UpdateExpression="SET #s = :pending REMOVE ocr_lease_token, ocr_lease_until", ConditionExpression="#s = :processing AND ocr_lease_token = :token", ExpressionAttributeNames={"#s": "status"}, ExpressionAttributeValues={":pending": "ocr_pending", ":processing": "ocr_processing", ":token": token})
    except ClientError:
        pass


def complete_crop(job_id: str, crop_id: str, token: str, titles: list[dict[str, Any]]) -> None:
    """Commit one crop and its job progress in a single transaction."""
    ddb_client.transact_write_items(TransactItems=[
        {"Update": {"TableName": CROPS_TABLE, "Key": {"job_id": {"S": job_id}, "crop_id": {"S": crop_id}}, "UpdateExpression": "SET titles = :titles, #s = :done REMOVE ocr_lease_token, ocr_lease_until", "ConditionExpression": "#s = :processing AND ocr_lease_token = :token", "ExpressionAttributeNames": {"#s": "status"}, "ExpressionAttributeValues": {":titles": {"L": [{"M": {"title": {"S": str(t["title"])}}} for t in titles]}, ":done": {"S": "ocr_done"}, ":processing": {"S": "ocr_processing"}, ":token": {"S": token}}}},
        {"Update": {"TableName": JOBS_TABLE, "Key": {"job_id": {"S": job_id}}, "UpdateExpression": "ADD ocr_done :one", "ConditionExpression": "#s = :pending", "ExpressionAttributeNames": {"#s": "status"}, "ExpressionAttributeValues": {":one": {"N": "1"}, ":pending": {"S": "ocr_pending"}}}},
    ])


def queue_lookup_outbox(job_id: str) -> None:
    """One durable intent; the dispatcher owns all SQS retries."""
    now = str(int(time.time()))
    try:
        ddb_client.transact_write_items(TransactItems=[
            {"Update": {"TableName": JOBS_TABLE, "Key": {"job_id": {"S": job_id}}, "UpdateExpression": "SET #s = :lookup", "ConditionExpression": "#s = :ocr AND ocr_done >= ocr_total", "ExpressionAttributeNames": {"#s": "status"}, "ExpressionAttributeValues": {":lookup": {"S": "lookup_pending"}, ":ocr": {"S": "ocr_pending"}}}},
            {"Put": {"TableName": OUTBOX_TABLE, "Item": {"job_id": {"S": job_id}, "event_type": {"S": "lookup"}, "created_at": {"S": now}}, "ConditionExpression": "attribute_not_exists(job_id)"}},
        ])
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "TransactionCanceledException":
            raise


def process_one(job_id: str, crop_id: str, crop_key: str) -> None:
    token = claim_crop(job_id, crop_id)
    if not token:
        return
    print(f"[ocr] job_id={job_id} crop_id={crop_id}")
    obj = s3.get_object(Bucket=BUCKET, Key=crop_key)
    raw = obj["Body"].read()
    mime = "image/jpeg" if crop_key.endswith((".jpg", ".jpeg")) else "image/png"

    try:
        titles = gemini_ocr(raw, mime)
    except Exception as e:
        print(f"[ocr] gemini error: {e}", file=sys.stderr)
        release_crop(job_id, crop_id, token)
        raise
    complete_crop(job_id, crop_id, token, titles)
    item = jobs_table.get_item(Key={"job_id": job_id}, ConsistentRead=True).get("Item") or {}
    if int(item.get("ocr_done", 0)) >= int(item.get("ocr_total", 0)) > 0:
        queue_lookup_outbox(job_id)


def handler(event, context):
    for rec in event.get("Records", []):
        body = json.loads(rec["body"])
        process_one(body["job_id"], body["crop_id"], body["crop_key"])
    return {"ok": True}
