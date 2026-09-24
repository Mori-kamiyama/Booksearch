"""
棚検知専用スクリプト。OCR は行わず、棚の区画検出のみを実施する。

Gemini API と OpenRouter (OpenAI互換) の両方に対応。

使い方:
  # Gemini
  python scripts/detect_shelves.py --image data/add_tag.jpg --model gemini-3.1-flash-lite

  # OpenRouter
  python scripts/detect_shelves.py --image data/shelf_2.jpg --model google/gemma-4-31b-it --backend openrouter
  python scripts/detect_shelves.py --image data/shelf_3.jpg --model qwen/qwen3-vl-8b-instruct --backend openrouter
"""

import argparse
import base64
import json
import os
from pathlib import Path

import cv2
import numpy as np


REPO_ROOT = Path(__file__).resolve().parent.parent

SHELF_DETECTION_PROMPT = """\
この本棚の画像から、本が置かれている棚の区画をすべて見つけ、
各区画の四隅の座標を返してください。

回答を JSON 配列で返してください。以下の形式EXACTLY を使用してください:
[
  [[0.10, 0.20], [0.85, 0.18], [0.86, 0.48], [0.09, 0.50]],
  [[0.09, 0.50], [0.86, 0.48], [0.87, 0.80], [0.08, 0.82]]
]

ルール:
- 外側の配列の各要素が1つの棚区画
- 各区画は [[左上], [右上], [右下], [左下]] の順で4点
- 各点は [x, y] の形式（0.0=左端/上端、1.0=右端/下端）
- カメラの角度で台形・平行四辺形になる場合はその通りに返す
- 本が実際に並んでいる区画のみ対象（空の棚は除外してよい）
- JSON 配列 ONLY。説明文は一切不要。
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="本棚の区画検出（OCRなし）")
    parser.add_argument("--image", required=True, help="入力画像パス")
    parser.add_argument("--model", required=True, help="モデル ID")
    parser.add_argument(
        "--backend",
        choices=["gemini", "openrouter"],
        default="gemini",
        help="使用するバックエンド (gemini / openrouter)",
    )
    parser.add_argument("--output-dir", default=None, help="出力先ディレクトリ（省略時は自動生成）")
    return parser.parse_args()


# ── Gemini backend ────────────────────────────────────────────────────────────

def detect_with_gemini(model: str, image_path: Path) -> list[dict]:
    from google import genai
    from google.genai import types

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY 環境変数が未設定です。")

    client = genai.Client(api_key=api_key)
    image_bytes = image_path.read_bytes()
    suffix = image_path.suffix.lower()
    mime = "image/jpeg" if suffix in (".jpg", ".jpeg") else "image/png"

    response = client.models.generate_content(
        model=model,
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type=mime),
            SHELF_DETECTION_PROMPT,
        ],
    )
    from PIL import Image as PILImage
    img = PILImage.open(image_path)
    return _parse_shelf_quads(response.text, (img.height, img.width))


# ── OpenRouter backend ────────────────────────────────────────────────────────

def detect_with_openrouter(model: str, image_path: Path) -> list[dict]:
    from openai import OpenAI
    from PIL import Image as PILImage

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY 環境変数が未設定です。")

    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)

    image_bytes = image_path.read_bytes()
    suffix = image_path.suffix.lower()
    mime = "image/jpeg" if suffix in (".jpg", ".jpeg") else "image/png"
    b64 = base64.b64encode(image_bytes).decode()
    data_url = f"data:{mime};base64,{b64}"

    response = client.chat.completions.create(
        model=model,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "text", "text": SHELF_DETECTION_PROMPT},
            ],
        }],
    )
    img = PILImage.open(image_path)
    return _parse_shelf_quads(response.choices[0].message.content, (img.height, img.width))


# ── 共通ユーティリティ ─────────────────────────────────────────────────────────

def _parse_shelf_quads(text: str, image_shape: tuple) -> list[dict]:
    """VLM の応答から棚区画の quad リストをパースする。"""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    height, width = image_shape[:2]
    # JSON後の余分なテキストを無視するため raw_decode を使用
    decoder = json.JSONDecoder()
    raw, _ = decoder.raw_decode(text)
    shelves = []
    for item in raw:
        # [[x,y]×4] 形式
        pts = item if isinstance(item, list) else item.get("quad", item.get("corners", []))
        if len(pts) != 4:
            continue
        # 座標 → ピクセル座標（先頭2要素のみ使用）
        # x > 1.0 → すでにピクセル座標、x <= 1.0 → 正規化座標 → ピクセルに変換
        quad = []
        for pt in pts:
            x, y = pt[0], pt[1]
            px = x if x > 1.0 else x * width
            py = y if y > 1.0 else y * height
            quad.append([px, py])

        tl, tr, br, bl = quad
        avg_w = int(((tr[0] - tl[0]) + (br[0] - bl[0])) / 2)
        avg_h = int(((bl[1] - tl[1]) + (br[1] - tr[1])) / 2)
        shelves.append({"quad": quad, "width": avg_w, "height": avg_h})
    return shelves


def warp_quad(image: np.ndarray, quad: list) -> np.ndarray:
    """台形・四角形を透視変換で矩形に補正する。"""
    pts = np.array(quad, dtype=np.float32)
    tl, tr, br, bl = pts
    w = int(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl)))
    h = int(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)))
    w, h = max(w, 1), max(h, 1)
    dst = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float32)
    M = cv2.getPerspectiveTransform(pts, dst)
    return cv2.warpPerspective(image, M, (w, h))


def draw_result(
    image: np.ndarray,
    intersections: list[dict],
    shelves: list[dict],
    output_path: Path,
) -> None:
    viz = image.copy()
    height, width = image.shape[:2]

    for inter in intersections:
        x = int(inter["x"] * width if inter["x"] <= 1.0 else inter["x"])
        y = int(inter["y"] * height if inter["y"] <= 1.0 else inter["y"])
        cv2.circle(viz, (x, y), 10, (0, 0, 255), -1)
        cv2.circle(viz, (x, y), 14, (0, 0, 255), 3)

    for i, shelf in enumerate(shelves):
        pts = np.array(shelf["quad"], dtype=np.int32)
        cv2.polylines(viz, [pts], isClosed=True, color=(0, 255, 0), thickness=4)
        tl = pts[0]
        cv2.putText(viz, f"Shelf {i+1}  {shelf['width']}x{shelf['height']}",
                    (tl[0] + 8, tl[1] + 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 0), 3)

    cv2.imwrite(str(output_path), viz)


def main() -> int:
    args = parse_args()
    image_path = REPO_ROOT / args.image

    if not image_path.exists():
        raise FileNotFoundError(f"画像が見つかりません: {image_path}")

    # 出力先: outputs/shelf_detection/{image_stem}/{model_slug}/
    model_slug = args.model.replace("/", "_").replace(":", "_")
    if args.output_dir:
        output_dir = REPO_ROOT / args.output_dir
    else:
        output_dir = REPO_ROOT / "outputs" / "shelf_detection" / image_path.stem / model_slug
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"画像   : {image_path.name}")
    print(f"モデル : {args.model}  [{args.backend}]")
    print(f"出力   : {output_dir}")

    image = cv2.imread(str(image_path))
    if image is None:
        raise RuntimeError(f"画像を読み込めません: {image_path}")

    print("\n棚の区画を検出中...")
    if args.backend == "gemini":
        shelves = detect_with_gemini(args.model, image_path)
    else:
        shelves = detect_with_openrouter(args.model, image_path)

    print(f"棚区画: {len(shelves)}個")
    for i, s in enumerate(shelves):
        tl, tr, br, bl = s["quad"]
        print(f"  {i+1}. TL({tl[0]:.0f},{tl[1]:.0f}) TR({tr[0]:.0f},{tr[1]:.0f}) "
              f"BR({br[0]:.0f},{br[1]:.0f}) BL({bl[0]:.0f},{bl[1]:.0f})  ({s['width']}x{s['height']})")

    viz_path = output_dir / "shelf_detection.jpg"
    draw_result(image, [], shelves, viz_path)
    print(f"\n可視化: {viz_path}")

    # 各区画を透視変換で補正して保存
    warped_dir = output_dir / "warped"
    warped_dir.mkdir(exist_ok=True)
    for i, shelf in enumerate(shelves):
        warped = warp_quad(image, shelf["quad"])
        warped_path = warped_dir / f"shelf_{i+1:02d}.jpg"
        cv2.imwrite(str(warped_path), warped)
        shelf["warped_image"] = str(warped_path)
    print(f"補正画像: {warped_dir}/shelf_*.jpg")

    result = {
        "image": str(image_path),
        "model": args.model,
        "backend": args.backend,
        "shelves": shelves,
    }
    json_path = output_dir / "result.json"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"JSON  : {json_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
