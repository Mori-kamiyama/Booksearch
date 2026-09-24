#!/usr/bin/env bash
# Container イメージに同梱するアセットを assets/ ディレクトリに配置する。
# sam build の直前に呼ぶ。

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
AWS_DIR="$ROOT/app/aws"

YOLO_ASSETS="$AWS_DIR/functions/yolo_worker/assets"
LOOKUP_ASSETS="$AWS_DIR/functions/lookup_worker/assets"

mkdir -p "$YOLO_ASSETS" "$LOOKUP_ASSETS"

# YOLO Worker: モデル + AprilTag mapping + known_books
echo "→ YOLO Worker assets"
YOLO_MODEL_SOURCE="$ROOT/app/aws/functions/yolo_worker/assets/yolo_model.pt"
if [[ ! -f "$YOLO_MODEL_SOURCE" ]]; then
  YOLO_MODEL_SOURCE="$ROOT/research/runs/detect/runs/picture_box_detection/yolo11n_quick/weights/best.pt"
fi
cp "$YOLO_MODEL_SOURCE" "$YOLO_ASSETS/yolo_model.pt"
cp "$ROOT/assets/data/apriltag_shelf_map.json" "$YOLO_ASSETS/apriltag_shelf_map.json"
cp "$ROOT/assets/data/known_books.json" "$YOLO_ASSETS/known_books.json"

# Lookup Worker: library.db + known_books
echo "→ Lookup Worker assets"
LIBRARY_DB_SOURCE="$ROOT/research/outputs/library/library.db"
if [[ ! -f "$LIBRARY_DB_SOURCE" ]]; then
  LIBRARY_DB_SOURCE="$ROOT/app/aws/functions/go_api/library.db"
fi
cp "$LIBRARY_DB_SOURCE" "$LOOKUP_ASSETS/library.db"
cp "$ROOT/assets/data/known_books.json" "$LOOKUP_ASSETS/known_books.json"

echo "✓ assets ready"
ls -la "$YOLO_ASSETS" "$LOOKUP_ASSETS"
