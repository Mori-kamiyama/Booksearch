"""Unit tests for OCR's durable state transitions (no AWS or Gemini calls)."""
import importlib.util
import os
import pathlib
import sys
import types
import unittest
from unittest.mock import patch

for key, value in {
    "BUCKET": "test-bucket", "JOBS_TABLE": "jobs", "CROPS_TABLE": "crops",
    "LOOKUP_QUEUE_URL": "https://example.invalid/lookup", "OUTBOX_TABLE": "outbox",
    "SECRET_GEMINI_ARN": "arn:test", "AWS_EC2_METADATA_DISABLED": "true",
}.items():
    os.environ.setdefault(key, value)


class ConditionalError(Exception):
    def __init__(self, code="ConditionalCheckFailedException"):
        self.response = {"Error": {"Code": code}}


class EmptyTable:
    def update_item(self, **_kwargs):
        return {}
    def get_item(self, **_kwargs):
        return {"Item": {"ocr_done": 1, "ocr_total": 1}}


fake_boto3 = types.ModuleType("boto3")
fake_boto3.client = lambda *_args, **_kwargs: types.SimpleNamespace(transact_write_items=lambda **_kwargs: {})
fake_boto3.resource = lambda *_args, **_kwargs: types.SimpleNamespace(Table=lambda *_: EmptyTable())
sys.modules.setdefault("boto3", fake_boto3)
fake_exceptions = types.ModuleType("botocore.exceptions")
fake_exceptions.ClientError = ConditionalError
sys.modules.setdefault("botocore", types.ModuleType("botocore"))
sys.modules.setdefault("botocore.exceptions", fake_exceptions)

spec = importlib.util.spec_from_file_location("ocr_handler", pathlib.Path(__file__).with_name("handler.py"))
handler = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = handler
spec.loader.exec_module(handler)


class OcrDurabilityTests(unittest.TestCase):
    def test_duplicate_claim_does_not_run_ocr(self):
        with patch.object(handler, "claim_crop", return_value=None), patch.object(handler, "gemini_ocr") as gemini:
            handler.process_one("job", "crop", "crops/job/crop.jpg")
        gemini.assert_not_called()

    def test_gemini_failure_releases_claim_and_is_retried(self):
        body = types.SimpleNamespace(read=lambda: b"image")
        with patch.object(handler, "claim_crop", return_value="lease"), \
             patch.object(handler, "s3", types.SimpleNamespace(get_object=lambda **_kwargs: {"Body": body})), \
             patch.object(handler, "gemini_ocr", side_effect=RuntimeError("429")), \
             patch.object(handler, "release_crop") as release:
            with self.assertRaisesRegex(RuntimeError, "429"):
                handler.process_one("job", "crop", "crops/job/crop.jpg")
        release.assert_called_once_with("job", "crop", "lease")

    def test_crop_done_and_counter_increment_are_one_transaction(self):
        client = types.SimpleNamespace(transact_write_items=lambda **kwargs: setattr(client, "items", kwargs["TransactItems"]))
        with patch.object(handler, "ddb_client", client):
            handler.complete_crop("job", "crop", "lease", [{"title": "Title"}])
        self.assertEqual(2, len(client.items))
        self.assertIn("ocr_done", client.items[1]["Update"]["UpdateExpression"])
        self.assertIn("ocr_lease_token", client.items[0]["Update"]["ConditionExpression"])

    def test_last_crop_creates_lookup_outbox_in_same_transaction_as_state(self):
        client = types.SimpleNamespace(transact_write_items=lambda **kwargs: setattr(client, "items", kwargs["TransactItems"]))
        with patch.object(handler, "ddb_client", client):
            handler.queue_lookup_outbox("job")
        self.assertEqual(2, len(client.items))
        self.assertIn("lookup_pending", str(client.items[0]))
        self.assertEqual("lookup", client.items[1]["Put"]["Item"]["event_type"]["S"])


if __name__ == "__main__":
    unittest.main()
