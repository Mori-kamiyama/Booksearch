from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
import types


class FakeClientError(Exception):
    def __init__(self):
        self.response = {"Error": {"Code": "unused"}}


class FakeTable:
    def __init__(self, attributes=None) -> None:
        self.calls = []
        self.attributes = attributes or {}

    def update_item(self, **kwargs):
        self.calls.append(kwargs)
        return {"Attributes": self.attributes}


class FakeBody:
    def read(self):
        return b"jpeg"


class FakeS3:
    def get_object(self, **_kwargs):
        return {"Body": FakeBody()}


class FakeSQS:
    def __init__(self) -> None:
        self.messages = []

    def send_message(self, **kwargs):
        self.messages.append(kwargs)


def load_worker(monkeypatch):
    for key in ["BUCKET", "JOBS_TABLE", "CROPS_TABLE", "LOOKUP_QUEUE_URL", "SECRET_GEMINI_ARN"]:
        monkeypatch.setenv(key, key.lower())
    fake_boto3 = types.ModuleType("boto3")
    fake_boto3.client = lambda _name: object()
    fake_boto3.resource = lambda _name: types.SimpleNamespace(Table=lambda _table: FakeTable())
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    exceptions = types.ModuleType("botocore.exceptions")
    exceptions.ClientError = FakeClientError
    monkeypatch.setitem(sys.modules, "botocore", types.ModuleType("botocore"))
    monkeypatch.setitem(sys.modules, "botocore.exceptions", exceptions)
    path = Path(__file__).parents[1] / "aws/functions/ocr_worker/handler.py"
    spec = importlib.util.spec_from_file_location("live_ocr_handler", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def test_ocr_failure_is_recorded_and_job_continues_to_lookup(monkeypatch) -> None:
    worker = load_worker(monkeypatch)
    worker.s3 = FakeS3()
    worker.sqs = FakeSQS()
    worker.crops_table = FakeTable()
    worker.jobs_table = FakeTable({"ocr_done": 1, "ocr_total": 1})
    worker.fingerprints_table = None
    worker.gemini_ocr = lambda *_args: (_ for _ in ()).throw(RuntimeError("gemini unavailable"))

    worker.process_one("job-1", "crop-1", "crops/crop-1.jpg")

    crop_values = worker.crops_table.calls[0]["ExpressionAttributeValues"]
    assert crop_values[":e"] == "gemini unavailable"
    assert crop_values[":s"] == "ocr_done"
    assert worker.sqs.messages == []
    final_values = worker.jobs_table.calls[-1]["ExpressionAttributeValues"]
    assert final_values[":yes"] is True
    assert final_values[":version"] == 1
    assert final_values[":pending"] == "lookup_pending"


class FailingS3:
    def get_object(self, **_kwargs):
        raise RuntimeError("crop object is gone")


def test_incremental_final_lookup_persists_outbox_intent(monkeypatch) -> None:
    worker = load_worker(monkeypatch)
    worker.sqs = FakeSQS()
    worker.jobs_table = FakeTable()

    worker.queue_final_lookup_if_ready("job-1", {
        "scan_closed": True,
        "status": "processing",
        "processed_frames": 2,
        "accepted_frames": 2,
        "ocr_done": 3,
        "ocr_total": 3,
    })

    assert worker.sqs.messages == []
    values = worker.jobs_table.calls[-1]["ExpressionAttributeValues"]
    assert values[":yes"] is True
    assert values[":version"] == 1
    assert values[":pending"] == "lookup_pending"
    assert values[":expected"] == "processing"


def test_final_lookup_outbox_does_not_reopen_terminal_status(monkeypatch) -> None:
    worker = load_worker(monkeypatch)
    worker.sqs = FakeSQS()
    for status in ("done", "canceled"):
        worker.jobs_table = FakeTable()
        worker.queue_final_lookup_if_ready("job-1", {
            "scan_closed": True,
            "status": status,
            "processed_frames": 2,
            "accepted_frames": 2,
            "ocr_done": 3,
            "ocr_total": 3,
        })
        values = worker.jobs_table.calls[-1]["ExpressionAttributeValues"]
        assert values[":expected"] == "processing"


def test_crop_that_exhausts_retries_still_lets_the_job_finish(monkeypatch) -> None:
    import json

    worker = load_worker(monkeypatch)
    worker.s3 = FailingS3()
    worker.sqs = FakeSQS()
    worker.crops_table = FakeTable()
    worker.jobs_table = FakeTable({"ocr_done": 2, "ocr_total": 2})
    worker.fingerprints_table = None

    worker.handler({"Records": [{
        "body": json.dumps({"job_id": "job-1", "crop_id": "crop-1", "crop_key": "crops/crop-1.jpg"}),
        "attributes": {"ApproximateReceiveCount": str(worker.MAX_RECEIVE_COUNT)},
    }]}, None)

    crop_values = worker.crops_table.calls[0]["ExpressionAttributeValues"]
    assert crop_values[":s"] == "ocr_done"
    assert "crop object is gone" in crop_values[":e"]
    # ocr_done reached ocr_total, so lookup must be queued instead of hanging.
    assert worker.sqs.messages == []
    final_values = worker.jobs_table.calls[-1]["ExpressionAttributeValues"]
    assert final_values[":yes"] is True
    assert final_values[":version"] == 1
    assert final_values[":pending"] == "lookup_pending"


def test_crop_is_retried_while_sqs_attempts_remain(monkeypatch) -> None:
    import json

    worker = load_worker(monkeypatch)
    worker.s3 = FailingS3()
    worker.sqs = FakeSQS()
    worker.crops_table = FakeTable()
    worker.jobs_table = FakeTable({"ocr_done": 1, "ocr_total": 2})
    worker.fingerprints_table = None

    try:
        worker.handler({"Records": [{
            "body": json.dumps({"job_id": "job-1", "crop_id": "crop-1", "crop_key": "crops/crop-1.jpg"}),
            "attributes": {"ApproximateReceiveCount": "1"},
        }]}, None)
    except RuntimeError:
        pass
    else:
        raise AssertionError("the message must go back to SQS while retries remain")

    assert worker.crops_table.calls == []
    assert worker.sqs.messages == []
