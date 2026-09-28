#!/usr/bin/env python3
"""Remove only old image objects with no owner/reference; dry-run by default.

Apply requires S3 versioning and uses conditional delete markers so content
remains recoverable. Never deletes object versions or catalog/featured data.
"""
import argparse
from datetime import datetime, timezone, timedelta
import json
import re
import boto3

UUID = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
OWNER = re.compile(rf'^(uploads|crops|live)/({UUID})/')


def referenced_jobs(value):
    if isinstance(value, str):
        return set(re.findall(UUID, value.lower()))
    if isinstance(value, dict):
        return set().union(*(referenced_jobs(v) for v in value.values())) if value else set()
    if isinstance(value, (list, set, tuple)):
        return set().union(*(referenced_jobs(v) for v in value)) if value else set()
    return set()


def collect_references(ddb, table_names):
    found = set()
    for table_name in table_names:
        table = ddb.Table(table_name)
        args = {'ConsistentRead': True}
        while True:
            result = table.scan(**args)
            for item in result.get('Items', []):
                found.update(referenced_jobs(item))
            if not result.get('LastEvaluatedKey'):
                break
            args['ExclusiveStartKey'] = result['LastEvaluatedKey']
    return found


def candidate(obj, references, cutoff):
    match = OWNER.match(obj['Key'])
    return bool(match and match.group(2) not in references and obj['LastModified'] < cutoff)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bucket', required=True)
    parser.add_argument('--project', default='booksearch')
    parser.add_argument('--region', default='ap-northeast-1')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    session = boto3.Session(region_name=args.region)
    s3, ddb = session.client('s3'), session.resource('dynamodb')
    tables = [args.project + '-' + suffix for suffix in [
        'jobs', 'scan-tasks', 'crops', 'crop-fingerprints', 'shelf-observations', 'shelf-candidates']]
    references = collect_references(ddb, tables)
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    objects = []
    for prefix in ['uploads/', 'crops/', 'live/']:
        for page in s3.get_paginator('list_objects_v2').paginate(Bucket=args.bucket, Prefix=prefix):
            objects.extend(obj for obj in page.get('Contents', []) if candidate(obj, references, cutoff))
    if args.apply:
        if s3.get_bucket_versioning(Bucket=args.bucket).get('Status') != 'Enabled':
            raise RuntimeError('Versioning must be enabled before deleting any current object')
        # Fail closed if any table cannot be read. Recheck references immediately
        # before mutation; existing job ownership blocks deletion entirely.
        references = collect_references(ddb, tables)
    results = []
    for obj in objects:
        if not candidate(obj, references, cutoff):
            continue
        record = {'key': obj['Key'], 'etag': obj['ETag'], 'bytes': obj['Size']}
        if args.apply:
            owner = OWNER.match(obj['Key']).group(2)
            if ddb.Table(args.project + '-jobs').get_item(Key={'job_id': owner}, ConsistentRead=True).get('Item'):
                continue
            result = s3.delete_object(Bucket=args.bucket, Key=obj['Key'], IfMatch=obj['ETag'])
            if not result.get('DeleteMarker') or not result.get('VersionId'):
                raise RuntimeError('Expected recoverable versioned delete marker')
            record['delete_marker_version'] = result['VersionId']
        results.append(record)
    print(json.dumps({'mode': 'apply' if args.apply else 'dry-run', 'objects': results, 'count': len(results)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
