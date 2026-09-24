"""Deliver durable scan outbox records to SQS.

DynamoDB Streams retries a failed record. Sending before marking delivered makes
duplicate SQS messages possible, so consumers must claim their job atomically.
"""
import json
import os
import boto3
from botocore.exceptions import ClientError

OUTBOX_TABLE = os.environ["OUTBOX_TABLE"]
JOBS_TABLE = os.environ["JOBS_TABLE"]
CROPS_TABLE = os.environ["CROPS_TABLE"]
YOLO_QUEUE_URL = os.environ["YOLO_QUEUE_URL"]
OCR_QUEUE_URL = os.environ["OCR_QUEUE_URL"]
LOOKUP_QUEUE_URL = os.environ["LOOKUP_QUEUE_URL"]
ddb = boto3.resource("dynamodb").Table(OUTBOX_TABLE)
jobs = boto3.resource("dynamodb").Table(JOBS_TABLE)
crops = boto3.resource("dynamodb").Table(CROPS_TABLE)
ddb_client = boto3.client("dynamodb")
sqs = boto3.client("sqs")

def value(image, name):
    field = image.get(name, {})
    return field.get("S")

def deliver(job_id, event_type, image_key=None, crop_id=None, crop_key=None):
    if event_type == "yolo":
        sqs.send_message(QueueUrl=YOLO_QUEUE_URL, MessageBody=json.dumps({"job_id": job_id, "image_key": image_key}))
    elif event_type == "lookup":
        sqs.send_message(QueueUrl=LOOKUP_QUEUE_URL, MessageBody=json.dumps({"job_id": job_id}))
    elif event_type.startswith("ocr:"):
        sqs.send_message(QueueUrl=OCR_QUEUE_URL, MessageBody=json.dumps({"job_id": job_id, "crop_id": crop_id, "crop_key": crop_key}))
    else:
        return
    try:
        ddb.update_item(Key={"job_id": job_id, "event_type": event_type}, UpdateExpression="SET delivered_at = :now", ConditionExpression="attribute_not_exists(delivered_at)", ExpressionAttributeValues={":now": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()})
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise


def repair_ocr_intents(item):
    """Make every claimable crop reachable even if YOLO died after its state change."""
    if item.get("status") != "ocr_pending":
        return
    try:
        page = crops.query(
            KeyConditionExpression="job_id = :job_id",
            ExpressionAttributeValues={":job_id": item["job_id"]},
        )
        for crop in page.get("Items", []):
            if crop.get("status") not in ("ocr_pending", "ocr_processing"):
                continue
            event_type = f"ocr:{crop['crop_id']}"
            try:
                ddb.put_item(
                    Item={"job_id": item["job_id"], "event_type": event_type,
                          "crop_id": crop["crop_id"], "crop_key": crop["crop_key"],
                          "created_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()},
                    ConditionExpression="attribute_not_exists(job_id)",
                )
            except ClientError as exc:
                if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                    raise
    except Exception:
        # Reconciliation is intentionally best-effort: the next scheduled run
        # retries it, and it must not suppress delivery of unrelated outbox rows.
        return

def repair_lookup_intent(item):
    """Recover a crash between OCR completion and the final outbox intent."""
    if item.get("status") != "ocr_pending" or int(item.get("ocr_total", 0)) <= 0 or int(item.get("ocr_done", 0)) < int(item.get("ocr_total", 0)):
        return
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    try:
        ddb_client.transact_write_items(TransactItems=[
            {"Update": {"TableName": JOBS_TABLE, "Key": {"job_id": {"S": item["job_id"]}}, "UpdateExpression": "SET #s = :lookup", "ConditionExpression": "#s = :ocr AND ocr_done >= ocr_total", "ExpressionAttributeNames": {"#s": "status"}, "ExpressionAttributeValues": {":lookup": {"S": "lookup_pending"}, ":ocr": {"S": "ocr_pending"}}}},
            {"Put": {"TableName": OUTBOX_TABLE, "Item": {"job_id": {"S": item["job_id"]}, "event_type": {"S": "lookup"}, "created_at": {"S": now}}, "ConditionExpression": "attribute_not_exists(job_id)"}},
        ])
    except ClientError:
        pass

def handler(event, context):
    failures = []
    for record in event.get("Records", []):
        if record.get("eventName") not in ("INSERT", "MODIFY"):
            continue
        image = record.get("dynamodb", {}).get("NewImage", {})
        event_type = value(image, "event_type")
        if (event_type not in ("yolo", "lookup") and not (event_type or "").startswith("ocr:")) or value(image, "delivered_at"):
            continue
        try:
            deliver(value(image, "job_id"), event_type, value(image, "image_key"), value(image, "crop_id"), value(image, "crop_key"))
        except Exception:
            failures.append({"itemIdentifier": record["eventID"]})
    # Streams are the fast path. The scheduled invocation is the recovery path
    # for stream retention exhaustion or an earlier Lambda configuration error.
    if not event.get("Records"):
        start = None
        while True:
            page = ddb.scan(ExclusiveStartKey=start) if start else ddb.scan()
            for item in page.get("Items", []):
                event_type = item.get("event_type", "")
                if (event_type in ("yolo", "lookup") or event_type.startswith("ocr:")) and not item.get("delivered_at"):
                    deliver(item["job_id"], event_type, item.get("image_key"), item.get("crop_id"), item.get("crop_key"))
            start = page.get("LastEvaluatedKey")
            if not start:
                break
        start = None
        while True:
            page = jobs.scan(ExclusiveStartKey=start) if start else jobs.scan()
            for item in page.get("Items", []):
                repair_ocr_intents(item)
                repair_lookup_intent(item)
            start = page.get("LastEvaluatedKey")
            if not start:
                break
    return {"batchItemFailures": failures}
