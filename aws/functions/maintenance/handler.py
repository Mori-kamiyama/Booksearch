"""Conservative hourly expiration and independent featured freshness check."""
from datetime import datetime, timezone
import os
import time
import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

ACTIVE = {'uploading', 'pending', 'collecting', 'processing', 'ocr_pending', 'lookup_pending'}
LEASES = ('lease_until', 'ocr_lease_until', 'lookup_lease_until')
GUARDS = ('status', 'state', 'updated_at', 'created_at', 'accepted_frames', 'processed_frames',
          'ocr_done', 'ocr_total', 'lease_until', 'ocr_lease_until', 'lookup_lease_until',
          'claim_token', 'ocr_claim_token', 'lookup_claim_token', 'detection_token')


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            return None
        return parsed.timestamp()
    except (ValueError, TypeError):
        return None


def stale_job(job, now):
    updated = timestamp(job.get('updated_at') or job.get('created_at'))
    return (job.get('status') in ACTIVE and updated is not None and now - updated >= 86400
            and all(int(job.get(field, 0)) <= now for field in LEASES))


def guarded(item):
    names, values, expressions = {}, {}, []
    for i, field in enumerate(GUARDS):
        name = f'#g{i}'
        names[name] = field
        if field in item:
            value = f':g{i}'
            values[value] = item[field]
            expressions.append(f'{name} = {value}')
        else:
            expressions.append(f'attribute_not_exists({name})')
    result = {'ConditionExpression': ' AND '.join(expressions), 'ExpressionAttributeNames': names}
    if values:
        result['ExpressionAttributeValues'] = values
    return result


def all_items(table, operation='scan', **kwargs):
    while True:
        result = getattr(table, operation)(**kwargs)
        yield from result.get('Items', [])
        if not result.get('LastEvaluatedKey'):
            return
        kwargs['ExclusiveStartKey'] = result['LastEvaluatedKey']


def expire_job(ddb, job, now, tables):
    if not stale_job(job, now):
        return 'fresh'
    children = []
    for name, key in [('tasks', 'task_id'), ('crops', 'crop_id')]:
        for item in all_items(tables[name], 'query', KeyConditionExpression=Key('job_id').eq(job['job_id']), ConsistentRead=True):
            if any(int(item.get(field, 0)) > now for field in LEASES):
                return 'leased'
            # Recent worker activity must not be expired even if the job timestamp is old.
            updated = timestamp(item.get('updated_at') or item.get('created_at'))
            if updated is not None and now - updated < 86400:
                return 'recent_child'
            children.append({'ConditionCheck': {
                'TableName': tables[name].name, 'Key': {'job_id': job['job_id'], key: item[key]}, **guarded(item)}})
            if len(children) > 99:
                return 'too_many_children'
    guard = guarded(job)
    guard['ExpressionAttributeNames'].update({'#new_status': 'status', '#error': 'error'})
    guard.setdefault('ExpressionAttributeValues', {}).update({
        ':failed': 'failed', ':reason': 'stalled_timeout', ':error': '処理が24時間以上進まなかったため期限切れになりました。再度スキャンしてください。',
        ':expired': datetime.fromtimestamp(now, timezone.utc).isoformat().replace('+00:00', 'Z')})
    transaction = [{'Update': {
        'TableName': tables['jobs'].name, 'Key': {'job_id': job['job_id']}, **guard,
        'UpdateExpression': 'SET #new_status = :failed, #error = :error, expired_at = :expired, updated_at = :expired, expiration_reason = :reason',
    }}, *children]
    try:
        # Resource client applies DynamoDB native-value serialization.
        ddb.meta.client.transact_write_items(TransactItems=transaction)
        return 'expired'
    except ClientError as error:
        if error.response['Error']['Code'] == 'TransactionCanceledException':
            return 'changed'
        raise


def featured_stale(metadata, now):
    dt = datetime.fromtimestamp(now, timezone.utc)
    year, week, day = dt.isocalendar()
    if day == 1 and dt.hour < 6:
        return False  # weekly build has six hours to publish
    return metadata.get('featured-week') != f'{year}-{week}'


def handler(event, context):
    now = int(time.time())
    ddb = boto3.resource('dynamodb')
    tables = {key: ddb.Table(os.environ[env]) for key, env in [
        ('jobs', 'JOBS_TABLE'), ('tasks', 'SCAN_TASKS_TABLE'), ('crops', 'CROPS_TABLE')]}
    counts = {}
    for job in all_items(tables['jobs'], ConsistentRead=True):
        if context and context.get_remaining_time_in_millis() < 15000:
            raise RuntimeError('maintenance exceeded safe scan time budget')
        result = expire_job(ddb, job, now, tables)
        counts[result] = counts.get(result, 0) + 1
    try:
        home = boto3.client('s3').head_object(Bucket=os.environ['FRONTEND_BUCKET'], Key='home.html')
        stale = featured_stale(home.get('Metadata', {}), now)
    except ClientError as error:
        if error.response['Error']['Code'] not in {'404', 'NoSuchKey', 'NotFound'}:
            raise
        stale = True
    dimensions = [{'Name': 'Project', 'Value': os.environ['PROJECT_NAME']}]
    boto3.client('cloudwatch').put_metric_data(Namespace='Booksearch/Operations', MetricData=[
        {'MetricName': 'FeaturedStale', 'Value': int(stale), 'Unit': 'Count', 'Dimensions': dimensions},
        {'MetricName': 'MaintenanceHealthy', 'Value': 1, 'Unit': 'Count', 'Dimensions': dimensions},
        {'MetricName': 'StalledJobsSkipped', 'Value': sum(counts.get(k, 0) for k in ['leased', 'recent_child', 'too_many_children', 'changed']), 'Unit': 'Count', 'Dimensions': dimensions},
    ])
    print({'expiration': counts, 'featured_stale': stale})
    return {'expiration': counts, 'featured_stale': stale}
