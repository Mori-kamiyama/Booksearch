#!/usr/bin/env bash
# Go API /api/shelves が返す mapping を S3 に置く（フロント参照用）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BUCKET=$(aws cloudformation describe-stacks --stack-name booksearch \
  --query "Stacks[0].Outputs[?OutputKey=='BucketName'].OutputValue" --output text)
echo "BUCKET=$BUCKET"
aws s3 cp "$ROOT/aws/functions/yolo_worker/assets/apriltag_shelf_map.json" \
  "s3://$BUCKET/assets/apriltag_shelf_map.json"
