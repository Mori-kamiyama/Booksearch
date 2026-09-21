from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import boto3
import pytest
from moto import mock_aws


ROOT = Path(__file__).parents[1]


def load_durable():
    path = ROOT / "aws/functions/yolo_worker/durable.py"
    spec = importlib.util.spec_from_file_location("yolo_durable_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def worker(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        ddb = boto3.resource("dynamodb")
        jobs = ddb.create_table(
            TableName="jobs", KeySchema=[{"AttributeName": "job_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "job_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        tasks = ddb.create_table(
            TableName="tasks",
            KeySchema=[
                {"AttributeName": "job_id", "KeyType": "HASH"},
                {"AttributeName": "task_id", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "job_id", "AttributeType": "S"},
                {"AttributeName": "task_id", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        s3 = boto3.client("s3")
        s3.create_bucket(Bucket="assets")
        durable = load_durable()
        worker = SimpleNamespace(
            jobs_table=jobs,
            scan_tasks_table=tasks,
            s3=s3,
            BUCKET="assets",
            JOBS_TABLE="jobs",
            SCAN_TASKS_TABLE="tasks",
            VIDEO_EXTENSIONS=set(),
            process_frame=lambda *_args, **_kwargs: (
                [{
                    "job_id": "job-1", "crop_id": "crop-1", "crop_key": "crops/job-1/crop-1.jpg",
                    "status": "ocr_pending", "requires_ocr": True,
                }],
                {"image_key": "frame.jpg", "width": 100, "height": 100},
            ),
            ddb_safe=lambda value: value,
        )
        yield durable, worker


def put_job_and_task(worker, *, incremental=True, state="pending", **task_fields):
    jobs = worker.jobs_table
    tasks = worker.scan_tasks_table
    jobs.put_item(Item={
        "job_id": "job-1", "status": "processing" if incremental else "pending",
        "accepted_frames": 1, "processed_frames": 0, "crop_total": 0,
        "ocr_total": 0, "ocr_done": 0,
    })
    task = {
        "job_id": "job-1", "task_id": "task-1", "state": state,
        "image_keys": ["frame.jpg"], "incremental": incremental,
        **task_fields,
    }
    tasks.put_item(Item=task)


def test_pending_task_commits_manifest_and_counters(worker):
    durable, w = worker
    put_job_and_task(w)

    durable.process_task({"job_id": "job-1", "task_id": "task-1", "image_keys": ["frame.jpg"], "incremental": True}, w)

    task = w.scan_tasks_table.get_item(Key={"job_id": "job-1", "task_id": "task-1"})["Item"]
    job = w.jobs_table.get_item(Key={"job_id": "job-1"})["Item"]
    assert task["state"] == "done"
    assert task["detection_token"]
    assert task["manifest_key"].startswith("manifests/job-1/task-1/")
    assert job["processed_frames"] == 1
    assert job["crop_total"] == 1
    assert job["ocr_total"] == 1
    manifest = json.loads(w.s3.get_object(Bucket="assets", Key=task["manifest_key"])["Body"].read())
    assert manifest["detection_token"] == task["detection_token"]
    assert manifest["crops"][0]["requires_ocr"] is True


def test_prepared_task_reuses_manifest_without_detection(worker):
    durable, w = worker
    token = "existing-token"
    key = "manifests/job-1/task-1/existing-token.json"
    manifest = {
        "schema_version": 1, "job_id": "job-1", "task_id": "task-1",
        "detection_token": token, "crops": [], "frames": [],
        "crop_count": 0, "ocr_total": 0,
    }
    w.s3.put_object(Bucket="assets", Key=key, Body=json.dumps(manifest).encode())
    put_job_and_task(w, incremental=False, state="prepared", manifest_key=key,
                     detection_token=token, lease_until=0)
    w.process_frame = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must reuse manifest"))

    durable.process_task({"job_id": "job-1", "task_id": "task-1", "image_keys": ["frame.jpg"], "incremental": False}, w)

    task = w.scan_tasks_table.get_item(Key={"job_id": "job-1", "task_id": "task-1"})["Item"]
    job = w.jobs_table.get_item(Key={"job_id": "job-1"})["Item"]
    assert task["state"] == "done"
    assert task["detection_token"] == token
    assert job["status"] == "lookup_pending"
    assert job["final_lookup_outbox_version"] == 1


def test_unexpired_lease_is_busy_and_not_replaced(worker):
    durable, w = worker
    put_job_and_task(w, lease_token="owner", lease_until=9999999999)

    with pytest.raises(durable.TaskBusy):
        durable.process_task({"job_id": "job-1", "task_id": "task-1", "image_keys": ["frame.jpg"], "incremental": True}, w)

    task = w.scan_tasks_table.get_item(Key={"job_id": "job-1", "task_id": "task-1"})["Item"]
    assert task["lease_token"] == "owner"


def test_abandon_commits_zero_crop_manifest_and_job_failure_count(worker):
    durable, w = worker
    put_job_and_task(w, lease_token="owner", lease_until=0)

    assert durable.abandon_task(
        {"job_id": "job-1", "task_id": "task-1", "image_keys": ["frame.jpg"], "incremental": True},
        "detector failed", "owner", w,
    ) is True

    task = w.scan_tasks_table.get_item(Key={"job_id": "job-1", "task_id": "task-1"})["Item"]
    job = w.jobs_table.get_item(Key={"job_id": "job-1"})["Item"]
    assert task["state"] == "done"
    assert task["error"] == "detector failed"
    assert task["crop_count"] == 0
    assert job["processed_frames"] == 1
    assert job["failed_frames"] == 1
    manifest = json.loads(w.s3.get_object(Bucket="assets", Key=task["manifest_key"])["Body"].read())
    assert manifest["crops"] == []
    assert manifest["error"] == "detector failed"
