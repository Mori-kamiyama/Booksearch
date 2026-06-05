"""
PP-OCRv5（PaddleOCR）で画像からテキストを抽出するベンチマーク用スクリプト。
回転・縦書き対応の text spotting 系。

使い方:
  uv run python scripts/ppocr_bench.py outputs/add_tag/add_tag_warped.jpg
  uv run python scripts/ppocr_bench.py data/add_tag.jpg --output-dir outputs/add_tag_ppocr
"""

import argparse
import json
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PP-OCRv5 で画像からテキストを抽出します。")
    parser.add_argument("image", help="入力画像パス")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--lang", default="japan", help="言語（デフォルト: japan）")
    parser.add_argument("--min-score", type=float, default=0.5)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    image_path = REPO_ROOT / args.image
    if not image_path.exists():
        raise FileNotFoundError(f"画像が見つかりません: {image_path}")

    stem = image_path.stem
    output_dir = REPO_ROOT / (args.output_dir or f"outputs/{stem}_ppocr")
    output_dir.mkdir(parents=True, exist_ok=True)

    from paddleocr import PaddleOCR

    print(f"PP-OCRv5 初期化中 (lang={args.lang})")
    ocr = PaddleOCR(
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=True,
        lang=args.lang,
    )

    print(f"推論中: {image_path.name}")
    t0 = time.perf_counter()
    result = ocr.predict(str(image_path))
    elapsed = time.perf_counter() - t0

    strings = []
    if result:
        for item in result:
            if hasattr(item, 'rec_texts'):
                for text, score in zip(item.rec_texts, item.rec_scores):
                    if text and len(text.strip()) >= 2 and score >= args.min_score:
                        strings.append(text.strip())
            elif isinstance(item, list):
                for line in item:
                    if isinstance(line, list) and len(line) >= 2:
                        text_info = line[1]
                        if isinstance(text_info, (list, tuple)) and len(text_info) >= 2:
                            text, score = text_info[0], text_info[1]
                            if text and len(text.strip()) >= 2 and score >= args.min_score:
                                strings.append(text.strip())

    print(f"\n推論時間: {elapsed:.1f}s")
    print(f"抽出テキスト数: {len(strings)}")
    for s in strings:
        print(f"  {s}")

    payload = {
        "model": "PP-OCRv5",
        "image": str(image_path),
        "elapsed_sec": round(elapsed, 2),
        "strings": strings,
    }
    out_path = output_dir / "summary.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nsummary_json: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
