#!/usr/bin/env bash
# Container イメージに同梱するアセットを assets/ ディレクトリに配置する。
# sam build の直前に呼ぶ。

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
AWS_DIR="$ROOT/aws"

YOLO_ASSETS="$AWS_DIR/functions/yolo_worker/assets"
LOOKUP_ASSETS="$AWS_DIR/functions/lookup_worker/assets"

mkdir -p "$YOLO_ASSETS" "$LOOKUP_ASSETS"

# YOLO Worker: モデル + AprilTag mapping + known_books
echo "→ YOLO Worker assets"
YOLO_MODEL_SOURCE="$ROOT/aws/functions/yolo_worker/assets/yolo_model.pt"
if [[ ! -f "$YOLO_MODEL_SOURCE" ]]; then
  YOLO_MODEL_SOURCE="$ROOT/runs/detect/runs/picture_box_detection/yolo11n_quick/weights/best.pt"
fi
if [[ "$(cd "$(dirname "$YOLO_MODEL_SOURCE")" && pwd)/$(basename "$YOLO_MODEL_SOURCE")" != "$(cd "$(dirname "$YOLO_ASSETS/yolo_model.pt")" && pwd)/$(basename "$YOLO_ASSETS/yolo_model.pt")" ]]; then
  cp "$YOLO_MODEL_SOURCE" "$YOLO_ASSETS/yolo_model.pt"
else
  echo "  yolo_model.pt: already in place"
fi
# 既存のworker用マッピングがあればそれを保持する。初回だけ、互換形式の
# data/apriltag_shelf_map.json を配置する（旧exampleファイルには依存しない）。
if [[ ! -f "$YOLO_ASSETS/apriltag_shelf_map.json" ]]; then
  cp "$ROOT/data/apriltag_shelf_map.json" "$YOLO_ASSETS/apriltag_shelf_map.json"
fi
cp "$ROOT/data/known_books.json" "$YOLO_ASSETS/known_books.json"

# Lookup Worker: library.db + known_books
echo "→ Lookup Worker assets"
LIBRARY_DB_SOURCE="$ROOT/outputs/library/library.db"
if [[ ! -f "$LIBRARY_DB_SOURCE" ]]; then
  LIBRARY_DB_SOURCE="$ROOT/aws/functions/go_api/library.db"
fi
cp "$LIBRARY_DB_SOURCE" "$LOOKUP_ASSETS/library.db"
cp "$ROOT/data/known_books.json" "$LOOKUP_ASSETS/known_books.json"

echo "✓ assets ready"
ls -la "$YOLO_ASSETS" "$LOOKUP_ASSETS"
