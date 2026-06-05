#!/usr/bin/env bash
# E2E: 1枚の画像を POST → catalog が出るまでポーリング
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
IMAGE="${1:-$ROOT/Picture/PXL_20260424_022009671.MP.jpg}"

if [[ ! -f "$IMAGE" ]]; then
  echo "image not found: $IMAGE" >&2
  exit 1
fi

API=$(aws cloudformation describe-stacks --stack-name booksearch \
  --query "Stacks[0].Outputs[?OutputKey=='ApiEndpoint'].OutputValue" --output text)
echo "API=$API"

# 画像を base64 にして JSON で POST
B64=$(base64 -i "$IMAGE")
FILENAME=$(basename "$IMAGE")
PAYLOAD=$(jq -n --arg fn "$FILENAME" --arg b "$B64" '{filename:$fn, content_base64:$b}')

echo "→ POST /api/scan"
RESP=$(curl -s -X POST "$API/api/scan" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD")
echo "$RESP"
JOB_ID=$(echo "$RESP" | jq -r .job_id)
if [[ -z "$JOB_ID" || "$JOB_ID" == "null" ]]; then
  echo "scan failed" >&2; exit 1
fi
echo "job_id=$JOB_ID"

# ポーリング
for i in {1..60}; do
  sleep 5
  echo "--- poll #$i ---"
  STATUS_RESP=$(curl -s "$API/api/jobs/$JOB_ID")
  STATUS=$(echo "$STATUS_RESP" | jq -r .status)
  echo "status=$STATUS"
  if [[ "$STATUS" == "done" || "$STATUS" == "failed" ]]; then
    echo "$STATUS_RESP" | jq '.'
    if [[ "$STATUS" == "done" ]]; then
      BOOK_COUNT=$(echo "$STATUS_RESP" | jq '[.catalog.entries[]?.books[]?.title] | length')
      echo "✓ done: $BOOK_COUNT book titles extracted"
    fi
    exit 0
  fi
done
echo "timeout" >&2
exit 1
