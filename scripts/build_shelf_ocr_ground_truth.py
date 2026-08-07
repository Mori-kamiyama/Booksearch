"""
book_catalog_data_260702/catalog.json から OCRベンチマーク用の正解データを生成する。
library_db との照合スコアが高い（誤読の可能性が低い）タイトルだけを正解として採用する。

使い方:
  uv run python scripts/build_shelf_ocr_ground_truth.py
"""

import argparse
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = REPO_ROOT / "outputs/book_catalog_data_260702/catalog.json"
CROPS_DIR = REPO_ROOT / "outputs/book_catalog_data_260702/crops"
OUTPUT_PATH = REPO_ROOT / "benchmark/ground_truth_shelf.json"
MIN_SCORE = 0.95


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="棚OCRベンチマーク用の正解データを生成します。")
    parser.add_argument("--min-score", type=float, default=MIN_SCORE, help="採用する library_db 照合スコアの下限")
    parser.add_argument("--output", default=str(OUTPUT_PATH), help="出力先パス")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))

    samples = []
    for entry in catalog["entries"]:
        crop_name = Path(entry["crop_image"]).name
        crop_path = CROPS_DIR / crop_name
        if not crop_path.exists():
            continue

        titles = []
        for book in entry.get("books", []):
            candidates = book.get("book_lookup", {}).get("candidates", [])
            if not candidates:
                continue
            top = candidates[0]
            if top.get("match_confidence") == "auto" and top.get("score", 0) >= args.min_score:
                titles.append(top["title"])

        if titles:
            samples.append({"image": f"outputs/book_catalog_data_260702/crops/{crop_name}", "books": titles})

    payload = {
        "source": "outputs/book_catalog_data_260702/catalog.json",
        "min_score": args.min_score,
        "note": "library_dbとの照合スコアが高いタイトルのみを正解として採用（Gemini 3.1 Flash Lite Previewによる一次OCR結果の検証済みサブセット）",
        "samples": samples,
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    n_books = sum(len(s["books"]) for s in samples)
    print(f"画像数: {len(samples)}  正解タイトル数: {n_books}")
    print(f"保存: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
