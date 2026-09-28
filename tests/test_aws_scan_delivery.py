"""DynamoDB transaction and replay checks; no external AWS/OCR calls."""
import importlib.util
import json
import sys
import types
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

ROOT = Path(__file__).parents[1]


def module(monkeypatch, folder, name):
    path = ROOT / 'aws/functions' / folder / 'handler.py'
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, result)
    spec.loader.exec_module(result)
    return result


@pytest.fixture
def pipeline(monkeypatch):
    for k, v in {'AWS_DEFAULT_REGION':'us-east-1', 'AWS_ACCESS_KEY_ID':'testing',
                 'AWS_SECRET_ACCESS_KEY':'testing', 'JOBS_TABLE':'jobs', 'CROPS_TABLE':'crops',
                 'SCAN_TASKS_TABLE':'tasks', 'BUCKET':'scan-test-assets', 'SECRET_GEMINI_ARN':'unused'}.items():
        monkeypatch.setenv(k,v)
    for k in ('CROP_FINGERPRINTS_TABLE','SHELF_OBSERVATIONS_TABLE','SHELF_CANDIDATES_TABLE'):
        monkeypatch.delenv(k, raising=False)
    with mock_aws():
        ddb = boto3.resource('dynamodb')
        tables = {}
        for name, sk in [('jobs', None),('crops','crop_id'),('tasks','task_id')]:
            keys = [{'AttributeName':'job_id','KeyType':'HASH'}]
            attrs = [{'AttributeName':'job_id','AttributeType':'S'}]
            if sk:
                keys.append({'AttributeName':sk,'KeyType':'RANGE'})
                attrs.append({'AttributeName':sk,'AttributeType':'S'})
            tables[name] = ddb.create_table(TableName=name,KeySchema=keys,AttributeDefinitions=attrs,BillingMode='PAY_PER_REQUEST')
        sqs = boto3.client('sqs')
        queues = {}
        for name in ['yolo','ocr','lookup']:
            queues[name] = sqs.create_queue(QueueName=name)['QueueUrl']
            monkeypatch.setenv(name.upper()+'_QUEUE_URL', queues[name])
        s3 = boto3.client('s3')
        s3.create_bucket(Bucket='scan-test-assets')
        s3.put_object(Bucket='scan-test-assets',Key='crop.jpg',Body=b'jpeg')
        tables['jobs'].put_item(Item={'job_id':'job','status':'ocr_pending','ocr_total':1,'ocr_done':0})
        task = {'job_id':'job','task_id':'batch','state':'done','detection_token':'winner','manifest_key':'manifest.json','incremental':False,'image_keys':['upload.jpg']}
        tables['tasks'].put_item(Item=task)
        crop = {'job_id':'job','crop_id':'crop','task_id':'batch','detection_token':'winner','status':'ocr_pending','requires_ocr':True,'crop_key':'crop.jpg'}
        tables['crops'].put_item(Item=crop)
        s3.put_object(Bucket='scan-test-assets',Key='manifest.json',Body=json.dumps({'crops':[crop],'frames':[]}).encode())
        ocr = module(monkeypatch,'ocr_worker','delivery_ocr')
        monkeypatch.setattr(ocr,'gemini_ocr',lambda *_:[{'title':'Book'}])
        dispatcher = module(monkeypatch,'lookup_dispatcher','delivery_dispatcher')
        lookup = module(monkeypatch,'lookup_worker','delivery_lookup')
        yield tables, sqs, queues, s3, ocr, dispatcher, lookup


def item(table, **key):
    return table.get_item(Key=key,ConsistentRead=True)['Item']


def test_ocr_transaction_failure_never_splits_result_and_count(pipeline, monkeypatch):
    tables, _, _, _, ocr, _, _ = pipeline
    original = ocr.transaction_client.transact_write_items
    monkeypatch.setattr(ocr.transaction_client,'transact_write_items',lambda **_: (_ for _ in ()).throw(RuntimeError('DB outage')))
    with pytest.raises(RuntimeError):
        ocr.process_one('job','crop','crop.jpg')
    assert item(tables['crops'],job_id='job',crop_id='crop')['status']=='ocr_pending'
    assert item(tables['jobs'],job_id='job')['ocr_done']==0
    monkeypatch.setattr(ocr.transaction_client,'transact_write_items',original)
    ocr.process_one('job','crop','crop.jpg')
    ocr.process_one('job','crop','crop.jpg')
    assert item(tables['crops'],job_id='job',crop_id='crop')['status']=='ocr_done'
    assert item(tables['jobs'],job_id='job')['ocr_done']==1


