"""Durable detection-task execution for the YOLO worker.

The legacy queue contract is intentionally kept in ``handler.py``.  Messages
that contain ``task_id`` use this module so that a frame is acknowledged only
after its manifest, task state, and JobsTable counters have been committed in
one DynamoDB transaction.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError


LEASE_SECONDS = 660


class TaskBusy(RuntimeError):
    """Another worker currently owns an unexpired task lease."""


class TaskExecutionError(RuntimeError):
    """A task failed after this invocation acquired a lease."""

    def __init__(self, message: str, *, lease_token: str, abandonable: bool) -> None:
        super().__init__(message)
        self.lease_token = lease_token
        self.abandonable = abandonable


def _now() -> int:
    return int(time.time())


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _client(worker: Any):
    return worker.scan_tasks_table.meta.client


def _task_key(job_id: str, task_id: str) -> dict[str, str]:
    return {"job_id": job_id, "task_id": task_id}


def _read_task(worker: Any, job_id: str, task_id: str) -> dict[str, Any]:
    out = worker.scan_tasks_table.get_item(
        Key=_task_key(job_id, task_id), ConsistentRead=True,
    )
    item = out.get("Item") or {}
    if not item:
        raise RuntimeError(f"scan task not found: {job_id}/{task_id}")
    return item


def _conditional(error: Exception) -> bool:
    return getattr(error, "response", {}).get("Error", {}).get("Code") in {
        "ConditionalCheckFailedException", "TransactionCanceledException",
    }


def _claim(worker: Any, task: dict[str, Any]) -> tuple[dict[str, Any], str]:
    job_id = str(task["job_id"])
    task_id = str(task["task_id"])
    state = str(task.get("state") or "pending")
    if state == "done":
        return task, ""
    if state not in {"pending", "prepared"}:
        raise TaskBusy(f"task is not claimable: {state}")

    now = _now()
    lease_token = uuid.uuid4().hex
    values: dict[str, Any] = {
        ":now": now,
        ":lease": now + LEASE_SECONDS,
        ":token": lease_token,
        ":state": state,
        ":updated": _iso_now(),
    }
    try:
        updated = worker.scan_tasks_table.update_item(
            Key=_task_key(job_id, task_id),
            UpdateExpression="SET lease_token = :token, lease_until = :lease, updated_at = :updated",
            ConditionExpression=(
                "#state = :state AND "
                "(attribute_not_exists(lease_until) OR lease_until < :now)"
            ),
            ExpressionAttributeNames={"#state": "state"},
            ExpressionAttributeValues=values,
            ReturnValues="ALL_NEW",
        )
    except ClientError as exc:
        if _conditional(exc):
            raise TaskBusy(f"task lease is still active: {job_id}/{task_id}") from exc
        raise
    return updated.get("Attributes", task), lease_token


def _safe_task_component(value: str) -> str:
    return "".join(char if char.isalnum() or char in "-_" else "_" for char in value)


def _manifest_key(job_id: str, task_id: str, token: str) -> str:
    return (
        f"manifests/{_safe_task_component(job_id)}/"
        f"{_safe_task_component(task_id)}/{token}.json"
    )


def _put_manifest(worker: Any, key: str, manifest: dict[str, Any]) -> None:
    worker.s3.put_object(
        Bucket=worker.BUCKET,
        Key=key,
        Body=json.dumps(manifest, ensure_ascii=False, separators=(",", ":"), default=str).encode(),
        ContentType="application/json",
    )


def _get_manifest(worker: Any, key: str) -> dict[str, Any]:
    obj = worker.s3.get_object(Bucket=worker.BUCKET, Key=key)
    body = obj["Body"].read()
    manifest = json.loads(body)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("crops"), list):
        raise RuntimeError(f"invalid detection manifest: {key}")
    return manifest


def _detect(worker: Any, body: dict[str, Any], task: dict[str, Any], token: str) -> dict[str, Any]:
    """Run existing detection helpers and return the immutable task manifest."""
    job_id = str(body["job_id"])
    task_id = str(body["task_id"])
    image_keys = [str(key) for key in (task.get("image_keys") or body.get("image_keys") or [])]
    if not image_keys:
        raise RuntimeError("task image_keys is empty")

    crops: list[dict[str, Any]] = []
    frames: list[dict[str, Any]] = []
    seen_hashes: list[tuple[int, str]] = []
    for index, image_key in enumerate(image_keys, 1):
        suffix = Path(image_key).suffix.lower()
        if suffix in worker.VIDEO_EXTENSIONS:
            video_path = f"/tmp/{job_id}_{task_id}_{index:04d}{suffix}"
            worker.s3.download_file(worker.BUCKET, image_key, video_path)
            frame_paths = worker.extract_video_frames(video_path, job_id)
            try:
                for frame_index, frame_path in enumerate(frame_paths, index):
                    records, diagnostics = worker.process_frame(
                        job_id, image_key, frame_index, seen_hashes, frame_path,
                        task_id=task_id, detection_token=token,
                    )
                    crops.extend(records)
                    frames.append(diagnostics)
            finally:
                Path(video_path).unlink(missing_ok=True)
                for frame_path in frame_paths:
                    Path(frame_path).unlink(missing_ok=True)
        else:
            records, diagnostics = worker.process_frame(
                job_id, image_key, index, seen_hashes,
                task_id=task_id, detection_token=token,
            )
            crops.extend(records)
            frames.append(diagnostics)

    ocr_crops = [crop for crop in crops if crop.get("requires_ocr")]
    skip_reason_counts: dict[str, int] = {}
    for crop in crops:
        for reason in (crop.get("quality") or {}).get("reasons", []):
            skip_reason_counts[reason] = skip_reason_counts.get(reason, 0) + 1
    diagnostics = {
        "frames": frames,
        "skip_reasons": skip_reason_counts,
        "readable_count": len(ocr_crops),
        "duplicate_count": sum(crop.get("status") == "skipped_duplicate_crop" for crop in crops),
        "total_crops": len(crops),
    }
    return {
        "schema_version": 1,
        "job_id": job_id,
        "task_id": task_id,
        "detection_token": token,
        "incremental": bool(body.get("incremental")),
        "frames": frames,
        "crops": crops,
        "crop_count": len(crops),
        "ocr_total": len(ocr_crops),
        "diagnostics": diagnostics,
    }


def _mark_prepared(worker: Any, task: dict[str, Any], lease_token: str,
                   manifest_key: str, detection_token: str) -> None:
    worker.scan_tasks_table.update_item(
        Key=_task_key(str(task["job_id"]), str(task["task_id"])),
        UpdateExpression=(
            "SET #state = :prepared, manifest_key = :manifest, "
            "detection_token = :detection, updated_at = :updated"
        ),
        ConditionExpression=(
            "#state = :pending AND lease_token = :lease "
            "AND lease_until >= :lease_now"
        ),
        ExpressionAttributeNames={"#state": "state"},
        ExpressionAttributeValues={
            ":prepared": "prepared", ":pending": "pending", ":lease": lease_token,
            ":manifest": manifest_key, ":detection": detection_token,
            ":lease_now": _now(), ":updated": _iso_now(),
        },
    )


def _job_update(worker: Any, body: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    job_id = str(body["job_id"])
    crop_count = int(manifest.get("crop_count", len(manifest.get("crops", []))))
    ocr_total = int(manifest.get("ocr_total", 0))
    diagnostics = manifest.get("diagnostics") or {"frames": manifest.get("frames", [])}
    if hasattr(worker, "ddb_safe"):
        diagnostics = worker.ddb_safe(diagnostics)
    frames = manifest.get("frames") or []
    first_frame = frames[0] if frames and isinstance(frames[0], dict) else {}
    values: dict[str, Any] = {":one": 1, ":crop": crop_count, ":ocr": ocr_total, ":now": _iso_now(), ":diag": diagnostics}
    dimensions = ""
    if first_frame.get("width") is not None and first_frame.get("height") is not None:
        values[":width"] = int(first_frame["width"])
        values[":height"] = int(first_frame["height"])
        dimensions = ", image_width = :width, image_height = :height"
    names = {"#state": "status"}
    incremental = bool(body.get("incremental"))
    if incremental:
        return {
            "TableName": worker.JOBS_TABLE,
            "Key": {"job_id": job_id},
            "UpdateExpression": f"SET updated_at = :now, latest_diagnostics = :diag{dimensions} ADD processed_frames :one, crop_total :crop, ocr_total :ocr",
            "ConditionExpression": "#state IN (:collecting, :processing)",
            "ExpressionAttributeNames": names,
            "ExpressionAttributeValues": {
                **values, ":collecting": "collecting", ":processing": "processing",
            },
        }

    values = {":one": 1, ":crop": crop_count, ":now": values[":now"], ":diag": diagnostics, ":zero": 0, ":pending": "pending"}
    if dimensions:
        values.update({":width": int(first_frame["width"]), ":height": int(first_frame["height"])})
    if ocr_total:
        names["#d"] = "diagnostics"
        return {
            "TableName": worker.JOBS_TABLE,
            "Key": {"job_id": job_id},
            "UpdateExpression": f"SET #state = :ocr_pending, #d = :diag, crop_total = :crop, ocr_total = :ocr, ocr_done = :zero, updated_at = :now{dimensions} ADD processed_frames :one",
            "ConditionExpression": "#state = :pending",
            "ExpressionAttributeNames": names,
            "ExpressionAttributeValues": {**values, ":ocr": ocr_total, ":ocr_pending": "ocr_pending"},
        }
    names["#d"] = "diagnostics"
    return {
        "TableName": worker.JOBS_TABLE,
        "Key": {"job_id": job_id},
        "UpdateExpression": f"SET #state = :lookup_pending, final_lookup_queued = :yes, final_lookup_outbox_version = :version, #d = :diag, crop_total = :crop, ocr_total = :zero, ocr_done = :zero, updated_at = :now{dimensions} ADD processed_frames :one",
        "ConditionExpression": "#state = :pending",
        "ExpressionAttributeNames": names,
        "ExpressionAttributeValues": {
            **values, ":lookup_pending": "lookup_pending", ":yes": True, ":version": 1,
        },
    }


def _commit(worker: Any, body: dict[str, Any], task: dict[str, Any], lease_token: str,
            manifest_key: str, detection_token: str, manifest: dict[str, Any]) -> None:
    crop_count = int(manifest.get("crop_count", len(manifest.get("crops", []))))
    ocr_total = int(manifest.get("ocr_total", 0))
    task_update = {
        "TableName": worker.SCAN_TASKS_TABLE,
        "Key": _task_key(str(task["job_id"]), str(task["task_id"])),
        "UpdateExpression": (
            "SET #state = :done, manifest_key = :manifest, detection_token = :detection, "
            "crop_count = :crop, ocr_total = :ocr, updated_at = :now "
            "REMOVE lease_token, lease_until"
        ),
        "ConditionExpression": (
            "#state = :prepared AND lease_token = :lease "
            "AND detection_token = :detection"
        ),
        "ExpressionAttributeNames": {"#state": "state"},
        "ExpressionAttributeValues": {
            ":done": "done", ":prepared": "prepared", ":lease": lease_token,
            ":manifest": manifest_key, ":detection": detection_token,
            ":crop": crop_count, ":ocr": ocr_total, ":now": _iso_now(),
        },
    }
    _client(worker).transact_write_items(
        TransactItems=[{"Update": task_update}, {"Update": _job_update(worker, body, manifest)}],
    )


def _terminal_job(status: Any) -> bool:
    return status in {"canceled", "done", "failed", "no_detection", "no_readable_crops", "lookup_pending"}


def _refresh_final_intent(worker: Any, body: dict[str, Any]) -> None:
    if not bool(body.get("incremental")):
        return
    helper = getattr(worker, "queue_final_lookup_if_ready", None)
    if not callable(helper):
        return
    item = worker.jobs_table.get_item(
        Key={"job_id": str(body["job_id"])}, ConsistentRead=True,
    ).get("Item") or {}
    helper(str(body["job_id"]), item)


def process_task(body: dict[str, Any], worker: Any | None = None) -> None:
    """Process a durable task; the caller must retry all raised exceptions."""
    if worker is None:
        import handler as worker  # type: ignore[no-redef]
    job_id = str(body["job_id"])
    task_id = str(body["task_id"])
    task = _read_task(worker, job_id, task_id)
    job = worker.jobs_table.get_item(Key={"job_id": job_id}, ConsistentRead=True).get("Item") or {}
    if _terminal_job(job.get("status")):
        return
    effective_body = {
        **body,
        "image_keys": list(task.get("image_keys") or body.get("image_keys") or []),
        "incremental": bool(task.get("incremental", body.get("incremental", False))),
    }
    if task.get("state") == "done":
        _refresh_final_intent(worker, effective_body)
        return
    task, lease_token = _claim(worker, task)
    if not lease_token:
        return
    try:
        if task.get("state") == "prepared" and task.get("manifest_key") and task.get("detection_token"):
            manifest_key = str(task["manifest_key"])
            detection_token = str(task["detection_token"])
            try:
                manifest = _get_manifest(worker, manifest_key)
            except Exception as exc:
                raise TaskExecutionError(str(exc), lease_token=lease_token, abandonable=False) from exc
        else:
            detection_token = uuid.uuid4().hex
            try:
                manifest = _detect(worker, effective_body, task, detection_token)
            except Exception as exc:
                raise TaskExecutionError(str(exc), lease_token=lease_token, abandonable=True) from exc
            manifest_key = _manifest_key(job_id, task_id, detection_token)
            try:
                _put_manifest(worker, manifest_key, manifest)
                _mark_prepared(worker, task, lease_token, manifest_key, detection_token)
            except Exception as exc:
                raise TaskExecutionError(str(exc), lease_token=lease_token, abandonable=False) from exc
        try:
            _commit(worker, effective_body, task, lease_token, manifest_key, detection_token, manifest)
        except Exception as exc:
            raise TaskExecutionError(str(exc), lease_token=lease_token, abandonable=False) from exc
        _refresh_final_intent(worker, effective_body)
    except TaskBusy:
        raise
    except TaskExecutionError:
        raise
    except Exception as exc:
        raise TaskExecutionError(str(exc), lease_token=lease_token, abandonable=False) from exc


def abandon_task(body: dict[str, Any], reason: str, lease_token: str,
                 worker: Any | None = None) -> bool:
    """Fence a permanently failed attempt and count it without OCR work."""
    if worker is None:
        import handler as worker  # type: ignore[no-redef]
    job_id = str(body["job_id"])
    task_id = str(body["task_id"])
    stored_task = _read_task(worker, job_id, task_id)
    incremental = bool(stored_task.get("incremental", body.get("incremental", False)))
    token = f"abandoned-{uuid.uuid4().hex}"
    manifest = {
        "schema_version": 1, "job_id": job_id, "task_id": task_id,
        "detection_token": token, "incremental": incremental,
        "frames": [], "crops": [], "crop_count": 0, "ocr_total": 0,
        "error": reason,
    }
    key = _manifest_key(job_id, task_id, token)
    _put_manifest(worker, key, manifest)
    task_update = {
        "TableName": worker.SCAN_TASKS_TABLE,
        "Key": _task_key(job_id, task_id),
        "UpdateExpression": (
            "SET #state = :done, manifest_key = :manifest, detection_token = :token, "
            "crop_count = :zero, ocr_total = :zero, #error = :error, updated_at = :now "
            "REMOVE lease_token, lease_until"
        ),
        "ConditionExpression": "#state IN (:pending, :prepared) AND lease_token = :lease",
        "ExpressionAttributeNames": {"#state": "state", "#error": "error"},
        "ExpressionAttributeValues": {
            ":done": "done", ":pending": "pending", ":prepared": "prepared",
            ":lease": lease_token, ":manifest": key, ":token": token,
            ":zero": 0, ":error": reason, ":now": _iso_now(),
        },
    }
    if incremental:
        job_update = {
            "TableName": worker.JOBS_TABLE,
            "Key": {"job_id": job_id},
            "UpdateExpression": "SET updated_at = :now ADD processed_frames :one, failed_frames :one",
            "ConditionExpression": "#state IN (:collecting, :processing)",
            "ExpressionAttributeNames": {"#state": "status"},
            "ExpressionAttributeValues": {
                ":now": _iso_now(), ":one": 1, ":collecting": "collecting", ":processing": "processing",
            },
        }
    else:
        job_update = {
            "TableName": worker.JOBS_TABLE,
            "Key": {"job_id": job_id},
            "UpdateExpression": "SET #state = :failed, #error = :error, updated_at = :now ADD processed_frames :one",
            "ConditionExpression": "#state IN (:pending, :ocr_pending)",
            "ExpressionAttributeNames": {"#state": "status", "#error": "error"},
            "ExpressionAttributeValues": {
                ":failed": "failed", ":error": reason, ":now": _iso_now(), ":one": 1,
                ":pending": "pending", ":ocr_pending": "ocr_pending",
            },
        }
    try:
        _client(worker).transact_write_items(TransactItems=[{"Update": task_update}, {"Update": job_update}])
        return True
    except ClientError as exc:
        if _conditional(exc):
            return False
        raise
