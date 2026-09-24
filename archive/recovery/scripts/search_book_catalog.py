"""
build_book_catalog.py が作った catalog.json を検索する。

使い方:
  uv run python scripts/search_book_catalog.py "旅をする木"
  uv run python scripts/search_book_catalog.py "978" --field isbn
  uv run python scripts/search_book_catalog.py "add_tag:box_03" --field box
"""

from __future__ import annotations

import argparse
import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CATALOG = "outputs/book_catalog/catalog.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OCR済み蔵書catalogを検索します。")
    parser.add_argument("query", help="検索語")
    parser.add_argument("--catalog", default=DEFAULT_CATALOG, help="catalog JSON")
    parser.add_argument(
        "--field",
        choices=["all", "title", "author", "isbn", "box", "raw"],
        default="all",
        help="検索対象",
    )
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--min-score", type=float, default=0.25)
    parser.add_argument("--json", action="store_true", help="結果をJSONで出力")
    return parser.parse_args()


def resolve_path(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else REPO_ROOT / p


def normalize(text: Any) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", "", str(text)).lower()


def get_isbn_values(book: dict[str, Any]) -> list[str]:
    values = []
    # 旧catalog互換: 以前はOCRで visible_isbn を持たせていた。
    if book.get("visible_isbn"):
        values.append(str(book["visible_isbn"]))

    lookup = book.get("isbn_lookup")
    if isinstance(lookup, dict):
        for candidate in lookup.get("candidates", []):
            values.extend(str(isbn) for isbn in candidate.get("isbns", []))
    return values


def flatten_catalog(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for entry in catalog.get("entries", []):
        books = entry.get("books", [])
        if not books:
            rows.append(
                {
                    "box_id": entry.get("box_id"),
                    "source_image": entry.get("source_image"),
                    "crop_image": entry.get("crop_image"),
                    "title": None,
                    "author": None,
                    "publisher": None,
                    "isbn": [],
                    "raw_text": None,
                    "ocr_confidence": None,
                    "detector_confidence": entry.get("detector_confidence"),
                }
            )
            continue

        for book in books:
            rows.append(
                {
                    "box_id": entry.get("box_id"),
                    "source_image": entry.get("source_image"),
                    "crop_image": entry.get("crop_image"),
                    "title": book.get("title"),
                    "author": book.get("author"),
                    "publisher": book.get("publisher"),
                    "isbn": get_isbn_values(book),
                    "raw_text": book.get("raw_text"),
                    "ocr_confidence": book.get("ocr_confidence"),
                    "detector_confidence": entry.get("detector_confidence"),
                }
            )
    return rows


def score_text(query: str, value: Any) -> float:
    q = normalize(query)
    v = normalize(value)
    if not q or not v:
        return 0.0
    if q in v:
        return min(1.0, 0.7 + len(q) / max(len(v), 1) * 0.3)
    return SequenceMatcher(None, q, v).ratio()


def score_row(query: str, row: dict[str, Any], field: str) -> float:
    if field == "title":
        return score_text(query, row.get("title"))
    if field == "author":
        return score_text(query, row.get("author"))
    if field == "raw":
        return score_text(query, row.get("raw_text"))
    if field == "box":
        return score_text(query, row.get("box_id"))
    if field == "isbn":
        return max((score_text(query, isbn) for isbn in row.get("isbn", [])), default=0.0)

    values = [
        row.get("title"),
        row.get("author"),
        row.get("raw_text"),
        row.get("box_id"),
        *row.get("isbn", []),
    ]
    return max(score_text(query, value) for value in values)


def search(
    catalog: dict[str, Any],
    query: str,
    field: str,
    min_score: float,
    limit: int,
) -> list[dict[str, Any]]:
    rows = flatten_catalog(catalog)
    scored = []
    for row in rows:
        score = score_row(query, row, field)
        if score >= min_score:
            scored.append({"score": score, **row})
    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored[:limit]


def print_results(results: list[dict[str, Any]]) -> None:
    if not results:
        print("該当なし")
        return

    for i, row in enumerate(results, 1):
        title = row.get("title") or "(OCR未実行/タイトルなし)"
        author = f" / {row['author']}" if row.get("author") else ""
        isbn = f" / ISBN: {', '.join(row['isbn'])}" if row.get("isbn") else ""
        print(f"{i:2d}. {title}{author}{isbn}")
        print(f"    score={row['score']:.3f} box={row.get('box_id')} crop={row.get('crop_image')}")


def main() -> int:
    args = parse_args()
    catalog_path = resolve_path(args.catalog)
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    results = search(catalog, args.query, args.field, args.min_score, args.limit)

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print_results(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