def test_ocr_response_loss_does_not_double_count(pipeline, monkeypatch):
    tables, _, _, _, ocr, _, _ = pipeline
    original = ocr.transaction_client.transact_write_items
    def lose_response(**kwargs):
        original(**kwargs)
        raise RuntimeError('response lost')
    monkeypatch.setattr(ocr.transaction_client,'transact_write_items',lose_response)
    with pytest.raises(RuntimeError):
        ocr.process_one('job','crop','crop.jpg')
    monkeypatch.setattr(ocr.transaction_client,'transact_write_items',original)
    ocr.process_one('job','crop','crop.jpg')
    assert item(tables['jobs'],job_id='job')['ocr_done']==1
    assert item(tables['jobs'],job_id='job')['status']=='lookup_pending'


def test_abandoned_crop_counts_once(pipeline):
    tables, _, _, _, ocr, _, _ = pipeline
    ocr.abandon_crop('job','crop','missing image',False)
    ocr.abandon_crop('job','crop','missing image',False)
    assert item(tables['jobs'],job_id='job')['ocr_done']==1
    assert item(tables['crops'],job_id='job',crop_id='crop')['ocr_error']=='missing image'


def test_task_completion_replays_missing_ocr_delivery(pipeline):
    tables, sqs, queues, _, ocr, dispatcher, _ = pipeline
    dispatcher.handler({'source':'aws.events'},None)
    message=json.loads(sqs.receive_message(QueueUrl=queues['ocr'])['Messages'][0]['Body'])
    assert message['task_id']=='batch'
    ocr.handler({'Records':[{'body':json.dumps(message)}]},None)
    assert item(tables['jobs'],job_id='job')['status']=='lookup_pending'
    dispatcher.handler({'source':'aws.events'},None)
    assert sqs.receive_message(QueueUrl=queues['lookup']).get('Messages')


def test_pending_task_survives_missing_initial_queue_delivery(pipeline):
    tables, sqs, queues, _, _, dispatcher, _ = pipeline
    tables['jobs'].put_item(Item={'job_id':'job','status':'pending'})
    tables['tasks'].put_item(Item={'job_id':'job','task_id':'batch','state':'pending','image_keys':['upload.jpg'],'incremental':False})
    dispatcher.handler({'source':'aws.events'},None)
    message=json.loads(sqs.receive_message(QueueUrl=queues['yolo'])['Messages'][0]['Body'])
    assert message['task_id']=='batch'
    assert message['image_keys']==['upload.jpg']


def test_obsolete_attempt_is_not_ocrd_or_included_in_catalog(pipeline, monkeypatch):
    tables, sqs, queues, s3, ocr, dispatcher, lookup = pipeline
    orphan={'job_id':'job','crop_id':'orphan','task_id':'batch','detection_token':'loser','status':'ocr_pending','requires_ocr':True,'crop_key':'crop.jpg'}
    tables['crops'].put_item(Item=orphan)
    monkeypatch.setattr(ocr,'gemini_ocr',lambda *_: (_ for _ in ()).throw(AssertionError('orphan reached OCR')))
    ocr.process_one('job','orphan','crop.jpg')
    assert [c['crop_id'] for c in lookup.fetch_crops('job')]==['crop']
    assert item(tables['jobs'],job_id='job')['ocr_done']==0


def test_cancel_prevents_ocr_count_and_dispatch(pipeline, monkeypatch):
    tables, sqs, queues, _, ocr, dispatcher, _ = pipeline
    tables['jobs'].put_item(Item={'job_id':'job','status':'canceled','ocr_done':0,'ocr_total':1})
    ocr.process_one('job','crop','crop.jpg')
    dispatcher.handler({'source':'aws.events'},None)
    assert not sqs.receive_message(QueueUrl=queues['ocr']).get('Messages')
    assert item(tables['jobs'],job_id='job')['ocr_done']==0


