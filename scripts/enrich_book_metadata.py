"""Fetch and cache Google Books metadata for a local SQLite catalogue.

Examples:
  uv run python scripts/enrich_book_metadata.py --db path/to/library.db --limit 10 --dry-run
  GOOGLE_BOOKS_API_KEY=... uv run python scripts/enrich_book_metadata.py --db path/to/library.db --limit 100

The script only calls Google Books when it is explicitly run. Exact ISBN matches
are accepted; title/author matches need a conservative similarity threshold.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from recommendation_utils import metadata_match_score, normalize_isbn

REPO_ROOT = Path(__file__).resolve().parent.parent
GOOGLE_BOOKS_URL = "https://www.googleapis.com/books/v1/volumes"


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


def create_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS book_metadata (
            book_id INTEGER PRIMARY KEY,
            description TEXT,
            categories_json TEXT NOT NULL DEFAULT '[]',
            page_count INTEGER,
            language TEXT,
            source TEXT NOT NULL DEFAULT 'google_books',
            source_id TEXT,
            match_method TEXT,
            match_score REAL,
            fetched_at TEXT NOT NULL,
            fetch_status TEXT NOT NULL,
            error TEXT,
            FOREIGN KEY (book_id) REFERENCES books(id)
        )
        """
    )


def google_request(query: str, timeout: float) -> dict[str, Any]:
    parameters = {"q": query, "maxResults": "5", "printType": "books"}
    api_key = os.getenv("GOOGLE_BOOKS_API_KEY")
    if api_key:
        parameters["key"] = api_key
    url = f"{GOOGLE_BOOKS_URL}?{urllib.parse.urlencode(parameters)}"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def volume_isbns(volume_info: dict[str, Any]) -> list[str]:
    return [str(item.get("identifier") or "") for item in volume_info.get("industryIdentifiers") or []]


def select_volume(book: sqlite3.Row, volumes: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, float, str]:
    best: tuple[dict[str, Any], float, str] | None = None
    for volume in volumes:
        info = volume.get("volumeInfo") or {}
        score, method = metadata_match_score(
            local_title=str(book["title"] or ""),
            local_authors=str(book["authors"] or ""),
            local_isbn=str(book["isbn"] or ""),
            candidate_title=str(info.get("title") or ""),
            candidate_authors=[str(value) for value in info.get("authors") or []],
            candidate_isbns=volume_isbns(info),
        )
        if best is None or score > best[1]:
            best = (volume, score, method)
    if best is None:
        return None, 0.0, "no_candidate"

    volume, score, method = best
    # ISBN matches are unambiguous. A title+author result must be strong enough
    # to avoid storing a different edition or a similarly named work.
    accepted = method == "isbn_exact" or (method == "title_author" and score >= 0.78) or (method == "title_only" and score >= 0.92)
    return (volume if accepted else None), score, method


def query_for_book(book: sqlite3.Row) -> str:
    isbn = normalize_isbn(str(book["isbn"] or ""))
    if isbn:
        return f"isbn:{isbn}"
    title = str(book["title"] or "").strip()
    authors = str(book["authors"] or "").strip()
    if not title:
        return ""
    return f'intitle:"{title}"' + (f' inauthor:"{authors}"' if authors else "")


