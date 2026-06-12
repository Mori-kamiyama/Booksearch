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
cp "$ROOT/runs/detect/runs/picture_box_detection/yolo11n_quick/weights/best.pt" "$YOLO_ASSETS/yolo_model.pt"
# AprilTag mapping は example のものを「最初の JSON object だけ」抜く
# (元ファイルは JSON object が2つ連続している)
python3 - <<'PY'
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
cp "$ROOT/outputs/library/library.db" "$LOOKUP_ASSETS/library.db"
cp "$ROOT/data/known_books.json" "$LOOKUP_ASSETS/known_books.json"

echo "✓ assets ready"
ls -la "$YOLO_ASSETS" "$LOOKUP_ASSETS"

# Go API: local book search uses a bundled read-only SQLite DB.
echo "→ Go API assets"
cp "$ROOT/outputs/library/library.db" "$GO_API_DIR/library.db"

echo "✓ assets ready"
ls -la "$YOLO_ASSETS" "$LOOKUP_ASSETS" "$GO_API_DIR/library.db"
