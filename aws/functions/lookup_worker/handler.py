"""DB Lookup Worker Lambda

入力: SQS lookup-queue
  body = {"job_id": "..."}

処理:
  1. DDB から job と全 crops を取得
  2. crops.titles それぞれを SQLite (library.db) で照合
  3. 候補が無ければ known_books.json で補完
  4. catalog.json を組み立てて S3 PUT (catalogs/<job_id>/catalog.json)
  5. jobs.status = "done"
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import sys
import time
import unicodedata
from decimal import Decimal
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

BUCKET = os.environ["BUCKET"]
JOBS_TABLE = os.environ["JOBS_TABLE"]
CROPS_TABLE = os.environ["CROPS_TABLE"]
SHELF_OBSERVATIONS_TABLE = os.environ.get("SHELF_OBSERVATIONS_TABLE")
SHELF_CANDIDATES_TABLE = os.environ.get("SHELF_CANDIDATES_TABLE")
TASK_ROOT = os.environ.get("LAMBDA_TASK_ROOT", ".")

DB_PATH = Path(TASK_ROOT) / "assets" / "library.db"
KNOWN_BOOKS_PATH = Path(TASK_ROOT) / "assets" / "known_books.json"

s3 = boto3.client("s3")
ddb = boto3.resource("dynamodb")
jobs_table = ddb.Table(JOBS_TABLE)
crops_table = ddb.Table(CROPS_TABLE)
shelf_observations_table = ddb.Table(SHELF_OBSERVATIONS_TABLE) if SHELF_OBSERVATIONS_TABLE else None
shelf_candidates_table = ddb.Table(SHELF_CANDIDATES_TABLE) if SHELF_CANDIDATES_TABLE else None


# ---------- normalize / score ----------
def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).lower()
    return re.sub(r"[\s　・:：,，.．。『』「」\"'“”‘’!?！？\-‐‑‒–—―（）()【】\[\]]+", "", text)


def normalize_isbn(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"[^0-9xX]", "", str(value)).upper()


# 1-2文字のOCR断片が長い書名/著者名にたまたま含まれるだけで
# 0.7以上のスコアが付き、無関係な本に自動一致していたため、
# 部分一致による高スコアは共有断片が一定文字数以上のときだけ許可する。
MIN_TRUSTED_SUBSTRING_LEN = 4


def score_text(qn: str, vn: str) -> float:
    if not qn or not vn:
        return 0.0
    if qn == vn:
        return 1.0
    if qn in vn and len(qn) >= MIN_TRUSTED_SUBSTRING_LEN:
        return min(0.98, 0.7 + len(qn) / len(vn) * 0.25)
    if vn in qn and len(vn) >= MIN_TRUSTED_SUBSTRING_LEN:
        if len(vn) >= 6:
            return min(0.96, 0.82 + len(vn) / len(qn) * 0.15)
        return min(0.94, 0.65 + len(vn) / len(qn) * 0.25)
    return SequenceMatcher(None, qn, vn).ratio()


# ---------- library search ----------
def search_library(con, title: str, limit: int = 5) -> list[dict[str, Any]]:
    qn = normalize_text(title)
    if not qn:
        return []
    like = f"%{qn}%"
    rows = con.execute(
        """
        SELECT b.*, bc.thumbnail, bc.info_link, bc.matched_title AS cover_matched_title
        FROM books b
        LEFT JOIN book_covers bc ON bc.book_id = b.id
        WHERE b.title_norm LIKE ? OR b.authors_norm LIKE ? OR b.publisher_norm LIKE ?
        LIMIT 200
        """,
        [like, like, like],
    ).fetchall()

    results = []
    seen = set()
    for row in rows:
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        t = score_text(qn, row["title_norm"] or "")
        a = score_text(qn, row["authors_norm"] or "") * 0.9
        p = score_text(qn, row["publisher_norm"] or "") * 0.8
        score = max(t, a, p)
        if score >= 0.72:
            results.append({
                "source": "library_db",
                "score": round(score, 4),
                "match_confidence": "auto" if score >= 0.85 else "review",
                "title": row["title"],
                "authors": [row["authors"]] if row["authors"] else [],
                "publisher": row["publisher"],
                "published_date": row["published_date"],
                "class_number": row["class_number"],
                "acquisition_type": row["acquisition_type"],
                "registration_number": row["registration_number"],
                "isbns": [row["isbn"]] if row["isbn"] else [],
                "library_db_id": row["id"],
                "thumbnail": row["thumbnail"] if "thumbnail" in row.keys() else None,
                "info_link": row["info_link"] if "info_link" in row.keys() else None,
            })
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:limit]


def known_books_candidates(title: str, records: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    if not title:
        return []
    qn = normalize_text(title)
    out = []
    for rec in records:
        aliases = [rec.get("title"), *(rec.get("aliases") or [])]
        score = max(score_text(qn, normalize_text(a)) for a in aliases if a)
        if score < 0.72:
            continue
        out.append({
            "source": "known_books",
            "score": round(score, 4),
            "match_confidence": "auto" if score >= 0.85 else "review",
            "title": rec.get("title"),
            "authors": rec.get("authors") or [],
            "publisher": rec.get("publisher"),
            "published_date": rec.get("published_date"),
            "class_number": rec.get("class_number"),
            "acquisition_type": rec.get("acquisition_type"),
            "registration_number": rec.get("registration_number"),
            "isbns": rec.get("isbns") or [],
            "library_db_id": None,
            "thumbnail": rec.get("thumbnail"),
            "info_link": rec.get("info_link"),
        })
    out.sort(key=lambda x: x["score"], reverse=True)
    return out[:limit]


# ---------- DDB helpers ----------
def fetch_crops(job_id: str) -> list[dict[str, Any]]:
    items = []
    last_key = None
    while True:
        kwargs = {"KeyConditionExpression": Key("job_id").eq(job_id)}
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        out = crops_table.query(**kwargs)
        items.extend(out.get("Items", []))
        last_key = out.get("LastEvaluatedKey")
        if not last_key:
            break
    items.sort(key=lambda x: x.get("crop_id", ""))
    return items


def jsonify(obj):
    if isinstance(obj, Decimal):
        n = float(obj)
        if n.is_integer():
            return int(n)
        return n
    if isinstance(obj, dict):
        return {k: jsonify(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [jsonify(x) for x in obj]
    return obj


def candidate_confidence(avg_score: float, observations: int) -> float:
    if observations <= 0:
        return 0.0
    confidence = avg_score * (1 - math.pow(0.72, observations))
    return min(0.99, round(confidence, 4))


def put_shelf_observation(
    job_id: str,
    crop_id: str,
    shelf_id: str | None,
    candidate: dict[str, Any],
) -> bool:
    if not shelf_observations_table or not shelf_id:
        return False
    book_id = candidate.get("library_db_id")
    if not book_id:
        return False
    score = float(candidate.get("score") or 0)
    observation_id = f"{job_id}:{crop_id}:{book_id}:{shelf_id}"
    try:
        shelf_observations_table.put_item(
            Item={
                "observation_id": observation_id,
                "job_id": job_id,
                "crop_id": crop_id,
                "book_id": int(book_id),
                "shelf_id": shelf_id,
                "score": Decimal(str(score)),
                "title": candidate.get("title") or "",
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
            ConditionExpression="attribute_not_exists(observation_id)",
        )
        return True
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return False
        raise


def refresh_shelf_candidate(book_id: int, shelf_id: str, candidate: dict[str, Any]) -> None:
    if not shelf_observations_table or not shelf_candidates_table:
        return
    scan = shelf_observations_table.scan(
        FilterExpression="book_id = :b AND shelf_id = :s",
        ExpressionAttributeValues={":b": book_id, ":s": shelf_id},
    )
    items = scan.get("Items", [])
    while "LastEvaluatedKey" in scan:
        scan = shelf_observations_table.scan(
            FilterExpression="book_id = :b AND shelf_id = :s",
            ExpressionAttributeValues={":b": book_id, ":s": shelf_id},
            ExclusiveStartKey=scan["LastEvaluatedKey"],
        )
        items.extend(scan.get("Items", []))
    observations = len(items)
    if observations == 0:
        return
    avg_score = sum(float(x.get("score", 0)) for x in items) / observations
    shelf_candidates_table.put_item(
        Item={
            "book_id": book_id,
            "shelf_id": shelf_id,
            "observations": observations,
            "avg_score": Decimal(str(round(avg_score, 4))),
            "confidence": Decimal(str(candidate_confidence(avg_score, observations))),
            "title": candidate.get("title") or "",
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    )


def update_shelf_confidence(catalog: dict[str, Any]) -> int:
    added = 0
    touched: set[tuple[int, str, str]] = set()
    job_id = catalog["job_id"]
    for entry in catalog.get("entries", []):
        if entry.get("ocr_error") == "skipped_duplicate_crop":
            continue
        shelf_id = entry.get("shelf_id")
        if not shelf_id:
            continue
        for book in entry.get("books", []):
            lookup = book.get("book_lookup") or {}
            candidates = lookup.get("candidates") or []
            if not candidates:
                continue
            candidate = candidates[0]
            book_id = candidate.get("library_db_id")
            if not book_id:
                continue
            if put_shelf_observation(job_id, entry["crop_id"], shelf_id, candidate):
                added += 1
            touched.add((int(book_id), shelf_id, json.dumps(candidate, ensure_ascii=False, sort_keys=True)))
    for book_id, shelf_id, candidate_json in touched:
        refresh_shelf_candidate(book_id, shelf_id, json.loads(candidate_json))
    return added


# ---------- main ----------
def build_catalog(job_id: str) -> dict[str, Any]:
    job_resp = jobs_table.get_item(Key={"job_id": job_id})
    job = job_resp.get("Item", {})
    crops = fetch_crops(job_id)
    print(f"[lookup] job_id={job_id} crops={len(crops)}")

    known_books: list[dict[str, Any]] = []
    if KNOWN_BOOKS_PATH.exists():
        try:
            known_books = json.loads(KNOWN_BOOKS_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[lookup] known_books load failed: {e}")

    entries = []
    crops_by_id = {crop.get("crop_id"): crop for crop in crops}
    if DB_PATH.exists():
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro&immutable=1", uri=True)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only = ON")
    else:
        con = None
        print("[lookup] WARNING: library.db not bundled")

    for crop in crops:
        quality = crop.get("quality") or {}
        if crop.get("status") == "skipped_low_quality" or quality.get("readable") is False:
            print(f"[lookup] skip unreadable crop {crop.get('crop_id')} reasons={quality.get('reasons')}")
            continue

        crop_titles = crop.get("titles") or []
        existing_ref = crop.get("existing_ocr_ref") or {}
        if not crop_titles and existing_ref.get("source") == "session":
            referenced = crops_by_id.get(existing_ref.get("crop_id")) or {}
            crop_titles = referenced.get("titles") or []
        enriched = []
        for book in crop_titles:
            title = book.get("title")
            candidates = []
            if con and title:
                candidates = search_library(con, title)
            if not candidates and title and known_books:
                candidates = known_books_candidates(title, known_books)
            enriched.append({
                "title": title,
                "book_lookup": {"query": title, "source": "library_db",
                                "candidates": candidates} if title else None,
            })
        entry = {
            "box_id": f"{job_id}:{crop['crop_id']}",
            "crop_id": crop["crop_id"],
            "crop_key": crop["crop_key"],
            "crop_image": f"s3://{BUCKET}/{crop['crop_key']}",
            "bbox_xyxy": crop.get("bbox_xyxy"),
            "detector_confidence": crop.get("detector_confidence"),
            "crop_quality": crop.get("quality"),
            "shelf_id": (crop.get("shelf") or {}).get("shelf_id") if crop.get("shelf") else None,
            "shelf_assignment": crop.get("shelf"),
            "ocr_error": crop.get("ocr_error"),
            "existing_ocr_ref": existing_ref or None,
            "books": enriched,
        }
        entries.append(entry)

    if con:
        con.close()

    catalog = {
        "job_id": job_id,
        "image_key": job.get("image_key"),
        "image_width": int(job.get("image_width", 0)) if job.get("image_width") else None,
        "image_height": int(job.get("image_height", 0)) if job.get("image_height") else None,
        "detector_model": "yolo11n_quick (bundled)",
        "gemini_model": os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite-preview"),
        "library_db": str(DB_PATH) if DB_PATH.exists() else None,
        "entries": entries,
    }
    return jsonify(catalog)


def process_job(job_id: str, incremental: bool = False) -> None:
    catalog = build_catalog(job_id)
    shelf_observations_added = update_shelf_confidence(catalog)
    key = f"catalogs/{job_id}/catalog.json"
    s3.put_object(
        Bucket=BUCKET, Key=key,
        Body=json.dumps(catalog, ensure_ascii=False, indent=2).encode("utf-8"),
        ContentType="application/json; charset=utf-8",
    )

    entries = catalog.get("entries", [])
    shelf_count = len({entry.get("shelf_id") for entry in entries if entry.get("shelf_id")})
    book_count = sum(len(entry.get("books") or []) for entry in entries)
    if incremental:
        jobs_table.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET catalog_key = :k, updated_at = :t, detected_shelf_count = :sc, "
                             "detected_book_count = :bc ADD result_revision :one",
            ExpressionAttributeValues={
                ":k": key, ":t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                ":sc": shelf_count, ":bc": book_count, ":one": 1,
            },
        )
        print(f"[lookup] partial {job_id} books={book_count} -> {key}")
    else:
        jobs_table.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET #s = :s, catalog_key = :k, updated_at = :t, shelf_observations_added = :soa, "
                             "detected_shelf_count = :sc, detected_book_count = :bc",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": "done", ":k": key,
                ":t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                ":soa": shelf_observations_added, ":sc": shelf_count, ":bc": book_count,
            },
        )
        print(f"[lookup] done {job_id} -> {key}")


def handler(event, context):
    for rec in event.get("Records", []):
        body = json.loads(rec["body"])
        try:
            process_job(body["job_id"], bool(body.get("incremental")))
        except Exception as e:
            print(f"[lookup] ERROR: {e}", file=sys.stderr)
            jobs_table.update_item(
                Key={"job_id": body["job_id"]},
                UpdateExpression="SET #s = :s, #e = :e",
                ExpressionAttributeNames={"#s": "status", "#e": "error"},
                ExpressionAttributeValues={":s": "failed", ":e": str(e)},
            )
            raise
    return {"ok": True}
