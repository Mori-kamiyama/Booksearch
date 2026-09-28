#!/usr/bin/env python3
"""Read-only diagnostics for scan jobs, task leases, queues, and DLQs.

The command deliberately emits aggregate data only.  It never reads message
bodies, prints job/task IDs, changes DynamoDB, or changes queue visibility.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import os
import time
from typing import Any, Iterable

import boto3
from botocore.exceptions import ClientError


LEASE_FIELDS = ("lease_until", "lookup_lease_until", "ocr_lease_until")
SCAN_PROJECTION = (
    "#status, #state, created_at, updated_at, lease_until, "
    "lookup_lease_until, ocr_lease_until"
)
SCAN_ATTRIBUTE_NAMES = {"#status": "status", "#state": "state"}


def _parse_time(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), timezone.utc)
        text = str(value).strip()
        if text.isdigit():
            return datetime.fromtimestamp(float(text), timezone.utc)
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _age_seconds(value: Any, now: datetime) -> float | None:
    parsed = _parse_time(value)
    if parsed is None:
        return None
    return max(0.0, (now - parsed).total_seconds())


def _newest_or_oldest_age(items: Iterable[dict[str, Any]], now: datetime) -> float | None:
    ages = [age for item in items if (age := _age_seconds(item.get("updated_at") or item.get("created_at"), now)) is not None]
    return max(ages) if ages else None


def summarize_items(items: Iterable[dict[str, Any]], now: datetime | None = None) -> dict[str, Any]:
    """Summarize items without returning their identifiers or contents."""
    now = now or datetime.now(timezone.utc)
    count = 0
    statuses: Counter[str] = Counter()
    oldest_by_status: dict[str, float] = {}
    updated_ages: list[float] = []
    lease_summary: dict[str, dict[str, int]] = {
        field: {"active": 0, "expired": 0} for field in LEASE_FIELDS
    }
    for item in items:
        count += 1
        status = str(item.get("status") or item.get("state") or "missing")
        statuses[status] += 1
        age = _age_seconds(item.get("updated_at") or item.get("created_at"), now)
        if age is not None:
            updated_ages.append(age)
            oldest_by_status[status] = max(oldest_by_status.get(status, 0.0), age)
        for field in LEASE_FIELDS:
            if field not in item:
                continue
            lease = _parse_time(item[field])
            if lease is None:
                continue
            key = "active" if lease > now else "expired"
            lease_summary[field][key] += 1
    return {
        "item_count": count,
        "status_counts": dict(sorted(statuses.items())),
        "oldest_updated_age_seconds": round(max(updated_ages), 3) if updated_ages else None,
        "oldest_age_by_status_seconds": {
            key: round(value, 3) for key, value in sorted(oldest_by_status.items())
        },
        "leases": lease_summary,
    }


def scan_table(table: Any, now: datetime | None = None) -> dict[str, Any]:
    """Scan every DynamoDB page and return aggregate diagnostics."""
    now = now or datetime.now(timezone.utc)
    started = time.perf_counter()
    pages = 0
    items: list[dict[str, Any]] = []
    consumed_capacity = 0.0
    last_key = None
    while True:
        kwargs = {
            "ProjectionExpression": SCAN_PROJECTION,
            "ExpressionAttributeNames": SCAN_ATTRIBUTE_NAMES,
            "ReturnConsumedCapacity": "TOTAL",
        }
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        page = table.scan(**kwargs)
        pages += 1
        items.extend(page.get("Items") or [])
        capacity = page.get("ConsumedCapacity") or {}
        try:
            consumed_capacity += float(capacity.get("CapacityUnits", 0))
        except (AttributeError, TypeError, ValueError):
            # Capacity is advisory diagnostics; malformed/missing values should
            # not hide the useful item and queue summaries.
            pass
        last_key = page.get("LastEvaluatedKey")
        if not last_key:
            break
    result = summarize_items(items, now)
    result["scan_pages"] = pages
    result["elapsed_seconds"] = round(max(0.0, time.perf_counter() - started), 3)
    result["consumed_read_capacity"] = round(consumed_capacity, 3)
    return result


def _metric_age(cloudwatch: Any, queue_name: str, now: datetime) -> float | None:
    if cloudwatch is None:
        return None
    try:
        response = cloudwatch.get_metric_statistics(
            Namespace="AWS/SQS",
            MetricName="ApproximateAgeOfOldestMessage",
            Dimensions=[{"Name": "QueueName", "Value": queue_name}],
            StartTime=now - timedelta(minutes=15),
            EndTime=now,
            Period=300,
            Statistics=["Maximum"],
        )
    except Exception:
        # Queue depth remains useful when the operator has SQS but not
        # CloudWatch read permission, or metrics have not emitted yet.
        return None
    datapoints = response.get("Datapoints") or []
    if not datapoints:
        return None
    return max(float(point.get("Maximum", 0)) for point in datapoints)


def queue_snapshot(sqs: Any, cloudwatch: Any, label: str, url: str,
                   now: datetime | None = None) -> dict[str, Any]:
    """Read SQS depth and CloudWatch oldest-message age without message bodies."""
    now = now or datetime.now(timezone.utc)
    attrs = sqs.get_queue_attributes(
        QueueUrl=url,
        AttributeNames=[
            "QueueArn", "ApproximateNumberOfMessages",
            "ApproximateNumberOfMessagesNotVisible", "ApproximateNumberOfMessagesDelayed",
            "RedrivePolicy",
        ],
    ).get("Attributes", {})
    arn = attrs.get("QueueArn", "")
    queue_name = arn.rsplit(":", 1)[-1] if arn else None
    def count(name: str) -> int:
        try:
            return int(attrs.get(name, 0))
        except (TypeError, ValueError):
            return 0
    result: dict[str, Any] = {
        "label": label,
        "queue_name": queue_name,
        "visible": count("ApproximateNumberOfMessages"),
        "in_flight": count("ApproximateNumberOfMessagesNotVisible"),
        "delayed": count("ApproximateNumberOfMessagesDelayed"),
        "oldest_age_seconds": _metric_age(cloudwatch, queue_name, now) if queue_name else None,
    }
    policy = attrs.get("RedrivePolicy")
    if policy:
        try:
            redrive = json.loads(policy)
            dlq_arn = redrive.get("deadLetterTargetArn", "")
            result["dlq_name"] = dlq_arn.rsplit(":", 1)[-1] if dlq_arn else None
        except (TypeError, ValueError):
            result["dlq_name"] = None
    return result


def _is_resource_not_found(error: Exception) -> bool:
    return (
        isinstance(error, ClientError)
        and error.response.get("Error", {}).get("Code") == "ResourceNotFoundException"
    )


def _endpoint(value: str) -> tuple[str, str]:
    label, separator, url = value.partition("=")
    if not separator or not label or not url.startswith("http"):
        raise argparse.ArgumentTypeError("endpoint must be LABEL=QUEUE_URL")
    return label, url


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs-table", default=os.environ.get("JOBS_TABLE"))
    parser.add_argument("--tasks-table", default=os.environ.get("SCAN_TASKS_TABLE"))
    parser.add_argument(
        "--queue", dest="queues", action="append", type=_endpoint, metavar="LABEL=QUEUE_URL",
        help="repeatable SQS queue endpoint; URLs are never printed",
    )
    parser.add_argument(
        "--dlq", dest="dlqs", action="append", type=_endpoint, metavar="LABEL=QUEUE_URL",
        help="repeatable DLQ endpoint; URLs are never printed",
    )
    parser.add_argument("--region", default=None)
    return parser


def run(args: argparse.Namespace, *, session_factory=boto3.Session,
        now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    if not args.jobs_table:
        raise ValueError("--jobs-table or JOBS_TABLE is required")
    session = session_factory(region_name=args.region) if args.region else session_factory()
    dynamodb = session.resource("dynamodb")
    jobs_report = scan_table(dynamodb.Table(args.jobs_table), now)
    if not args.tasks_table:
        tasks_report: dict[str, Any] = {"status": "not_configured"}
    else:
        try:
            tasks_report = scan_table(dynamodb.Table(args.tasks_table), now)
        except Exception as error:
            if not _is_resource_not_found(error):
                raise
            tasks_report = {"status": "not_deployed"}
    report: dict[str, Any] = {
        "generated_at": now.isoformat(),
        "tables": {
            "jobs": jobs_report,
            "scan_tasks": tasks_report,
        },
        "queues": [],
        "dlqs": [],
    }
    if args.queues or args.dlqs:
        sqs = session.client("sqs")
        cloudwatch = session.client("cloudwatch")
        explicit_dlq_names: set[str] = set()
        for label, url in args.queues or []:
            snapshot = queue_snapshot(sqs, cloudwatch, label, url, now)
            report["queues"].append(snapshot)
            if snapshot.get("dlq_name") and hasattr(sqs, "get_queue_url"):
                try:
                    dlq_url = sqs.get_queue_url(QueueName=snapshot["dlq_name"])["QueueUrl"]
                    dlq_snapshot = queue_snapshot(sqs, cloudwatch, f"{label}-dlq", dlq_url, now)
                    report["dlqs"].append(dlq_snapshot)
                    explicit_dlq_names.add(snapshot["dlq_name"])
                except Exception:
                    pass
        for label, url in args.dlqs or []:
            snapshot = queue_snapshot(sqs, cloudwatch, label, url, now)
            if snapshot.get("queue_name") not in explicit_dlq_names:
                report["dlqs"].append(snapshot)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        print(json.dumps(run(args), ensure_ascii=False, indent=2, sort_keys=True))
    except Exception as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
