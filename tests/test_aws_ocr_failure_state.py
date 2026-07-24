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


def load_worker():
    for key in ["BUCKET", "JOBS_TABLE", "CROPS_TABLE", "LOOKUP_QUEUE_URL", "SECRET_GEMINI_ARN"]:
        os.environ[key] = key.lower()
    fake_boto3 = types.ModuleType("boto3")
    fake_boto3.client = lambda _name: object()
    fake_boto3.resource = lambda _name: types.SimpleNamespace(Table=lambda _table: FakeTable())
    sys.modules["boto3"] = fake_boto3
    exceptions = types.ModuleType("botocore.exceptions")
    exceptions.ClientError = FakeClientError
    sys.modules["botocore"] = types.ModuleType("botocore")
    sys.modules["botocore.exceptions"] = exceptions
    path = Path(__file__).parents[1] / "aws/functions/ocr_worker/handler.py"
    spec = importlib.util.spec_from_file_location("live_ocr_handler", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_ocr_failure_is_recorded_and_job_continues_to_lookup() -> None:
    worker = load_worker()
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
    assert len(worker.sqs.messages) == 1
    assert worker.jobs_table.calls[-1]["ExpressionAttributeValues"][":s"] == "lookup_pending"
