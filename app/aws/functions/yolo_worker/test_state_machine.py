"""State-machine tests without loading a model or contacting AWS."""
import importlib.util
import os
import pathlib
import sys
import types
import unittest
from unittest.mock import patch

for key, value in {"BUCKET": "bucket", "JOBS_TABLE": "jobs", "CROPS_TABLE": "crops", "OUTBOX_TABLE": "outbox"}.items():
    os.environ.setdefault(key, value)


class ConditionalError(Exception):
    def __init__(self): self.response = {"Error": {"Code": "ConditionalCheckFailedException"}}


class Table:
    def update_item(self, **kwargs): self.last_update = kwargs
    def put_item(self, **kwargs): self.last_put = kwargs


fake_boto3 = types.ModuleType("boto3")
fake_boto3.client = lambda *_args, **_kwargs: object()
fake_boto3.resource = lambda *_args, **_kwargs: types.SimpleNamespace(Table=lambda *_: Table())
sys.modules.setdefault("boto3", fake_boto3)
exceptions = types.ModuleType("botocore.exceptions")
exceptions.ClientError = ConditionalError
sys.modules.setdefault("botocore", types.ModuleType("botocore"))
sys.modules.setdefault("botocore.exceptions", exceptions)
aruco = types.SimpleNamespace(DICT_4X4_50=1, DICT_4X4_100=2, DICT_5X5_100=3, DICT_6X6_250=4,
                              DICT_APRILTAG_16h5=5, DICT_APRILTAG_25h9=6, DICT_APRILTAG_36h10=7,
                              DICT_APRILTAG_36h11=8)
sys.modules.setdefault("cv2", types.SimpleNamespace(aruco=aruco))
sys.modules.setdefault("numpy", types.ModuleType("numpy"))
ultralytics = types.ModuleType("ultralytics")
ultralytics.YOLO = object
sys.modules.setdefault("ultralytics", ultralytics)

spec = importlib.util.spec_from_file_location("yolo_handler", pathlib.Path(__file__).with_name("handler.py"))
handler = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = handler
spec.loader.exec_module(handler)


class YoloStateMachineTests(unittest.TestCase):
    def test_claim_requires_pending_or_expired_processing_lease(self):
        table = Table()
        with patch.object(handler, "jobs_table", table):
            token = handler.claim_yolo("job")
        self.assertTrue(token)
        self.assertIn("yolo_processing", table.last_update["ExpressionAttributeValues"].values())
        self.assertIn("yolo_lease_until < :now", table.last_update["ConditionExpression"])

    def test_duplicate_claim_is_skipped(self):
        class Claimed(Table):
            def update_item(self, **_kwargs): raise ConditionalError()
        with patch.object(handler, "jobs_table", Claimed()):
            self.assertIsNone(handler.claim_yolo("job"))

    def test_ocr_outbox_key_is_deterministic(self):
        table = Table()
        with patch.object(handler, "outbox_table", table):
            handler.put_ocr_outbox("job", {"crop_id": "crop", "crop_key": "crops/job/crop.jpg"})
        self.assertEqual("ocr:crop", table.last_put["Item"]["event_type"])
        self.assertEqual("attribute_not_exists(job_id)", table.last_put["ConditionExpression"])


if __name__ == "__main__":
    unittest.main()
