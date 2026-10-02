from __future__ import annotations

import importlib.util
import json
import sys
import time
import types
from pathlib import Path

import pytest


class FakeClientError(Exception):
    def __init__(self, code: str = "ConditionalCheckFailedException") -> None:
        self.response = {"Error": {"Code": code}}


class FakeTable:
    def __init__(self, item: dict | None = None) -> None:
        self.item = dict(item or {})
        self.calls: list[dict] = []

    def get_item(self, **_kwargs):
        return {"Item": dict(self.item)} if self.item else {}

    def update_item(self, **kwargs):
        self.calls.append(kwargs)
        return {}


class FakeS3:
    def __init__(self) -> None:
        self.puts: list[dict] = []

    def put_object(self, **kwargs):
        self.puts.append(kwargs)


def load_worker(monkeypatch):
    for key in ["BUCKET", "JOBS_TABLE", "CROPS_TABLE"]:
        monkeypatch.setenv(key, key.lower())
    fake_boto3 = types.ModuleType("boto3")
    fake_boto3.client = lambda _name: object()
    fake_boto3.resource = lambda _name: types.SimpleNamespace(Table=lambda _table: FakeTable())
    fake_conditions = types.ModuleType("boto3.dynamodb.conditions")
    fake_conditions.Key = lambda name: types.SimpleNamespace(eq=lambda value: (name, value))
    fake_dynamodb = types.ModuleType("boto3.dynamodb")
    fake_dynamodb.conditions = fake_conditions
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    monkeypatch.setitem(sys.modules, "boto3.dynamodb", fake_dynamodb)
    monkeypatch.setitem(sys.modules, "boto3.dynamodb.conditions", fake_conditions)
    exceptions = types.ModuleType("botocore.exceptions")
    exceptions.ClientError = FakeClientError
    monkeypatch.setitem(sys.modules, "botocore", types.ModuleType("botocore"))
    monkeypatch.setitem(sys.modules, "botocore.exceptions", exceptions)
    path = Path(__file__).parents[1] / "aws/functions/lookup_worker/handler.py"
    spec = importlib.util.spec_from_file_location("lookup_reliability_worker", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def test_final_duplicate_done_is_skipped(monkeypatch) -> None:
    worker = load_worker(monkeypatch)
    worker.jobs_table = FakeTable({"status": "done", "catalog_key": "catalogs/job/final/old.json"})
    worker.build_catalog = lambda _job_id: (_ for _ in ()).throw(AssertionError("must skip"))

    worker.process_job("job", incremental=False)

    assert worker.jobs_table.calls == []


def test_active_final_lease_is_retryable_without_failed_update(monkeypatch) -> None:
    worker = load_worker(monkeypatch)
    worker.jobs_table = FakeTable({"status": "lookup_pending", "lookup_lease_until": int(time.time()) + 120})

    with pytest.raises(worker.RetryableLeaseError):
        worker.handler({"Records": [{"body": json.dumps({"job_id": "job"})}]}, None)

    assert worker.jobs_table.calls == []


def test_expired_final_lease_can_be_reclaimed(monkeypatch) -> None:
    worker = load_worker(monkeypatch)
    worker.jobs_table = FakeTable({"status": "lookup_pending", "lookup_lease_until": int(time.time()) - 1})

    token = worker.claim_final_lookup("job")

    assert token
    assert worker.jobs_table.calls[0]["ConditionExpression"].startswith("#s = :pending")
    values = worker.jobs_table.calls[0]["ExpressionAttributeValues"]
    assert values[":pending"] == "lookup_pending"


def test_final_failure_releases_only_owned_lease(monkeypatch) -> None:
    worker = load_worker(monkeypatch)
    worker.jobs_table = FakeTable({"status": "lookup_pending"})
    worker.build_catalog = lambda _job_id: (_ for _ in ()).throw(RuntimeError("catalog failed"))

    with pytest.raises(RuntimeError, match="catalog failed"):
        worker.process_job("job", incremental=False)

    release = worker.jobs_table.calls[-1]
    assert "REMOVE lookup_claim_token, lookup_lease_until" in release["UpdateExpression"]
    assert "lookup_claim_token = :token" in release["ConditionExpression"]
    assert release["ExpressionAttributeValues"][":error"] == "catalog failed"


def test_late_incremental_does_not_publish_after_final_pending(monkeypatch) -> None:
    worker = load_worker(monkeypatch)
    worker.jobs_table = FakeTable({"status": "lookup_pending"})
    worker.build_catalog = lambda _job_id: (_ for _ in ()).throw(AssertionError("must skip"))
    worker.s3 = FakeS3()

    worker.process_job("job", incremental=True)

    assert worker.s3.puts == []
    assert worker.jobs_table.calls == []


def test_shelf_confidence_counts_sessions_even_for_old_crop_observations(monkeypatch) -> None:
    worker = load_worker(monkeypatch)
    class Observations:
        def scan(self, **_kwargs):
            return {"Items": [
                {"job_id": "old-job", "score": 0.7},
                {"job_id": "old-job", "score": 0.9},
                {"job_id": "new-job", "score": 0.8},
            ]}
    class Candidates:
        def __init__(self):
            self.item = None
        def put_item(self, **kwargs):
            self.item = kwargs["Item"]
    worker.shelf_observations_table = Observations()
    worker.shelf_candidates_table = Candidates()
    worker.refresh_shelf_candidate(42, "shelf-a", {"title": "Book"})
    result = worker.shelf_candidates_table.item
    assert result["observations"] == 2
    assert float(result["avg_score"]) == 0.85


def test_tagged_repeat_can_locate_untagged_ocr_result(monkeypatch) -> None:
    worker = load_worker(monkeypatch)
    observed = []
    monkeypatch.setattr(worker, "put_shelf_observation", lambda job, crop, shelf, candidate:
                        observed.append((job, crop, shelf)) or True)
    monkeypatch.setattr(worker, "refresh_shelf_candidate", lambda *_args: None)
    book = {"book_lookup": {"candidates": [{"library_db_id": 42, "score": 0.9}]}}
    catalog = {"job_id": "job", "entries": [
        {"crop_id": "original", "shelf_id": None, "books": [book]},
        {"crop_id": "repeat", "shelf_id": "shelf-a", "books": [book],
         "ocr_error": "skipped_duplicate_crop",
         "existing_ocr_ref": {"source": "session", "crop_id": "original"}},
    ]}
    assert worker.update_shelf_confidence(catalog) == 1
    assert observed == [("job", "repeat", "shelf-a")]


def test_final_publication_uses_unique_key_and_error_alias(monkeypatch) -> None:
    worker = load_worker(monkeypatch)
    worker.jobs_table = FakeTable({"status": "lookup_pending"})
    worker.s3 = FakeS3()
    worker.build_catalog = lambda _job_id: {"job_id": "job", "entries": []}
    worker.update_shelf_confidence = lambda _catalog: 0

    worker.process_job("job", incremental=False)

    assert len(worker.s3.puts) == 1
    assert "/final/" in worker.s3.puts[0]["Key"]
    publication = worker.jobs_table.calls[-1]
    assert "#e" in publication["UpdateExpression"]
    assert publication["ExpressionAttributeNames"]["#e"] == "error"
    assert publication["ExpressionAttributeValues"][":s"] == "done"
