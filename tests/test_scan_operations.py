from __future__ import annotations

import importlib.util
import json
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path

import pytest
from botocore.exceptions import ClientError


ROOT = Path(__file__).parents[1]


def load_module():
    path = ROOT / "aws/scripts/diagnose_scan.py"
    spec = importlib.util.spec_from_file_location("scan_operations", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeTable:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def scan(self, **kwargs):
        self.calls.append(kwargs)
        page = self.pages.pop(0)
        page.setdefault("ConsumedCapacity", {"CapacityUnits": 0.5})
        return page


class ErrorTable:
    def __init__(self, code):
        self.error = ClientError({"Error": {"Code": code, "Message": code}}, "Scan")

    def scan(self, **_kwargs):
        raise self.error


class FakeSQS:
    def get_queue_url(self, QueueName):
        return {"QueueUrl": f"https://queue.example/{QueueName}"}

    def get_queue_attributes(self, **_kwargs):
        return {
            "Attributes": {
                "QueueArn": "arn:aws:sqs:us-east-1:123456789012:booksearch-yolo-queue",
                "ApproximateNumberOfMessages": "4",
                "ApproximateNumberOfMessagesNotVisible": "2",
                "ApproximateNumberOfMessagesDelayed": "1",
                "RedrivePolicy": json.dumps({"deadLetterTargetArn": "arn:aws:sqs:us-east-1:123456789012:booksearch-yolo-dlq"}),
            }
        }


class FakeCloudWatch:
    def get_metric_statistics(self, **_kwargs):
        return {"Datapoints": [{"Maximum": 321}]}


class FakeSession:
    def __init__(self, tables):
        self.tables = tables

    def resource(self, name):
        assert name == "dynamodb"
        return self

    def Table(self, name):
        return self.tables[name]

    def client(self, name):
        return FakeSQS() if name == "sqs" else FakeCloudWatch()


def test_scan_is_paginated_and_reports_aggregate_lease_status():
    module = load_module()
    now = datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc)
    jobs = FakeTable([
        {"Items": [{"job_id": "secret-job", "status": "processing", "updated_at": "2026-09-20T23:00:00Z", "lookup_lease_until": 1}], "LastEvaluatedKey": {"job_id": "page-1"}},
        {"Items": [{"job_id": "job-2", "status": "done", "updated_at": "2026-09-20T23:59:00Z"}]},
    ])
    tasks = FakeTable([
        {"Items": [{"job_id": "secret-job", "task_id": "task-1", "state": "prepared", "lease_until": 1}]},
    ])
    args = Namespace(
        jobs_table="jobs", tasks_table="tasks", region=None,
        queues=[("yolo", "https://queue.example/yolo")],
        dlqs=[],
    )

    report = module.run(args, session_factory=lambda: FakeSession({"jobs": jobs, "tasks": tasks}), now=now)

    assert report["tables"]["jobs"]["scan_pages"] == 2
    assert report["tables"]["jobs"]["item_count"] == 2
    assert report["tables"]["jobs"]["status_counts"] == {"done": 1, "processing": 1}
    assert report["tables"]["jobs"]["leases"]["lookup_lease_until"]["expired"] == 1
    assert report["tables"]["scan_tasks"]["leases"]["lease_until"]["expired"] == 1
    expected_scan = {
        "ProjectionExpression": module.SCAN_PROJECTION,
        "ExpressionAttributeNames": module.SCAN_ATTRIBUTE_NAMES,
        "ReturnConsumedCapacity": "TOTAL",
    }
    assert jobs.calls == [
        expected_scan,
        {**expected_scan, "ExclusiveStartKey": {"job_id": "page-1"}},
    ]
    assert report["tables"]["jobs"]["consumed_read_capacity"] == 1.0
    assert report["tables"]["jobs"]["elapsed_seconds"] >= 0
    assert report["queues"][0]["visible"] == 4
    assert report["queues"][0]["oldest_age_seconds"] == 321
    assert report["queues"][0]["dlq_name"] == "booksearch-yolo-dlq"
    assert report["dlqs"][0]["label"] == "yolo-dlq"
    assert "secret-job" not in json.dumps(report)
    assert "https://queue.example/yolo" not in json.dumps(report)


def test_summary_handles_invalid_timestamps_without_exposing_items():
    module = load_module()
    result = module.summarize_items([
        {"job_id": "secret", "status": "processing", "updated_at": "not-a-time", "lease_until": "bad"},
    ], datetime(2026, 9, 21, tzinfo=timezone.utc))

    assert result["item_count"] == 1
    assert result["oldest_updated_age_seconds"] is None
    assert result["leases"]["lease_until"] == {"active": 0, "expired": 0}
    assert "secret" not in json.dumps(result)


def test_tasks_table_can_be_omitted_for_old_deployment():
    module = load_module()
    args = Namespace(jobs_table="jobs", tasks_table=None, region=None, queues=[], dlqs=[])
    report = module.run(
        args,
        session_factory=lambda: FakeSession({"jobs": FakeTable([{"Items": []}])}),
        now=datetime(2026, 9, 21, tzinfo=timezone.utc),
    )

    assert report["tables"]["jobs"]["item_count"] == 0
    assert report["tables"]["scan_tasks"] == {"status": "not_configured"}


def test_missing_tasks_table_is_reported_without_hiding_other_diagnostics():
    module = load_module()
    args = Namespace(jobs_table="jobs", tasks_table="tasks", region=None, queues=[], dlqs=[])
    report = module.run(
        args,
        session_factory=lambda: FakeSession({
            "jobs": FakeTable([{"Items": [{"status": "processing"}]}]),
            "tasks": ErrorTable("ResourceNotFoundException"),
        }),
    )

    assert report["tables"]["jobs"]["item_count"] == 1
    assert report["tables"]["scan_tasks"] == {"status": "not_deployed"}


def test_tasks_access_denied_is_still_an_error():
    module = load_module()
    args = Namespace(jobs_table="jobs", tasks_table="tasks", region=None, queues=[], dlqs=[])
    with pytest.raises(ClientError, match="AccessDeniedException"):
        module.run(
            args,
            session_factory=lambda: FakeSession({
                "jobs": FakeTable([{"Items": []}]),
                "tasks": ErrorTable("AccessDeniedException"),
            }),
        )