def load_yolo(pipeline, monkeypatch, readable=True):
    tables, _, _, _, _, _, _ = pipeline
    monkeypatch.setitem(sys.modules,'ultralytics',types.SimpleNamespace(YOLO=object))
    monkeypatch.syspath_prepend(str(ROOT/'aws/functions/yolo_worker'))
    monkeypatch.delitem(sys.modules,'durable',raising=False)
    yolo=module(monkeypatch,'yolo_worker','delivery_yolo')
    def detect(job_id,image_key,index,seen,*args,task_id=None,detection_token=None):
        crops=[]
        if readable:
            crop={'job_id':job_id,'crop_id':detection_token+'-crop','crop_key':'crop.jpg',
                  'task_id':task_id,'detection_token':detection_token,'requires_ocr':True,'status':'ocr_pending',
                  'quality':{'readable':True,'reasons':[]},'fingerprint_scope':'shelf','phash':'ff'}
            tables['crops'].put_item(Item=crop)
            crops.append(crop)
        return crops,{'image_key':image_key,'width':100,'height':200,'apriltag':{}}
    monkeypatch.setattr(yolo,'process_frame',detect)
    tables['jobs'].put_item(Item={'job_id':'new-job','status':'pending'})
    tables['tasks'].put_item(Item={'job_id':'new-job','task_id':'batch','state':'pending','image_keys':['upload.jpg'],'incremental':False})
    return yolo, {'job_id':'new-job','task_id':'batch','image_keys':['upload.jpg'],'incremental':False}


@pytest.mark.parametrize('readable',[False,True])
def test_detection_task_commits_counts_once_and_replays_downstream(pipeline,monkeypatch,readable):
    tables,sqs,queues,_,ocr,dispatcher,lookup=pipeline
    yolo,body=load_yolo(pipeline,monkeypatch,readable)
    yolo.process_task(body)
    yolo.process_task(body)
    job=item(tables['jobs'],job_id='new-job')
    assert job['processed_frames']==1
    assert job['ocr_total']==int(readable)
    assert item(tables['tasks'],job_id='new-job',task_id='batch')['state']=='done'
    assert job['image_width']==100
    if readable:
        dispatcher.dispatch_scan_task('new-job','batch')
        message=json.loads(sqs.receive_message(QueueUrl=queues['ocr'])['Messages'][0]['Body'])
        ocr.handler({'Records':[{'body':json.dumps(message)}]},None)
    assert item(tables['jobs'],job_id='new-job')['status']=='lookup_pending'
    monkeypatch.setattr(lookup,'build_catalog',lambda job_id:{'job_id':job_id,'entries':[]})
    lookup.process_job('new-job')
    assert item(tables['jobs'],job_id='new-job')['status']=='done'


def test_prepared_detection_survives_transaction_outage_without_redetection(pipeline,monkeypatch):
    tables,_,_,_,_,_,_=pipeline
    yolo,body=load_yolo(pipeline,monkeypatch)
    original=yolo.scan_tasks_table.meta.client.transact_write_items
    monkeypatch.setattr(yolo.scan_tasks_table.meta.client,'transact_write_items',lambda **_: (_ for _ in ()).throw(RuntimeError('transaction outage')))
    with pytest.raises(RuntimeError):
        yolo.process_task(body)
    assert item(tables['tasks'],job_id='new-job',task_id='batch')['state']=='prepared'
    assert item(tables['jobs'],job_id='new-job')['status']=='pending'
    tables['tasks'].update_item(Key={'job_id':'new-job','task_id':'batch'},UpdateExpression='SET lease_until = :expired',ExpressionAttributeValues={':expired':0})
    monkeypatch.setattr(yolo.scan_tasks_table.meta.client,'transact_write_items',original)
    monkeypatch.setattr(yolo,'process_frame',lambda *_args,**_kwargs: (_ for _ in ()).throw(AssertionError('must reuse manifest')))
    yolo.process_task(body)
    assert item(tables['jobs'],job_id='new-job')['ocr_total']==1
    assert item(tables['jobs'],job_id='new-job')['processed_frames']==1
