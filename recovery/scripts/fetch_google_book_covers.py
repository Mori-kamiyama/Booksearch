"""
Google Books API から書影メタデータを少しずつ取得して library.db に保存する。

使い方:
  uv run python scripts/fetch_google_book_covers.py --limit 900
  uv run python scripts/fetch_google_book_covers.py --dry-run --limit 20
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from build_library_db import google_books_lookup


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = "outputs/library/library.db"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Google Books API の書影情報を既存 library.db に追記します。"
    )
    parser.add_argument("--db", default=DEFAULT_DB, help="SQLite DB")
    parser.add_argument(
        "--limit",
        type=int,
        default=900,
        help="今回 Google Books API を呼ぶ最大件数。1日1000回制限に対して少し余裕を残す",
    )
    parser.add_argument("--sleep", type=float, default=0.12, help="API呼び出し間隔")
    parser.add_argument("--api-timeout", type=float, default=4.0)
    parser.add_argument(
        "--api-key",
        default=None,
        help="Google Books API key。省略時は GOOGLE_BOOKS_API_KEY を使う",
    )
    parser.add_argument(
        "--retry-empty",
        action="store_true",
        help="過去に Google Books を試して書影なしだった本も再試行する",
    )
    parser.add_argument(
        "--max-consecutive-errors",
        type=int,
        default=5,
        help="連続エラーがこの回数に達したら停止する。429でquotaを無駄にしないため",
    )
    parser.add_argument("--dry-run", action="store_true", help="対象件数だけ表示する")
    return parser.parse_args()


def resolve_path(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else REPO_ROOT / p


def ensure_cover_table(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS book_covers (
            book_id INTEGER PRIMARY KEY,
            source TEXT,
            provider_id TEXT,
            matched_title TEXT,
            matched_authors_json TEXT,
            publisher TEXT,
            published_date TEXT,
            isbns_json TEXT,
            info_link TEXT,
            thumbnail TEXT,
            small_thumbnail TEXT,
            source_query TEXT,
            raw_json TEXT,
            error TEXT,
            fetched_at TEXT,
            FOREIGN KEY(book_id) REFERENCES books(id)
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_book_covers_thumbnail ON book_covers(thumbnail)")


def google_candidate_where(retry_empty: bool) -> str:
    if retry_empty:
        return """
        c.book_id IS NULL
        OR c.thumbnail IS NULL
        """
    return """
    c.book_id IS NULL
    OR (
        c.thumbnail IS NULL
        AND (
            COALESCE(c.source, '') != 'google_books'
            OR c.error IS NOT NULL
        )
    )
    """


def select_targets(
    con: sqlite3.Connection,
    limit: int,
    retry_empty: bool,
) -> list[sqlite3.Row]:
    where = google_candidate_where(retry_empty)
    return con.execute(
        f"""
        SELECT b.id, b.title, b.authors, b.isbn, c.source, c.thumbnail
        FROM books b
        LEFT JOIN book_covers c ON c.book_id = b.id
        WHERE b.title IS NOT NULL
          AND ({where})
        ORDER BY
          CASE WHEN b.isbn_norm IS NOT NULL AND b.isbn_norm != '' THEN 0 ELSE 1 END,
          b.id
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def upsert_cover(
    con: sqlite3.Connection,
    book_id: int,
    result: dict[str, Any],
    fetched_at: str,
) -> None:
    con.execute(
        """
        INSERT INTO book_covers (
            book_id, source, provider_id, matched_title, matched_authors_json,
            publisher, published_date, isbns_json, info_link, thumbnail,
            small_thumbnail, source_query, raw_json, error, fetched_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(book_id) DO UPDATE SET
            source = excluded.source,
            provider_id = excluded.provider_id,
            matched_title = excluded.matched_title,
            matched_authors_json = excluded.matched_authors_json,
            publisher = excluded.publisher,
            published_date = excluded.published_date,
            isbns_json = excluded.isbns_json,
            info_link = excluded.info_link,
            thumbnail = excluded.thumbnail,
            small_thumbnail = excluded.small_thumbnail,
            source_query = excluded.source_query,
            raw_json = excluded.raw_json,
            error = excluded.error,
            fetched_at = excluded.fetched_at
        """,
        (
            book_id,
            "google_books",
            result.get("google_books_id"),
            result.get("matched_title"),
            json.dumps(result.get("matched_authors") or [], ensure_ascii=False),
            result.get("publisher"),
            result.get("published_date"),
            json.dumps(result.get("isbns") or [], ensure_ascii=False),
            result.get("info_link"),
            result.get("thumbnail"),
            result.get("small_thumbnail"),
            result.get("source_query"),
            json.dumps(result.get("raw"), ensure_ascii=False) if result.get("raw") else None,
            result.get("error"),
            fetched_at,
        ),
    )


