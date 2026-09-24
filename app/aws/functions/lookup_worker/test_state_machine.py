"""Lookup claims are safe under duplicate SQS delivery (no AWS calls)."""
import importlib.util
import os
import pathlib
import sys
import types
import unittest
from unittest.mock import patch

for key, value in {"BUCKET": "bucket", "JOBS_TABLE": "jobs", "CROPS_TABLE": "crops"}.items():
    os.environ.setdefault(key, value)


class ConditionalError(Exception):
    def __init__(self): self.response = {"Error": {"Code": "ConditionalCheckFailedException"}}


class Table:
    def update_item(self, **kwargs): self.last_update = kwargs


fake_boto3 = types.ModuleType("boto3")
fake_boto3.client = lambda *_args, **_kwargs: object()
fake_boto3.resource = lambda *_args, **_kwargs: types.SimpleNamespace(Table=lambda *_: Table())
fake_ddb = types.ModuleType("boto3.dynamodb")
fake_conditions = types.ModuleType("boto3.dynamodb.conditions")
fake_conditions.Key = lambda name: name
sys.modules.setdefault("boto3", fake_boto3)
sys.modules.setdefault("boto3.dynamodb", fake_ddb)
sys.modules.setdefault("boto3.dynamodb.conditions", fake_conditions)
exceptions = types.ModuleType("botocore.exceptions")
exceptions.ClientError = ConditionalError
sys.modules.setdefault("botocore", types.ModuleType("botocore"))
sys.modules.setdefault("botocore.exceptions", exceptions)

spec = importlib.util.spec_from_file_location("lookup_handler", pathlib.Path(__file__).with_name("handler.py"))
handler = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = handler
spec.loader.exec_module(handler)


class LookupStateMachineTests(unittest.TestCase):
    def test_claim_requires_pending_or_expired_lease(self):
        table = Table()
        with patch.object(handler, "jobs_table", table):
            token = handler.claim_lookup("job")
        self.assertTrue(token)
        self.assertIn("lookup_processing", table.last_update["ExpressionAttributeValues"].values())
        self.assertIn("lookup_lease_until < :now", table.last_update["ConditionExpression"])

    def test_duplicate_claim_is_skipped(self):
        class Claimed(Table):
            def update_item(self, **_kwargs): raise ConditionalError()
        with patch.object(handler, "jobs_table", Claimed()):
            self.assertIsNone(handler.claim_lookup("job"))

    def test_completion_cannot_overwrite_a_newer_worker(self):
        table = Table()
        fake_s3 = types.SimpleNamespace(put_object=lambda **_kwargs: None)
        with patch.object(handler, "jobs_table", table), patch.object(handler, "s3", fake_s3), \
             patch.object(handler, "build_catalog", return_value={"entries": []}), \
             patch.object(handler, "update_shelf_confidence", return_value=0):
            handler.process_job("job", "lease")
        self.assertIn("lookup_lease_token = :token", table.last_update["ConditionExpression"])
        self.assertEqual("done", table.last_update["ExpressionAttributeValues"][":s"])


if __name__ == "__main__":
    unittest.main()
