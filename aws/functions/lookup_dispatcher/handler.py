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
from typing import Any

import boto3

JOBS_TABLE = os.environ["JOBS_TABLE"]
LOOKUP_QUEUE_URL = os.environ["LOOKUP_QUEUE_URL"]
OUTBOX_VERSION = 1

dynamodb = boto3.resource("dynamodb")
jobs_table = dynamodb.Table(JOBS_TABLE)
sqs = boto3.client("sqs")
logger = logging.getLogger(__name__)


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
            if not _is_initial_final_outbox_transition(record):
                continue
            dispatch(_job_id_from_record(record))
        except Exception:
            logger.exception("final lookup dispatch failed for %s", record.get("eventID"))
            failures.append({"itemIdentifier": _failure_identifier(record)})
    return {"batchItemFailures": failures}
