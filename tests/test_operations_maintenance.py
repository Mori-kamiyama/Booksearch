"""Safety checks for expiration, freshness monitoring and image cleanup."""
import importlib.util
from datetime import datetime, timezone, timedelta
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

ROOT = Path(__file__).resolve().parents[1]

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

m = load('maintenance', 'aws/functions/maintenance/handler.py')
c = load('cleanup', 'aws/scripts/cleanup_orphan_images.py')
NOW = int(datetime(2026, 9, 27, 12, tzinfo=timezone.utc).timestamp())
OLD = '2026-09-20T12:00:00Z'

@pytest.fixture
def tables(monkeypatch):
    monkeypatch.setenv('AWS_DEFAULT_REGION', 'us-east-1')
    with mock_aws():
        ddb = boto3.resource('dynamodb')
        result = {}
        for name, sort in [('jobs', None), ('tasks', 'task_id'), ('crops', 'crop_id')]:
            keys = [{'AttributeName': 'job_id', 'KeyType': 'HASH'}]
            if sort:
                keys.append({'AttributeName': sort, 'KeyType': 'RANGE'})
            result[name] = ddb.create_table(TableName=name, KeySchema=keys,
                AttributeDefinitions=[{'AttributeName': k['AttributeName'], 'AttributeType': 'S'} for k in keys],
                BillingMode='PAY_PER_REQUEST')
        yield ddb, result

def job(tables):
    item = {'job_id': 'j', 'status': 'processing', 'updated_at': OLD, 'accepted_frames': 1}
    tables['jobs'].put_item(Item=item)
    return item

def test_expiration_preserves_records(tables):
    ddb, t = tables
    item = job(t)
    t['tasks'].put_item(Item={'job_id': 'j', 'task_id': 't', 'state': 'pending', 'created_at': OLD})
    assert m.expire_job(ddb, item, NOW, t) == 'expired'
    updated = t['jobs'].get_item(Key={'job_id': 'j'})['Item']
    assert updated['status'] == 'failed'
    assert updated['expiration_reason'] == 'stalled_timeout'
    assert t['tasks'].get_item(Key={'job_id': 'j', 'task_id': 't'})['Item']

@pytest.mark.parametrize('change', [{'status': 'done'}, {'updated_at': 'bad'}, {'updated_at': '2026-09-27T11:00:00Z'}, {'lookup_lease_until': NOW + 60}])
def test_ineligible_untouched(tables, change):
    ddb, t = tables
    item = {**job(t), **change}
    t['jobs'].put_item(Item=item)
    assert m.expire_job(ddb, item, NOW, t) == 'fresh'

@pytest.mark.parametrize('change,expected', [({'lease_until': NOW + 60}, 'leased'), ({'updated_at': '2026-09-27T11:00:00Z'}, 'recent_child')])
def test_child_activity_protected(tables, change, expected):
    ddb, t = tables
    item = job(t)
    t['tasks'].put_item(Item={'job_id': 'j', 'task_id': 't', 'state': 'processing', 'created_at': OLD, **change})
    assert m.expire_job(ddb, item, NOW, t) == expected
    assert t['jobs'].get_item(Key={'job_id': 'j'})['Item']['status'] == 'processing'

@pytest.mark.parametrize('race', ['job', 'child'])
def test_concurrent_activity_cancels_expiration(tables, monkeypatch, race):
    ddb, t = tables
    item = job(t)
    child = {'job_id': 'j', 'task_id': 't', 'state': 'pending', 'created_at': OLD}
    t['tasks'].put_item(Item=child)
    original = ddb.meta.client.transact_write_items
    def raced(**kwargs):
        if race == 'job':
            t['jobs'].put_item(Item={**item, 'accepted_frames': 2})
        else:
            t['tasks'].put_item(Item={**child, 'lease_until': int(NOW + 60)})
        return original(**kwargs)
    monkeypatch.setattr(ddb.meta.client, 'transact_write_items', raced)
    assert m.expire_job(ddb, item, NOW, t) == 'changed'
    assert t['jobs'].get_item(Key={'job_id': 'j'})['Item']['status'] == 'processing'

def test_large_job_not_partially_expired(tables):
    ddb, t = tables
    item = job(t)
    for i in range(100):
        t['tasks'].put_item(Item={'job_id': 'j', 'task_id': str(i), 'state': 'done', 'created_at': OLD})
    assert m.expire_job(ddb, item, NOW, t) == 'too_many_children'

def test_pagination():
    class Pages:
        def scan(self, **kwargs):
            return {'Items': [2]} if kwargs.get('ExclusiveStartKey') else {'Items': [1], 'LastEvaluatedKey': {'k': 'next'}}
    assert list(m.all_items(Pages())) == [1, 2]

