from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import types


class FakeClientError(Exception):
    def __init__(self, code="unused"):
        self.response = {"Error": {"Code": code}}


class FakeTable:
    def __init__(self) -> None:
        self.updates = []

    def get_item(self, **kwargs):
        return {"Item": {"status": "pending"}}

    def update_item(self, **kwargs):
        self.updates.append(kwargs)
        return {}


class TerminalJobsTable(FakeTable):
    def __init__(self) -> None:
        super().__init__()
        self.attributes = {"status": "done", "final_lookup_outbox_version": 1}

    def update_item(self, **kwargs):
        self.updates.append(kwargs)
        if "attribute_not_exists(final_lookup_outbox_version)" in kwargs.get("ConditionExpression", ""):
            raise FakeClientError("ConditionalCheckFailedException")
        return {}

    def get_item(self, **_kwargs):
        return {"Item": self.attributes}


class FakeResource:
    def Table(self, _name):
        return FakeTable()


class FakeSQS:
    def __init__(self) -> None:
        self.messages = []

    def send_message(self, **kwargs):
        self.messages.append(kwargs)


def load_worker(monkeypatch):
    for key in ["BUCKET", "JOBS_TABLE", "CROPS_TABLE", "OCR_QUEUE_URL", "LOOKUP_QUEUE_URL"]:
        monkeypatch.setenv(key, key.lower())
    fake_boto3 = types.ModuleType("boto3")
    fake_boto3.client = lambda name: FakeSQS() if name == "sqs" else object()
    fake_boto3.resource = lambda _name: FakeResource()
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    fake_ultralytics = types.ModuleType("ultralytics")
    fake_ultralytics.YOLO = object
    monkeypatch.setitem(sys.modules, "ultralytics", fake_ultralytics)
    exceptions = types.ModuleType("botocore.exceptions")
    exceptions.ClientError = FakeClientError
    monkeypatch.setitem(sys.modules, "botocore", types.ModuleType("botocore"))
    monkeypatch.setitem(sys.modules, "botocore.exceptions", exceptions)
    path = Path(__file__).parents[1] / "aws/functions/yolo_worker/handler.py"
    spec = importlib.util.spec_from_file_location("live_yolo_handler", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def test_batch_queues_only_new_readable_crops(monkeypatch) -> None:
    worker = load_worker(monkeypatch)
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


def test_yolo_model_import_and_load_are_deferred(monkeypatch) -> None:
    monkeypatch.delenv("MPLCONFIGDIR", raising=False)
    monkeypatch.delenv("YOLO_CONFIG_DIR", raising=False)
    worker = load_worker(monkeypatch)

    assert worker._yolo_model is None
    assert worker.os.environ["MPLCONFIGDIR"] == "/tmp/matplotlib"
    assert worker.os.environ["YOLO_CONFIG_DIR"] == "/tmp/Ultralytics"

    loaded = []

    class FakeYOLO:
        def __init__(self, path):
            loaded.append(path)

    fake_ultralytics = types.ModuleType("ultralytics")
    fake_ultralytics.YOLO = FakeYOLO
    monkeypatch.setitem(sys.modules, "ultralytics", fake_ultralytics)
    first = worker.get_model()
    second = worker.get_model()
    assert isinstance(first, FakeYOLO)
    assert second is first
    assert loaded == [str(worker.MODEL_PATH)]


def test_quality_and_tag_helpers_still_load_optional_image_modules(monkeypatch) -> None:
    import numpy as np

    worker = load_worker(monkeypatch)
    image = np.zeros((100, 100, 3), dtype=np.uint8)
    quality = worker.assess_quality(image, (0, 0, 100, 100), (100, 100))
    assert quality["readable"] is False
    assert "blurry" in quality["reasons"]

    tags, diagnostics = worker.detect_tags(
        image, {"dictionary": "DICT_APRILTAG_36h11", "tags": {}},
    )
    assert tags == []
    assert diagnostics["raw_ids"] == []
    tags, diagnostics = worker.detect_tags(image, {"dictionary": "getPredefinedDictionary"})
    assert tags == []
    assert all(entry["dict"] in worker.ARUCO_DICTIONARIES for entry in diagnostics["tried"])


def test_duplicate_yolo_cannot_reopen_finalized_batch(monkeypatch) -> None:
    worker = load_worker(monkeypatch)
    worker.jobs_table = TerminalJobsTable()
    worker.sqs = FakeSQS()

    def process_frame(_job, _key, _index, _seen):
        return [{
            "crop_id": "crop-1", "crop_key": "crops/crop-1.jpg",
            "status": "ocr_pending", "quality": {"readable": True, "reasons": []},
        }], {"image_key": _key, "width": 100, "height": 100, "apriltag": {}}

    worker.process_frame = process_frame
    worker.process_job("job-1", ["frame-1.jpg"])

    assert worker.sqs.messages == []


class RecordingJobsTable:
    """Records updates and hands back the resulting live-session counters."""

    def __init__(self, attributes=None) -> None:
        self.updates = []
        self.attributes = attributes or {}

    def update_item(self, **kwargs):
        self.updates.append(kwargs)
        return {"Attributes": self.attributes}


def test_live_frame_that_exhausts_retries_still_lets_the_session_finish(monkeypatch) -> None:
    worker = load_worker(monkeypatch)
    worker.sqs = FakeSQS()
    # The abandoned frame is the last outstanding work of a closed session.
    worker.jobs_table = RecordingJobsTable({
        "scan_closed": True, "accepted_frames": 3, "processed_frames": 3,
        "ocr_total": 5, "ocr_done": 5,
    })
    worker.process_job = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("yolo oom"))

    worker.handler({"Records": [{
        "body": json.dumps({"job_id": "job-1", "image_keys": ["live/job-1/frames/a.jpg"], "incremental": True}),
        "attributes": {"ApproximateReceiveCount": str(worker.MAX_RECEIVE_COUNT)},
    }]}, None)

    counted = worker.jobs_table.updates[0]["ExpressionAttributeValues"]
    assert counted[":frame"] == {"live/job-1/frames/a.jpg"}
    assert counted[":one"] == 1
    # The final lookup intent is persisted for the stream dispatcher; no direct
    # SQS send remains in the worker.
    assert worker.sqs.messages == []
    final_values = worker.jobs_table.updates[-1]["ExpressionAttributeValues"]
    assert final_values[":yes"] is True
    assert final_values[":version"] == 1
    assert final_values[":pending"] == "lookup_pending"


def test_live_frame_is_retried_while_sqs_attempts_remain(monkeypatch) -> None:
    worker = load_worker(monkeypatch)
    worker.sqs = FakeSQS()
    worker.jobs_table = RecordingJobsTable()
    worker.process_job = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("transient"))

    try:
        worker.handler({"Records": [{
            "body": json.dumps({"job_id": "job-1", "image_keys": ["live/job-1/frames/a.jpg"], "incremental": True}),
            "attributes": {"ApproximateReceiveCount": "1"},
        }]}, None)
    except RuntimeError:
        pass
    else:
        raise AssertionError("the message must go back to SQS while retries remain")

    # A retryable frame must not be counted as processed, and the session must
    # not be marked failed.
    assert worker.jobs_table.updates == []
