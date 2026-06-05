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
cp "$YOLO_MODEL_SOURCE" "$YOLO_ASSETS/yolo_model.pt"
# AprilTag mapping は example のものを「最初の JSON object だけ」抜く
# (元ファイルは JSON object が2つ連続している)
uv run python - <<'PY'
import json, pathlib, re, sys
src = pathlib.Path("scripts/apriltag_shelf_map.example.json").read_text(encoding="utf-8")
# 連結 JSON のうち最初の 36h11 マッピングを採用
decoder = json.JSONDecoder()
obj, _ = decoder.raw_decode(src.lstrip())
pathlib.Path("aws/functions/yolo_worker/assets/apriltag_shelf_map.json").write_text(
    json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
print("  apriltag_shelf_map.json:", obj.get("dictionary"))
PY
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
