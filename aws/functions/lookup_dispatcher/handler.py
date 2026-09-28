"""Dispatch final lookup work from the JobsTable outbox stream.

The producer marks a job with ``status=lookup_pending`` and
``final_lookup_outbox_version=1`` in one DynamoDB update.  This function turns
that durable transition into the final lookup SQS message.  A scheduled scan
replays any pending outbox rows whose stream event expired before delivery.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import boto3

JOBS_TABLE = os.environ["JOBS_TABLE"]
LOOKUP_QUEUE_URL = os.environ["LOOKUP_QUEUE_URL"]
OUTBOX_VERSION = 1

dynamodb = boto3.resource("dynamodb")
jobs_table = dynamodb.Table(JOBS_TABLE)
sqs = boto3.client("sqs")
logger = logging.getLogger(__name__)
tasks_table = dynamodb.Table(os.environ["SCAN_TASKS_TABLE"]) if os.environ.get("SCAN_TASKS_TABLE") else None
crops_table = dynamodb.Table(os.environ["CROPS_TABLE"]) if tasks_table else None
s3 = boto3.client("s3") if tasks_table else None



def _attribute_value(value: dict[str, Any] | None) -> Any:
    if not value:
        return None
    if "S" in value:
        return value["S"]
    if "N" in value:
        return int(value["N"])
    if "BOOL" in value:
        return bool(value["BOOL"])
    return None


def _image_value(image: dict[str, Any], name: str) -> Any:
    return _attribute_value(image.get(name))


def _is_initial_final_outbox_transition(record: dict[str, Any]) -> bool:
    if record.get("eventName") not in {"INSERT", "MODIFY"}:
        return False
    change = record.get("dynamodb") or {}
    new_image = change.get("NewImage") or {}
    old_image = change.get("OldImage") or {}
    return (
        _image_value(new_image, "status") == "lookup_pending"
        and _image_value(new_image, "final_lookup_outbox_version") == OUTBOX_VERSION
        and "final_lookup_outbox_version" not in old_image
        and bool(_image_value(new_image, "job_id"))
    )


def _job_id_from_record(record: dict[str, Any]) -> str:
    image = (record.get("dynamodb") or {}).get("NewImage") or {}
    return str(_image_value(image, "job_id"))


def _failure_identifier(record: dict[str, Any]) -> str:
    change = record.get("dynamodb") or {}
    return str(change.get("SequenceNumber") or record.get("eventID") or "")


def dispatch(job_id: str) -> None:
    body = json.dumps(
        {"job_id": job_id, "incremental": False, "outbox_version": OUTBOX_VERSION},
        separators=(",", ":"),
    )
    sqs.send_message(QueueUrl=LOOKUP_QUEUE_URL, MessageBody=body)


def _is_conditional_failure(error: Exception) -> bool:
    return getattr(error, "response", {}).get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def _as_int(item: dict[str, Any], name: str) -> int | None:
    value = item.get(name)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_counter_ready(item: dict[str, Any]) -> bool:
    processed = _as_int(item, "processed_frames")
    accepted = _as_int(item, "accepted_frames")
    ocr_done = _as_int(item, "ocr_done")
    ocr_total = _as_int(item, "ocr_total")
    return (
        processed is not None and accepted is not None and processed >= accepted
        and ocr_done is not None and ocr_total is not None and ocr_done >= ocr_total
    )


def _recovery_status(item: dict[str, Any]) -> str | None:
    """Return the expected status for a counter-to-intent recovery, if ready."""
    if item.get("final_lookup_outbox_version") is not None or item.get("final_lookup_queued") is not None:
        return None
    status = item.get("status")
    if status == "processing" and item.get("scan_closed") is True and _is_counter_ready(item):
        return "processing"
    if status == "ocr_pending":
        ocr_total = _as_int(item, "ocr_total")
        ocr_done = _as_int(item, "ocr_done")
        if ocr_total is not None and ocr_total > 0 and ocr_done is not None and ocr_done >= ocr_total:
            return "ocr_pending"
    return None


def _recover_counter_to_intent(item: dict[str, Any], expected_status: str) -> bool:
    if expected_status == "processing":
        readiness = (
            "scan_closed = :closed AND processed_frames >= accepted_frames "
            "AND ocr_done >= ocr_total"
        )
        readiness_values = {":closed": True}
    else:
        readiness = "ocr_total > :zero AND ocr_done >= ocr_total"
        readiness_values = {":zero": 0}
    try:
        jobs_table.update_item(
            Key={"job_id": item["job_id"]},
            UpdateExpression="SET final_lookup_queued = :yes, final_lookup_outbox_version = :version, #s = :pending",
            ConditionExpression=(
                "attribute_not_exists(final_lookup_outbox_version) "
                "AND attribute_not_exists(final_lookup_queued) "
                "AND #s = :expected AND " + readiness
            ),
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":yes": True,
                ":version": OUTBOX_VERSION,
                ":pending": "lookup_pending",
                ":expected": expected_status,
                **readiness_values,
            },
        )
        return True
    except Exception as error:
        if _is_conditional_failure(error):
            return False
        raise


def reconcile() -> int:
    """Replay outbox rows and recover counter-to-intent crashes across pages."""
    count = 0
    last_key = None
    while True:
        kwargs: dict[str, Any] = {}
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        page = jobs_table.scan(**kwargs)
        for item in page.get("Items", []):
            should_dispatch = item.get("status") == "lookup_pending" and item.get("final_lookup_outbox_version") == OUTBOX_VERSION
            recovery_status = _recovery_status(item) if not should_dispatch else None
            if recovery_status and _recover_counter_to_intent(item, recovery_status):
                should_dispatch = True
            if should_dispatch:
                dispatch(str(item["job_id"]))
                count += 1
        last_key = page.get("LastEvaluatedKey")
        if not last_key:
            break
    if tasks_table:
        reconcile_scan_tasks()
    logger.info("reconciled %d final lookup outbox rows", count)
    return count


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    records = event.get("Records") or []
    if not records:
        if event.get("source") != "aws.events" and event.get("detail-type") != "Scheduled Event":
            return {"batchItemFailures": []}
        reconcile()
        return {"batchItemFailures": []}

    failures = []
    for record in records:
        try:
            new_image = (record.get("dynamodb") or {}).get("NewImage") or {}
            if "task_id" in new_image:
                if record.get("eventName") == "REMOVE":
                    continue
                old_image = (record.get("dynamodb") or {}).get("OldImage") or {}
                if _image_value(new_image, "state") != _image_value(old_image, "state"):
                    dispatch_scan_task(_image_value(new_image, "job_id"), _image_value(new_image, "task_id"))
                continue
            if not _is_initial_final_outbox_transition(record):
                continue
            dispatch(_job_id_from_record(record))
        except Exception:
            logger.exception("final lookup dispatch failed for %s", record.get("eventID"))
            failures.append({"itemIdentifier": _failure_identifier(record)})
    return {"batchItemFailures": failures}


def dispatch_scan_task(job_id: str, task_id: str) -> None:
    """Deliver only work belonging to the committed detection attempt."""
    if not tasks_table:
        return
    task = tasks_table.get_item(Key={"job_id": job_id, "task_id": task_id}, ConsistentRead=True).get("Item") or {}
    job = jobs_table.get_item(Key={"job_id": job_id}, ConsistentRead=True).get("Item") or {}
    if job.get("status") not in {"pending", "collecting", "processing", "ocr_pending"}:
        return
    state = task.get("state")
    if state in {"pending", "prepared"}:
        if int(task.get("lease_until", 0)) > int(time.time()):
            return
        sqs.send_message(QueueUrl=os.environ["YOLO_QUEUE_URL"], MessageBody=json.dumps({
            "job_id": job_id, "task_id": task_id, "image_keys": task["image_keys"],
            "incremental": bool(task.get("incremental")),
        }))
    elif state == "done" and task.get("manifest_key"):
        ready_status = _recovery_status(job)
        if ready_status and _recover_counter_to_intent(job, ready_status):
            dispatch(job_id)
            return
        manifest = json.loads(s3.get_object(Bucket=os.environ["BUCKET"], Key=task["manifest_key"])["Body"].read())
        for entry in manifest.get("crops", []):
            if not entry.get("requires_ocr", entry.get("status") == "ocr_pending"):
                continue
            crop = crops_table.get_item(Key={"job_id": job_id, "crop_id": entry["crop_id"]}, ConsistentRead=True).get("Item") or {}
            if crop.get("status") != "ocr_pending" or crop.get("task_id") != task_id or crop.get("detection_token") != task.get("detection_token"):
                continue
            if int(crop.get("ocr_lease_until", 0)) > int(time.time()):
                continue
            sqs.send_message(QueueUrl=os.environ["OCR_QUEUE_URL"], MessageBody=json.dumps({
                "job_id": job_id, "crop_id": crop["crop_id"], "crop_key": crop["crop_key"],
                "task_id": task_id, "detection_token": task["detection_token"],
                "fingerprint_scope": crop.get("fingerprint_scope"), "phash": crop.get("phash"),
                "incremental": bool(task.get("incremental")),
            }))
        if task.get("incremental"):
            sqs.send_message(QueueUrl=LOOKUP_QUEUE_URL, MessageBody=json.dumps({"job_id": job_id, "incremental": True}))


def reconcile_scan_tasks() -> None:
    last_key = None
    while True:
        page = tasks_table.scan(**({"ExclusiveStartKey": last_key} if last_key else {}))
        for task in page.get("Items", []):
            if task.get("state") in {"pending", "prepared", "done"}:
                dispatch_scan_task(task["job_id"], task["task_id"])
        last_key = page.get("LastEvaluatedKey")
        if not last_key:
            return
