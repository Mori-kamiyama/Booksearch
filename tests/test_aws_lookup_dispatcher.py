from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import types


class FakeTable:
    def __init__(self, pages: list[dict] | None = None) -> None:
        self.pages = pages or []
        self.calls: list[dict] = []
        self.update_calls: list[dict] = []

    def scan(self, **kwargs):
        self.calls.append(kwargs)
        return self.pages.pop(0) if self.pages else {"Items": []}

    def update_item(self, **kwargs):
        self.update_calls.append(kwargs)
        return {}


class FakeSQS:
    def __init__(self, fail=False) -> None:
        self.messages: list[dict] = []
        self.fail = fail

    def send_message(self, **kwargs):
        if self.fail:
            raise RuntimeError("sqs unavailable")
        self.messages.append(kwargs)


def load_dispatcher(monkeypatch, *, pages=None, fail=False):
    os.environ["JOBS_TABLE"] = "jobs"
    os.environ["LOOKUP_QUEUE_URL"] = "lookup-url"
    table = FakeTable(pages)
    sqs = FakeSQS(fail=fail)
    fake_boto3 = types.ModuleType("boto3")
    fake_boto3.resource = lambda _name: types.SimpleNamespace(Table=lambda _name: table)
    fake_boto3.client = lambda _name: sqs
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    path = Path(__file__).parents[1] / "aws/functions/lookup_dispatcher/handler.py"
    spec = importlib.util.spec_from_file_location("lookup_dispatcher", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module, table, sqs


def stream_record(*, event_name="MODIFY", old_version=None, status="lookup_pending", version=1, event_id="event-1", sequence="1"):
    old_image = {}
    if old_version is not None:
        old_image["final_lookup_outbox_version"] = {"N": str(old_version)}
    return {
        "eventID": event_id,
        "eventName": event_name,
        "dynamodb": {
            "SequenceNumber": sequence,
            "OldImage": old_image,
            "NewImage": {
                "job_id": {"S": "job-1"},
                "status": {"S": status},
                "final_lookup_outbox_version": {"N": str(version)},
            },
        },
    }


def test_dispatches_only_the_initial_version_one_transition(monkeypatch):
    dispatcher, _table, sqs = load_dispatcher(monkeypatch)
    event = {"Records": [
        stream_record(event_id="accepted"),
        stream_record(old_version=1, event_id="replay"),
        stream_record(event_name="REMOVE", event_id="removed"),
        stream_record(status="processing", event_id="unrelated"),
        stream_record(version=2, event_id="future"),
    ]}

    result = dispatcher.handler(event, None)

    assert result == {"batchItemFailures": []}
    assert len(sqs.messages) == 1
    assert json.loads(sqs.messages[0]["MessageBody"]) == {
        "job_id": "job-1", "incremental": False, "outbox_version": 1,
    }


def test_legacy_flag_without_version_is_not_migrated(monkeypatch):
    dispatcher, _table, sqs = load_dispatcher(monkeypatch)
    legacy = stream_record()
    legacy["dynamodb"]["NewImage"].pop("final_lookup_outbox_version")
    legacy["dynamodb"]["NewImage"]["final_lookup_queued"] = {"BOOL": True}

    assert dispatcher.handler({"Records": [legacy]}, None) == {"batchItemFailures": []}
    assert sqs.messages == []


def test_send_failure_reports_only_the_failed_stream_record(monkeypatch):
    dispatcher, _table, _sqs = load_dispatcher(monkeypatch, fail=True)
    result = dispatcher.handler({"Records": [stream_record(event_id="failed", sequence="42")]}, None)
    assert result == {"batchItemFailures": [{"itemIdentifier": "42"}]}


def test_reconciliation_scans_every_page_and_replays_matching_jobs(monkeypatch):
    dispatcher, table, sqs = load_dispatcher(monkeypatch, pages=[
        {"Items": [{"job_id": "job-1", "status": "lookup_pending", "final_lookup_outbox_version": 1}], "LastEvaluatedKey": {"job_id": "job-1"}},
        {"Items": [
            {"job_id": "job-2", "status": "lookup_pending", "final_lookup_outbox_version": 1},
            {"job_id": "done", "status": "done", "final_lookup_outbox_version": 1},
            {"job_id": "legacy", "status": "lookup_pending"},
        ]},
    ])

    result = dispatcher.handler({"source": "aws.events", "detail-type": "Scheduled Event"}, None)

    assert result == {"batchItemFailures": []}
    assert [json.loads(message["MessageBody"])["job_id"] for message in sqs.messages] == ["job-1", "job-2"]
    assert table.calls == [{}, {"ExclusiveStartKey": {"job_id": "job-1"}}]


def test_reconciliation_send_failure_is_retried_by_scheduled_invocation(monkeypatch):
    dispatcher, _table, _sqs = load_dispatcher(monkeypatch, pages=[{"Items": [{"job_id": "job-1", "status": "lookup_pending", "final_lookup_outbox_version": 1}]}], fail=True)
    try:
        dispatcher.handler({"source": "aws.events"}, None)
    except RuntimeError as exc:
        assert "sqs unavailable" in str(exc)
    else:
        raise AssertionError("scheduled reconciliation must fail when dispatch fails")


def test_reconciliation_recovers_ready_counter_to_intent(monkeypatch):
    dispatcher, table, sqs = load_dispatcher(monkeypatch, pages=[{"Items": [
        {
            "job_id": "closed-live", "status": "processing", "scan_closed": True,
            "accepted_frames": 3, "processed_frames": 3, "ocr_total": 4, "ocr_done": 4,
        },
        {"job_id": "batch", "status": "ocr_pending", "ocr_total": 2, "ocr_done": 2},
        {
            "job_id": "legacy", "status": "processing", "scan_closed": True,
            "accepted_frames": 1, "processed_frames": 1, "ocr_total": 1, "ocr_done": 1,
            "final_lookup_queued": True,
        },
        {
            "job_id": "open", "status": "processing", "scan_closed": False,
            "accepted_frames": 1, "processed_frames": 1, "ocr_total": 1, "ocr_done": 1,
        },
    ]}])

    result = dispatcher.handler({"source": "aws.events"}, None)

    assert result == {"batchItemFailures": []}
    assert [json.loads(message["MessageBody"])["job_id"] for message in sqs.messages] == ["closed-live", "batch"]
    assert [call["Key"]["job_id"] for call in table.update_calls] == ["closed-live", "batch"]
    assert all("attribute_not_exists(final_lookup_outbox_version)" in call["ConditionExpression"] for call in table.update_calls)
    assert all("attribute_not_exists(final_lookup_queued)" in call["ConditionExpression"] for call in table.update_calls)
