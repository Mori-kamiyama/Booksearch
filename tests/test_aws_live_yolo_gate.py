from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
import types


class FakeTable:
    def __init__(self) -> None:
        self.updates = []

    def update_item(self, **kwargs):
        self.updates.append(kwargs)
        return {}


class FakeResource:
    def Table(self, _name):
        return FakeTable()


class FakeSQS:
    def __init__(self) -> None:
        self.messages = []

    def send_message(self, **kwargs):
        self.messages.append(kwargs)


def load_worker():
    for key in ["BUCKET", "JOBS_TABLE", "CROPS_TABLE", "OCR_QUEUE_URL", "LOOKUP_QUEUE_URL"]:
        os.environ[key] = key.lower()
    fake_boto3 = types.ModuleType("boto3")
    fake_boto3.client = lambda name: FakeSQS() if name == "sqs" else object()
    fake_boto3.resource = lambda _name: FakeResource()
    sys.modules["boto3"] = fake_boto3
    fake_ultralytics = types.ModuleType("ultralytics")
    fake_ultralytics.YOLO = object
    sys.modules["ultralytics"] = fake_ultralytics
    path = Path(__file__).parents[1] / "aws/functions/yolo_worker/handler.py"
    spec = importlib.util.spec_from_file_location("live_yolo_handler", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_batch_queues_only_new_readable_crops() -> None:
    worker = load_worker()
    worker.jobs_table = FakeTable()
    worker.sqs = FakeSQS()

    def process_frame(_job, _key, index, _seen):
        status = "ocr_pending" if index == 1 else "skipped_duplicate_crop"
        return [{
            "crop_id": f"crop-{index}", "crop_key": f"crops/crop-{index}.jpg",
            "status": status, "quality": {"readable": True, "reasons": []},
            "fingerprint_scope": "shelf-A:1:1:1:1", "phash": "aa55aa55aa55aa55",
        }], {"image_key": _key, "width": 100, "height": 100, "apriltag": {}}

    worker.process_frame = process_frame
    worker.process_job("job-1", ["frame-1.jpg", "frame-2.jpg"])

    assert len(worker.sqs.messages) == 1
    assert "crop-1" in worker.sqs.messages[0]["MessageBody"]
    values = worker.jobs_table.updates[0]["ExpressionAttributeValues"]
    assert values[":n"] == 2
    assert values[":ot"] == 1