def upsert_metadata(con: sqlite3.Connection, values: dict[str, str]) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    con.executemany(
        """
        INSERT INTO metadata(key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        sorted(values.items()),
    )


def cover_stats(con: sqlite3.Connection) -> tuple[int, int, int]:
    total = con.execute("SELECT count(*) FROM books").fetchone()[0]
    fetched = con.execute("SELECT count(*) FROM book_covers").fetchone()[0]
    with_thumbnail = con.execute(
        "SELECT count(*) FROM book_covers WHERE thumbnail IS NOT NULL"
    ).fetchone()[0]
    return total, fetched, with_thumbnail


def main() -> int:
    args = parse_args()
    db_path = resolve_path(args.db)
    if not db_path.exists():
        raise SystemExit(f"DBが見つかりません: {db_path}")

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    ensure_cover_table(con)

    targets = select_targets(con, args.limit, args.retry_empty)
    if args.dry_run:
        total, fetched, with_thumbnail = cover_stats(con)
        print(f"db: {db_path}")
        print(f"books: {total}")
        print(f"cover rows: {fetched}")
        print(f"with thumbnails: {with_thumbnail}")
        print(f"targets this run: {len(targets)}")
        con.close()
        return 0

    fetched_at = datetime.now(timezone.utc).isoformat()
    api_key = args.api_key or os.environ.get("GOOGLE_BOOKS_API_KEY")
    found = 0
    consecutive_errors = 0
    for index, row in enumerate(targets, 1):
        result = google_books_lookup(
            {"title": row["title"], "authors": row["authors"], "isbn": row["isbn"]},
            timeout=args.api_timeout,
            api_key=api_key,
        )
        upsert_cover(con, row["id"], result, fetched_at)
        if result.get("thumbnail"):
            found += 1
            consecutive_errors = 0
        elif result.get("error"):
            consecutive_errors += 1
        else:
            consecutive_errors = 0
        if index % 25 == 0:
            con.commit()
            print(f"processed: {index}/{len(targets)}, thumbnails this run: {found}", flush=True)
        if (
            args.max_consecutive_errors > 0
            and consecutive_errors >= args.max_consecutive_errors
        ):
            con.commit()
            print(
                "stopping: "
                f"{consecutive_errors} consecutive errors "
                f"(last error: {result.get('error')})",
                flush=True,
            )
            break
        time.sleep(args.sleep)

    con.commit()
    total, fetched, with_thumbnail = cover_stats(con)
    upsert_metadata(
        con,
        {
            "cover_fetch_enabled": "True",
            "cover_source": "google_books_incremental",
            "cover_record_count": str(with_thumbnail),
            "google_books_last_fetch_at": fetched_at,
            "google_books_last_fetch_count": str(len(targets)),
            "google_books_last_thumbnail_count": str(found),
        },
    )
    con.commit()
    con.close()

    print(f"db: {db_path}")
    print(f"processed: {len(targets)}")
    print(f"thumbnails this run: {found}")
    print(f"cover rows: {fetched}/{total}")
    print(f"with thumbnails: {with_thumbnail}/{total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