@pytest.mark.parametrize('iso,week,stale', [('2026-09-28T05:59:00+00:00', '2026-39', False), ('2026-09-28T06:00:00+00:00', '2026-39', True), ('2026-09-28T06:00:00+00:00', '2026-40', False), ('2027-01-01T12:00:00+00:00', '2026-53', False)])
def test_featured_week(iso, week, stale):
    assert m.featured_stale({'featured-week': week}, datetime.fromisoformat(iso).timestamp()) is stale

def test_cleanup_scope_and_references():
    owner = '64264b73-0cff-473f-9b2b-486bbe245a44'
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    obj = {'Key': f'crops/{owner}/crop.jpg', 'LastModified': cutoff - timedelta(days=1)}
    refs = c.referenced_jobs({'items': [{'url': f's3://bucket/{obj["Key"]}'}]})
    assert not c.candidate(obj, refs, cutoff)
    assert c.candidate(obj, set(), cutoff)
    assert not c.candidate({**obj, 'Key': 'catalog/a.jpg'}, set(), cutoff)
    assert not c.candidate({**obj, 'LastModified': cutoff}, set(), cutoff)

@pytest.mark.parametrize('enabled', [False, True])
def test_cleanup_apply_requires_recoverable_versions(monkeypatch, capsys, enabled):
    import json
    monkeypatch.setenv('AWS_DEFAULT_REGION', 'us-east-1')
    with mock_aws():
        session = boto3.Session(region_name='us-east-1')
        s3 = session.client('s3')
        s3.create_bucket(Bucket='cleanup-test')
        if enabled:
            s3.put_bucket_versioning(Bucket='cleanup-test', VersioningConfiguration={'Status': 'Enabled'})
        key = 'uploads/64264b73-0cff-473f-9b2b-486bbe245a44/a.jpg'
        stored = s3.put_object(Bucket='cleanup-test', Key=key, Body=b'image')
        class Pages:
            def paginate(self, **kwargs):
                return [{'Contents': [{'Key': key, 'LastModified': datetime.now(timezone.utc) - timedelta(days=100), 'Size': 5, 'ETag': stored['ETag']}]}] if kwargs['Prefix'] == 'uploads/' else []
        monkeypatch.setattr(s3, 'get_paginator', lambda _: Pages())
        original = session.client
        monkeypatch.setattr(session, 'client', lambda name, **kw: s3 if name == 's3' else original(name, **kw))
        monkeypatch.setattr(c.boto3, 'Session', lambda **_: session)
        monkeypatch.setattr(c, 'collect_references', lambda *_: set())
        ddb = session.resource('dynamodb')
        ddb.create_table(TableName='booksearch-jobs', KeySchema=[{'AttributeName': 'job_id', 'KeyType': 'HASH'}], AttributeDefinitions=[{'AttributeName': 'job_id', 'AttributeType': 'S'}], BillingMode='PAY_PER_REQUEST')
        monkeypatch.setattr('sys.argv', ['cleanup', '--bucket', 'cleanup-test', '--apply'])
        if not enabled:
            with pytest.raises(RuntimeError, match='Versioning'):
                c.main()
            assert s3.get_object(Bucket='cleanup-test', Key=key)['Body'].read() == b'image'
        else:
            c.main()
            output = json.loads(capsys.readouterr().out)
            assert output['count'] == 1
            marker = output['objects'][0]['delete_marker_version']
            s3.delete_object(Bucket='cleanup-test', Key=key, VersionId=marker)
            assert s3.get_object(Bucket='cleanup-test', Key=key)['Body'].read() == b'image'

@pytest.mark.parametrize('present', [False, True])
def test_handler_publishes_independent_freshness_metrics(tables, monkeypatch, present):
    ddb, t = tables
    for key, value in {'JOBS_TABLE': 'jobs', 'SCAN_TASKS_TABLE': 'tasks', 'CROPS_TABLE': 'crops', 'PROJECT_NAME': 'test', 'FRONTEND_BUCKET': 'test-frontend'}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(m.time, 'time', lambda: NOW)
    s3 = boto3.client('s3')
    s3.create_bucket(Bucket='test-frontend')
    if present:
        s3.put_object(Bucket='test-frontend', Key='home.html', Body=b'home', Metadata={'featured-week': '2026-39'})
    result = m.handler({}, None)
    assert result['featured_stale'] is not present
    metrics = boto3.client('cloudwatch').list_metrics(Namespace='Booksearch/Operations')['Metrics']
    assert {metric['MetricName'] for metric in metrics} == {'FeaturedStale', 'MaintenanceHealthy', 'StalledJobsSkipped'}
