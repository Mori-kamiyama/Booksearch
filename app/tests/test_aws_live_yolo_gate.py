from __future__ import annotations

import importlib.util
import json
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


class RecordingJobsTable:
    """Records updates and hands back the resulting live-session counters."""

    def __init__(self, attributes=None) -> None:
        self.updates = []
        self.attributes = attributes or {}

    def update_item(self, **kwargs):
        self.updates.append(kwargs)
        return {"Attributes": self.attributes}


def test_live_frame_that_exhausts_retries_still_lets_the_session_finish() -> None:
    worker = load_worker()
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
    # The final lookup must still be queued, otherwise the job hangs forever.
    assert json.loads(worker.sqs.messages[0]["MessageBody"]) == {"job_id": "job-1", "incremental": False}


def test_live_frame_is_retried_while_sqs_attempts_remain() -> None:
    worker = load_worker()
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
