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

# 画像を base64 にして JSON で POST。
# 大きい画像を jq --arg で渡すと argv 上限に当たるため、一時ファイルに書く。
PAYLOAD_FILE=$(mktemp)
trap 'rm -f "$PAYLOAD_FILE"' EXIT
python3 - "$IMAGE" "$PAYLOAD_FILE" <<'PY'
import base64
import json
import pathlib
import sys

image = pathlib.Path(sys.argv[1])
payload_path = pathlib.Path(sys.argv[2])
payload = {
    "filename": image.name,
    "content_base64": base64.b64encode(image.read_bytes()).decode("ascii"),
}
payload_path.write_text(json.dumps(payload), encoding="utf-8")
PY

echo "→ POST /api/scan"
RESP=$(curl -s -X POST "$API/api/scan" \
  -H "Content-Type: application/json" \
  --data-binary "@$PAYLOAD_FILE")
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
