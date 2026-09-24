import importlib.util
import json
import os
import pathlib
import sys
import types
import unittest
from unittest.mock import patch

os.environ.setdefault("OUTBOX_TABLE", "test-outbox")
os.environ.setdefault("JOBS_TABLE", "test-jobs")
os.environ.setdefault("CROPS_TABLE", "test-crops")
os.environ.setdefault("YOLO_QUEUE_URL", "https://example.invalid/yolo")
os.environ.setdefault("OCR_QUEUE_URL", "https://example.invalid/ocr")
os.environ.setdefault("LOOKUP_QUEUE_URL", "https://example.invalid/lookup")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-northeast-1")

# Unit tests deliberately do not install or contact the AWS SDK.
fake_boto3 = types.ModuleType("boto3")
fake_boto3.resource = lambda *_args, **_kwargs: types.SimpleNamespace(Table=lambda *_: types.SimpleNamespace(scan=lambda **_kwargs: {"Items": []}))
fake_boto3.client = lambda *_args, **_kwargs: object()
sys.modules.setdefault("boto3", fake_boto3)
fake_botocore = types.ModuleType("botocore.exceptions")
fake_botocore.ClientError = Exception
sys.modules.setdefault("botocore", types.ModuleType("botocore"))
sys.modules.setdefault("botocore.exceptions", fake_botocore)

path = pathlib.Path(__file__).with_name("handler.py")
spec = importlib.util.spec_from_file_location("outbox_handler", path)
handler = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(handler)


class FakeQueue:
    def __init__(self): self.messages = []
    def send_message(self, **kwargs): self.messages.append(kwargs)


class FakeOutbox:
    def __init__(self): self.updated, self.puts = [], []
    def update_item(self, **kwargs): self.updated.append(kwargs)
    def put_item(self, **kwargs): self.puts.append(kwargs)

class RecoveringOutbox(FakeOutbox):
    def scan(self, **_kwargs):
        return {"Items": [{"job_id": "job-lookup", "event_type": "lookup"}]}


class OutboxDispatcherTests(unittest.TestCase):
    def test_stream_record_sends_then_marks_delivery(self):
        queue, outbox = FakeQueue(), FakeOutbox()
        event = {"Records": [{"eventID": "e1", "eventName": "INSERT", "dynamodb": {"NewImage": {
            "job_id": {"S": "job-1"}, "event_type": {"S": "yolo"}, "image_key": {"S": "uploads/job-1/a.jpg"},
        }}}]}
        with patch.object(handler, "sqs", queue), patch.object(handler, "ddb", outbox):
            result = handler.handler(event, None)
        self.assertEqual([], result["batchItemFailures"])
        self.assertEqual(1, len(queue.messages))
        self.assertEqual("job-1", outbox.updated[0]["Key"]["job_id"])

    def test_send_failure_requests_stream_retry(self):
        event = {"Records": [{"eventID": "e1", "eventName": "INSERT", "dynamodb": {"NewImage": {
            "job_id": {"S": "job-1"}, "event_type": {"S": "yolo"}, "image_key": {"S": "uploads/job-1/a.jpg"},
        }}}]}
        queue = FakeQueue()
        with patch.object(queue, "send_message", side_effect=RuntimeError("queue down")), patch.object(handler, "sqs", queue):
            result = handler.handler(event, None)
        self.assertEqual([{"itemIdentifier": "e1"}], result["batchItemFailures"])

    def test_lookup_record_uses_lookup_queue(self):
        queue, outbox = FakeQueue(), FakeOutbox()
        event = {"Records": [{"eventID": "e2", "eventName": "INSERT", "dynamodb": {"NewImage": {
            "job_id": {"S": "job-lookup"}, "event_type": {"S": "lookup"},
        }}}]}
        with patch.object(handler, "sqs", queue), patch.object(handler, "ddb", outbox):
            handler.handler(event, None)
        self.assertEqual(handler.LOOKUP_QUEUE_URL, queue.messages[0]["QueueUrl"])

    def test_ocr_record_uses_ocr_queue_and_keeps_crop_identity(self):
        queue, outbox = FakeQueue(), FakeOutbox()
        event = {"Records": [{"eventID": "e3", "eventName": "INSERT", "dynamodb": {"NewImage": {
            "job_id": {"S": "job-ocr"}, "event_type": {"S": "ocr:crop-1"},
            "crop_id": {"S": "crop-1"}, "crop_key": {"S": "crops/job-ocr/crop-1.jpg"},
        }}}]}
        with patch.object(handler, "sqs", queue), patch.object(handler, "ddb", outbox):
            handler.handler(event, None)
        self.assertEqual(handler.OCR_QUEUE_URL, queue.messages[0]["QueueUrl"])
        self.assertEqual({"job_id": "job-ocr", "crop_id": "crop-1", "crop_key": "crops/job-ocr/crop-1.jpg"}, json.loads(queue.messages[0]["MessageBody"]))

    def test_scheduled_recovery_redelivers_undelivered_record(self):
        queue, outbox = FakeQueue(), RecoveringOutbox()
        with patch.object(handler, "sqs", queue), patch.object(handler, "ddb", outbox):
            handler.handler({"source": "aws.events", "Records": []}, None)
        self.assertEqual(handler.LOOKUP_QUEUE_URL, queue.messages[0]["QueueUrl"])

    def test_recovery_creates_missing_ocr_outbox_record(self):
        outbox = FakeOutbox()
        crop_table = types.SimpleNamespace(query=lambda **_kwargs: {"Items": [{
            "crop_id": "crop-1", "crop_key": "crops/job-1/crop-1.jpg", "status": "ocr_pending",
        }]})
        with patch.object(handler, "ddb", outbox), patch.object(handler, "crops", crop_table):
            handler.repair_ocr_intents({"job_id": "job-1", "status": "ocr_pending"})
        self.assertEqual("ocr:crop-1", outbox.puts[0]["Item"]["event_type"])


if __name__ == "__main__":
    unittest.main()
