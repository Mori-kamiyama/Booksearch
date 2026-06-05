"""
yomitoku 0.13.0 の OCR クラスで画像からテキストを抽出するベンチマーク用スクリプト。

使い方:
  uv run python scripts/yomitoku_bench.py outputs/add_tag/add_tag_warped.jpg
  uv run python scripts/yomitoku_bench.py data/add_tag.jpg --output-dir outputs/add_tag_yomitoku13
"""

import argparse
import json
import time
from pathlib import Path

import cv2


REPO_ROOT = Path(__file__).resolve().parent.parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="yomitoku OCR クラスでテキストを抽出します。")
    parser.add_argument("image", help="入力画像パス")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--device", default="mps", choices=("mps", "cpu", "cuda"))
    parser.add_argument("--min-score", type=float, default=0.5, help="認識スコアの閾値")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    image_path = REPO_ROOT / args.image
    if not image_path.exists():
        raise FileNotFoundError(f"画像が見つかりません: {image_path}")

    stem = image_path.stem
    output_dir = REPO_ROOT / (args.output_dir or f"outputs/{stem}_yomitoku13")
    output_dir.mkdir(parents=True, exist_ok=True)

    from yomitoku import OCR

    print(f"yomitoku OCR 初期化中 (device={args.device})")
    ocr = OCR(device=args.device, visualize=False)

    img = cv2.imread(str(image_path))
    if img is None:
        raise ValueError(f"画像を読み込めません: {image_path}")

    print(f"推論中: {image_path.name}")
    t0 = time.perf_counter()
    results, _ = ocr(img)
    elapsed = time.perf_counter() - t0

    strings = [
        w.content
        for w in results.words
        if w.content and len(w.content.strip()) >= 2 and w.rec_score >= args.min_score
    ]

    print(f"\n推論時間: {elapsed:.1f}s")
    print(f"抽出テキスト数: {len(strings)}")
    for s in strings:
        print(f"  {s}")

    payload = {
        "model": f"yomitoku=={__import__('yomitoku').__version__}",
        "image": str(image_path),
        "device": args.device,
        "elapsed_sec": round(elapsed, 2),
        "strings": strings,
    }
    out_path = output_dir / "summary.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nsummary_json: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
