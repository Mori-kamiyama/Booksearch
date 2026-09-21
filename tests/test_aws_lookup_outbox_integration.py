"""Exercise producer -> reconciliation -> lookup with real SDK expressions in Moto.

Run via uv run --no-project --with pytest --with boto3 --with 'moto[dynamodb,sqs,s3]' pytest ...
No AWS credentials or network calls are needed.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

ROOT = Path(__file__).resolve().parents[1]


def load_handler(name, folder):
    spec = importlib.util.spec_from_file_location(name, ROOT / "aws/functions" / folder / "handler.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def pipeline(monkeypatch):
    for key, value in {
        "AWS_DEFAULT_REGION": "us-east-1", "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing", "BUCKET": "lookup-test-assets",
        "JOBS_TABLE": "jobs", "CROPS_TABLE": "crops", "SECRET_GEMINI_ARN": "unused",
    }.items():
        monkeypatch.setenv(key, value)
    for key in ("SHELF_OBSERVATIONS_TABLE", "SHELF_CANDIDATES_TABLE", "CROP_FINGERPRINTS_TABLE"):
        monkeypatch.delenv(key, raising=False)
    with mock_aws():
        ddb = boto3.resource("dynamodb")
        jobs = ddb.create_table(
            TableName="jobs", KeySchema=[{"AttributeName": "job_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "job_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        sqs = boto3.client("sqs")
        url = sqs.create_queue(QueueName="lookup")["QueueUrl"]
        monkeypatch.setenv("LOOKUP_QUEUE_URL", url)
        boto3.client("s3").create_bucket(Bucket="lookup-test-assets")
        producer = load_handler("integration_ocr", "ocr_worker")
        dispatcher = load_handler("integration_dispatcher", "lookup_dispatcher")
        consumer = load_handler("integration_lookup", "lookup_worker")
        monkeypatch.setattr(consumer, "build_catalog", lambda job_id: {"job_id": job_id, "entries": []})
        jobs.put_item(Item={
            "job_id": "job-1", "status": "processing", "scan_closed": True,
            "accepted_frames": 1, "processed_frames": 1, "ocr_total": 1, "ocr_done": 1,
        })
        yield jobs, sqs, url, producer, dispatcher, consumer


def get_job(jobs):
    return jobs.get_item(Key={"job_id": "job-1"}, ConsistentRead=True)["Item"]


def persist_intent(pipeline):
    jobs, _, _, producer, _, _ = pipeline
    producer.queue_final_lookup_if_ready("job-1", get_job(jobs))
    return get_job(jobs)


def test_committed_intent_survives_missing_stream_event_and_duplicate_delivery(pipeline):
    jobs, sqs, url, _, dispatcher, consumer = pipeline
    item = persist_intent(pipeline)
    assert item["status"] == "lookup_pending"
    assert item["final_lookup_outbox_version"] == 1
    assert not sqs.receive_message(QueueUrl=url).get("Messages")
    # Deliberately never deliver a stream record. The durable row is enough.
    dispatcher.handler({"source": "aws.events"}, None)
    message = sqs.receive_message(QueueUrl=url)["Messages"][0]
    event = {"Records": [{"body": message["Body"]}]}
    consumer.handler(event, None)
    completed = get_job(jobs)
    assert completed["status"] == "done"
    assert boto3.client("s3").get_object(Bucket="lookup-test-assets", Key=completed["catalog_key"])
    consumer.handler(event, None)
    assert get_job(jobs)["catalog_key"] == completed["catalog_key"]
    dispatcher.handler({"source": "aws.events"}, None)
    assert not sqs.receive_message(QueueUrl=url).get("Messages")


def test_reconciliation_recovers_counter_to_intent_after_producer_gap(pipeline):
    jobs, sqs, url, _, dispatcher, _ = pipeline
    jobs.update_item(
        Key={"job_id": "job-1"},
        UpdateExpression="SET #s = :s, scan_closed = :closed, accepted_frames = :accepted, processed_frames = :processed, ocr_total = :total, ocr_done = :done",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":s": "processing", ":closed": True, ":accepted": 2, ":processed": 2,
            ":total": 3, ":done": 3,
        },
    )

    dispatcher.handler({"source": "aws.events"}, None)

    item = get_job(jobs)
    assert item["status"] == "lookup_pending"
    assert item["final_lookup_outbox_version"] == 1
    message = sqs.receive_message(QueueUrl=url)["Messages"][0]
    assert json.loads(message["Body"]) == {"job_id": "job-1", "incremental": False, "outbox_version": 1}


def test_failed_publication_retains_intent_and_can_retry(pipeline, monkeypatch):
    jobs, _, _, _, _, consumer = pipeline
    persist_intent(pipeline)
    original_put = consumer.s3.put_object
    monkeypatch.setattr(consumer.s3, "put_object", lambda **_: (_ for _ in ()).throw(RuntimeError("S3 unavailable")))
    event = {"Records": [{"body": json.dumps({"job_id": "job-1", "incremental": False})}]}
    with pytest.raises(RuntimeError):
        consumer.handler(event, None)
    assert get_job(jobs)["status"] == "lookup_pending"
    assert get_job(jobs)["final_lookup_outbox_version"] == 1
    monkeypatch.setattr(consumer.s3, "put_object", original_put)
    consumer.handler(event, None)
    assert get_job(jobs)["status"] == "done"


def test_late_incremental_cannot_replace_final_catalog(pipeline, monkeypatch):
    jobs, _, _, _, _, consumer = pipeline
    final_key = []
    def build(job_id):
        persist_intent(pipeline)
        monkeypatch.setattr(consumer, "build_catalog", lambda _: {"job_id": job_id, "entries": []})
        consumer.process_job(job_id, incremental=False)
        final_key.append(get_job(jobs)["catalog_key"])
        return {"job_id": job_id, "entries": [{"shelf_id": "late", "books": []}]}
    monkeypatch.setattr(consumer, "build_catalog", build)
    consumer.process_job("job-1", incremental=True)
    item = get_job(jobs)
    assert item["status"] == "done"
    assert item["catalog_key"] == final_key[0]
    assert item["detected_shelf_count"] == 0
    catalog = json.loads(boto3.client("s3").get_object(Bucket="lookup-test-assets", Key=item["catalog_key"])["Body"].read())
    assert catalog["entries"] == []


@pytest.mark.parametrize("status", ["done", "canceled", "no_detection", "no_readable_crops"])
def test_stale_producer_snapshot_does_not_reopen_terminal_job(pipeline, status):
    jobs, _, _, producer, _, consumer = pipeline
    stale = get_job(jobs)
    jobs.update_item(Key={"job_id": "job-1"}, UpdateExpression="SET #s = :s",
                     ExpressionAttributeNames={"#s": "status"}, ExpressionAttributeValues={":s": status})
    producer.queue_final_lookup_if_ready("job-1", stale)
    producer.queue_final_lookup_if_ready("job-1", get_job(jobs))
    consumer.process_job("job-1")
    assert get_job(jobs)["status"] == status
    assert "final_lookup_outbox_version" not in get_job(jobs)


def test_expired_lease_can_be_reclaimed_but_active_lease_cannot(pipeline):
    jobs, _, _, _, _, consumer = pipeline
    persist_intent(pipeline)
    token = consumer.claim_final_lookup("job-1")
    with pytest.raises(consumer.RetryableLeaseError):
        consumer.process_job("job-1")
    assert get_job(jobs)["lookup_claim_token"] == token
    jobs.update_item(Key={"job_id": "job-1"}, UpdateExpression="SET lookup_lease_until = :expired",
                     ExpressionAttributeValues={":expired": 0})
    consumer.process_job("job-1")
    assert get_job(jobs)["status"] == "done"


def test_lost_worker_cannot_publish_after_another_worker_claims(pipeline, monkeypatch):
    jobs, _, _, _, _, consumer = pipeline
    persist_intent(pipeline)
    original_put = consumer.s3.put_object
    def steal_claim(**kwargs):
        result = original_put(**kwargs)
        jobs.update_item(Key={"job_id": "job-1"}, UpdateExpression="SET lookup_claim_token = :other",
                         ExpressionAttributeValues={":other": "new-owner"})
        return result
    monkeypatch.setattr(consumer.s3, "put_object", steal_claim)
    consumer.process_job("job-1")
    item = get_job(jobs)
    assert item["status"] == "lookup_pending"
    assert item["lookup_claim_token"] == "new-owner"
    assert "catalog_key" not in item


def test_send_accepted_but_response_lost_replays_safely(pipeline, monkeypatch):
    jobs, sqs, url, _, dispatcher, consumer = pipeline
    item = persist_intent(pipeline)
    from boto3.dynamodb.types import TypeSerializer
    serialize = TypeSerializer().serialize
    record = {
        "eventID": "different-from-sequence", "eventName": "MODIFY",
        "dynamodb": {"SequenceNumber": "12345", "OldImage": {},
                     "NewImage": {key: serialize(value) for key, value in item.items()}},
    }
    original_send = dispatcher.sqs.send_message
    def lost_response(**kwargs):
        original_send(**kwargs)
        raise RuntimeError("response lost after SQS accepted")
    monkeypatch.setattr(dispatcher.sqs, "send_message", lost_response)
    result = dispatcher.handler({"Records": [record]}, None)
    assert result == {"batchItemFailures": [{"itemIdentifier": "12345"}]}
    monkeypatch.setattr(dispatcher.sqs, "send_message", original_send)
    assert dispatcher.handler({"Records": [record]}, None) == {"batchItemFailures": []}
    messages = sqs.receive_message(QueueUrl=url, MaxNumberOfMessages=10)["Messages"]
    assert len(messages) == 2
    for message in messages:
        consumer.handler({"Records": [{"body": message["Body"]}]}, None)
    assert get_job(jobs)["status"] == "done"
    keys = boto3.client("s3").list_objects_v2(Bucket="lookup-test-assets")["Contents"]
    assert len(keys) == 1


def test_batch_ocr_finalizes_via_same_durable_intent(pipeline):
    jobs, sqs, url, producer, dispatcher, consumer = pipeline
    jobs.put_item(Item={"job_id": "job-1", "status": "ocr_pending", "ocr_total": 1, "ocr_done": 1})
    producer.advance_after_crop("job-1", get_job(jobs), incremental=False)
    assert get_job(jobs)["status"] == "lookup_pending"
    dispatcher.handler({"source": "aws.events"}, None)
    message = sqs.receive_message(QueueUrl=url)["Messages"][0]
    consumer.handler({"Records": [{"body": message["Body"]}]}, None)
    assert get_job(jobs)["status"] == "done"
    # A late OCR duplicate must not reopen the completed job.
    producer.advance_after_crop("job-1", get_job(jobs), incremental=False)
    assert get_job(jobs)["status"] == "done"


@pytest.mark.parametrize("status", ["processing", "ocr_pending"])
def test_reconciliation_repairs_crash_between_counter_update_and_intent(pipeline, status):
    jobs, sqs, url, _, dispatcher, consumer = pipeline
    # Counters have committed, but producer terminated before creating intent.
    jobs.update_item(Key={"job_id": "job-1"}, UpdateExpression="SET #s = :s",
                     ExpressionAttributeNames={"#s": "status"}, ExpressionAttributeValues={":s": status})
    dispatcher.handler({"source": "aws.events"}, None)
    assert get_job(jobs)["final_lookup_outbox_version"] == 1
    message = sqs.receive_message(QueueUrl=url)["Messages"][0]
    consumer.handler({"Records": [{"body": message["Body"]}]}, None)
    assert get_job(jobs)["status"] == "done"


@pytest.mark.parametrize("change", [
    {"scan_closed": False}, {"accepted_frames": 2}, {"ocr_total": 2}, {"status": "canceled"},
])
def test_reconciliation_does_not_finalize_incomplete_or_canceled_job(pipeline, change):
    jobs, sqs, url, _, dispatcher, _ = pipeline
    item = get_job(jobs)
    item.update(change)
    jobs.put_item(Item=item)
    dispatcher.handler({"source": "aws.events"}, None)
    assert "final_lookup_outbox_version" not in get_job(jobs)
    assert not sqs.receive_message(QueueUrl=url).get("Messages")


def test_batch_without_ocr_persists_intent_with_its_results(pipeline, monkeypatch):
    jobs, sqs, url, _, dispatcher, consumer = pipeline
    import sys
    import types
    monkeypatch.setitem(sys.modules, "ultralytics", types.SimpleNamespace(YOLO=object))
    monkeypatch.setenv("OCR_QUEUE_URL", "unused-ocr-queue")
    spec = importlib.util.spec_from_file_location("integration_yolo", ROOT / "aws/functions/yolo_worker/handler.py")
    yolo = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, yolo)
    spec.loader.exec_module(yolo)
    monkeypatch.setattr(yolo, "process_frame", lambda *_: ([], {"width": 100, "height": 100}))
    jobs.put_item(Item={"job_id": "job-1", "status": "pending"})
    yolo.process_job("job-1", ["test.jpg"])
    assert get_job(jobs)["status"] == "lookup_pending"
    assert get_job(jobs)["crop_total"] == 0
    dispatcher.handler({"source": "aws.events"}, None)
    message = sqs.receive_message(QueueUrl=url)["Messages"][0]
    consumer.handler({"Records": [{"body": message["Body"]}]}, None)
    completed = get_job(jobs)
    # A retried detector with a changed result must not reset a completed job.
    monkeypatch.setattr(yolo, "process_frame", lambda *_: ([{
        "status": "ocr_pending", "quality": {"reasons": []},
        "crop_id": "new", "crop_key": "unused", "fingerprint_scope": "unused", "phash": "unused",
    }], {"width": 100, "height": 100}))
    yolo.process_job("job-1", ["test.jpg"])
    assert get_job(jobs) == completed
    # An already running duplicate can also fail after another worker finishes.
    monkeypatch.setattr(yolo, "process_job", lambda *_: (_ for _ in ()).throw(RuntimeError("late failure")))
    with pytest.raises(RuntimeError):
        yolo.handler({"Records": [{"body": json.dumps({"job_id": "job-1", "image_key": "test.jpg"})}]}, None)
    assert get_job(jobs) == completed


def test_reconciliation_rechecks_readiness_when_scan_snapshot_is_stale(pipeline, monkeypatch):
    jobs, sqs, url, _, dispatcher, _ = pipeline
    original_update = dispatcher.jobs_table.update_item
    def additional_work_arrived(**kwargs):
        jobs.update_item(Key={"job_id": "job-1"}, UpdateExpression="SET ocr_total = :more",
                         ExpressionAttributeValues={":more": 2})
        return original_update(**kwargs)
    monkeypatch.setattr(dispatcher.jobs_table, "update_item", additional_work_arrived)
    dispatcher.handler({"source": "aws.events"}, None)
    assert get_job(jobs)["status"] == "processing"
    assert "final_lookup_outbox_version" not in get_job(jobs)
    assert not sqs.receive_message(QueueUrl=url).get("Messages")
