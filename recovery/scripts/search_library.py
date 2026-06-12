"""
ローカル図書DBを検索する。

使い方:
  uv run python scripts/search_library.py 君たちはどの主義で生きるか
  uv run python scripts/search_library.py 9784863102774 --isbn
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = "outputs/library/library.db"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ローカル図書DBを検索します。")
    parser.add_argument("query", help="検索語")
    parser.add_argument("--db", default=DEFAULT_DB, help="SQLite DB")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--isbn", action="store_true", help="ISBNとして検索する")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def resolve_path(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else REPO_ROOT / p


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).lower()
    return re.sub(r"[\s　・:：,，.．。『』「」\"'“”‘’!?！？\-‐‑‒–—―（）()【】\[\]]+", "", text)


def normalize_isbn(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"[^0-9xX]", "", str(value)).upper()


def score_text(query_norm: str, value_norm: str) -> float:
    if not query_norm or not value_norm:
        return 0.0
    if query_norm == value_norm:
        return 1.0
    if query_norm in value_norm:
        return min(0.98, 0.7 + len(query_norm) / len(value_norm) * 0.25)
    if value_norm in query_norm:
        if len(value_norm) >= 6:
            return min(0.96, 0.82 + len(value_norm) / len(query_norm) * 0.15)
        return min(0.94, 0.65 + len(value_norm) / len(query_norm) * 0.25)
    return SequenceMatcher(None, query_norm, value_norm).ratio()


def row_to_record(row: sqlite3.Row) -> dict[str, Any]:
    record = {
        "id": row["id"],
        "title": row["title"],
        "authors": row["authors"],
        "publisher": row["publisher"],
        "published_date": row["published_date"],
        "class_number": row["class_number"],
        "acquisition_type": row["acquisition_type"],
        "price": row["price"],
        "registration_number": row["registration_number"],
        "isbn": row["isbn"],
    }
    keys = set(row.keys())
    if "thumbnail" in keys:
        record["thumbnail"] = row["thumbnail"]
        record["info_link"] = row["info_link"]
        record["cover_matched_title"] = row["cover_matched_title"]
    return record


def search_library(
    db_path: Path,
    query: str,
    limit: int = 10,
    include_isbn: bool = False,
) -> list[dict[str, Any]]:
    query_norm = normalize_text(query)
    isbn_norm = normalize_isbn(query)
    isbn_mode = include_isbn and len(isbn_norm) >= 8

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    has_covers = bool(
        con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='book_covers'"
        ).fetchone()
    )
    select_sql = (
        """
        SELECT b.*, c.thumbnail, c.info_link, c.matched_title AS cover_matched_title
        FROM books b
        LEFT JOIN book_covers c ON c.book_id = b.id
        """
        if has_covers
        else "SELECT * FROM books"
    )
    like = f"%{query_norm}%"
    params: list[Any] = []
    where = []
    if query_norm and not isbn_mode:
        where.append(
            "(title_norm LIKE ? OR authors_norm LIKE ? OR publisher_norm LIKE ?)"
        )
        params.extend([like, like, like])
    if isbn_mode:
        where.append("isbn_norm LIKE ?")
        params.append(f"%{isbn_norm}%")

    if where:
        rows = con.execute(
            f"{select_sql} WHERE {' OR '.join(where)} LIMIT 200",
            params,
        ).fetchall()
    else:
        rows = []

    # LIKEで拾えないOCR揺れ対策に、全件を薄くfuzzy評価する。
    if len(rows) < limit and not isbn_mode:
        rows = con.execute(select_sql).fetchall()
    con.close()

    results = []
    seen = set()
    for row in rows:
        key = row["id"]
        if key in seen:
            continue
        seen.add(key)
        title_score = 0.0 if isbn_mode else score_text(query_norm, row["title_norm"] or "")
        author_score = 0.0 if isbn_mode else score_text(query_norm, row["authors_norm"] or "")
        publisher_score = 0.0 if isbn_mode else score_text(query_norm, row["publisher_norm"] or "")
        isbn_score = 0.0
        if isbn_mode and row["isbn_norm"]:
            isbn_score = 1.0 if isbn_norm == row["isbn_norm"] else (0.95 if isbn_norm in row["isbn_norm"] else 0.0)
        score = max(title_score, author_score * 0.9, publisher_score * 0.8, isbn_score)
        if score >= 0.35:
            record = row_to_record(row)
            record["score"] = score
            results.append(record)

    results.sort(key=lambda item: item["score"], reverse=True)
    return results[:limit]


def print_results(results: list[dict[str, Any]]) -> None:
    if not results:
        print("該当なし")
        return
    for i, item in enumerate(results, 1):
        author = f" / {item['authors']}" if item.get("authors") else ""
        publisher = f" / {item['publisher']}" if item.get("publisher") else ""
        isbn = f" / ISBN: {item['isbn']}" if item.get("isbn") else ""
        reg = f" / 登録番号: {item['registration_number']}" if item.get("registration_number") else ""
        cover = " / coverあり" if item.get("thumbnail") else ""
        print(f"{i:2d}. {item['title']}{author}{publisher}{isbn}{reg}{cover}")
        print(f"    score={item['score']:.3f}")
        if item.get("thumbnail"):
            print(f"    thumbnail={item['thumbnail']}")


def main() -> int:
    args = parse_args()
    db_path = resolve_path(args.db)
    results = search_library(db_path, args.query, args.limit, include_isbn=args.isbn)
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print_results(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