def upsert_metadata(
    connection: sqlite3.Connection,
    book_id: int,
    *,
    status: str,
    fetched_at: str,
    description: str | None = None,
    categories: list[str] | None = None,
    page_count: int | None = None,
    language: str | None = None,
    source_id: str | None = None,
    match_method: str | None = None,
    match_score: float | None = None,
    error: str | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO book_metadata (
            book_id, description, categories_json, page_count, language, source,
            source_id, match_method, match_score, fetched_at, fetch_status, error
        ) VALUES (?, ?, ?, ?, ?, 'google_books', ?, ?, ?, ?, ?, ?)
        ON CONFLICT(book_id) DO UPDATE SET
            description=excluded.description, categories_json=excluded.categories_json,
            page_count=excluded.page_count, language=excluded.language, source=excluded.source,
            source_id=excluded.source_id, match_method=excluded.match_method,
            match_score=excluded.match_score, fetched_at=excluded.fetched_at,
            fetch_status=excluded.fetch_status, error=excluded.error
        """,
        (book_id, description, json.dumps(categories or [], ensure_ascii=False), page_count, language, source_id, match_method, match_score, fetched_at, status, error),
    )


def enrich_book(connection: sqlite3.Connection, book: sqlite3.Row, timeout: float, dry_run: bool) -> str:
    fetched_at = datetime.now(timezone.utc).isoformat()
    query = query_for_book(book)
    if not query:
        if not dry_run:
            upsert_metadata(connection, int(book["id"]), status="not_found", fetched_at=fetched_at, error="missing title and ISBN")
        return "not_found"
    try:
        payload = google_request(query, timeout)
    except urllib.error.HTTPError as error:
        status = "temporary_error" if error.code == 429 or 500 <= error.code < 600 else "error"
        if not dry_run:
            upsert_metadata(connection, int(book["id"]), status=status, fetched_at=fetched_at, error=f"google books status {error.code}")
        return status
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        if not dry_run:
            upsert_metadata(connection, int(book["id"]), status="temporary_error", fetched_at=fetched_at, error=str(error))
        return "temporary_error"

    volume, score, method = select_volume(book, list(payload.get("items") or []))
    if volume is None:
        if not dry_run:
            upsert_metadata(connection, int(book["id"]), status="ambiguous" if payload.get("items") else "not_found", fetched_at=fetched_at, match_method=method, match_score=score)
        return "ambiguous" if payload.get("items") else "not_found"

    info = volume.get("volumeInfo") or {}
    page_count = info.get("pageCount")
    if not isinstance(page_count, int):
        page_count = None
    if not dry_run:
        upsert_metadata(
            connection,
            int(book["id"]),
            status="matched",
            fetched_at=fetched_at,
            description=str(info.get("description") or "") or None,
            categories=[str(value) for value in info.get("categories") or []],
            page_count=page_count,
            language=str(info.get("language") or "") or None,
            source_id=str(volume.get("id") or "") or None,
            match_method=method,
            match_score=score,
        )
    return "matched"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="SQLite catalogue DB path")
    parser.add_argument("--limit", type=int, default=100, help="maximum books to process (default: 100)")
    parser.add_argument("--book-id", type=int, action="append", help="only process this book ID; may be repeated")
    parser.add_argument("--delay", type=float, default=0.5, help="seconds between requests (default: 0.5)")
    parser.add_argument("--timeout", type=float, default=10, help="HTTP timeout seconds (default: 10)")
    parser.add_argument("--refresh", action="store_true", help="also retry rows already marked matched")
    parser.add_argument("--dry-run", action="store_true", help="perform requests but do not change the DB")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = resolve_path(args.db)
    if not db_path.is_file():
        print(f"DBが見つかりません: {db_path}", file=sys.stderr)
        return 2
    if args.limit <= 0 or args.delay < 0 or args.timeout <= 0:
        print("--limit は正、--delay は0以上、--timeout は正で指定してください。", file=sys.stderr)
        return 2

    counts: dict[str, int] = {}
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        create_schema(connection)
        where = "" if args.refresh else "WHERE COALESCE(m.fetch_status, '') != 'matched'"
        parameters: list[Any] = []
        if args.book_id:
            clause = ",".join("?" for _ in args.book_id)
            where += (" AND " if where else "WHERE ") + f"b.id IN ({clause})"
            parameters.extend(args.book_id)
        rows = connection.execute(
            f"""
            SELECT b.id, b.title, b.authors, b.isbn
            FROM books b
            LEFT JOIN book_metadata m ON m.book_id = b.id
            {where}
            ORDER BY b.id
            LIMIT ?
            """,
            (*parameters, args.limit),
        ).fetchall()
        for index, book in enumerate(rows):
            status = enrich_book(connection, book, args.timeout, args.dry_run)
            counts[status] = counts.get(status, 0) + 1
            if index + 1 < len(rows) and args.delay:
                time.sleep(args.delay)
        if args.dry_run:
            connection.rollback()

    print(f"db: {db_path}")
    print(f"processed: {len(rows)}")
    for status in sorted(counts):
        print(f"{status}: {counts[status]}")
    print(f"db_update: {'skipped (dry-run)' if args.dry_run else 'completed'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
