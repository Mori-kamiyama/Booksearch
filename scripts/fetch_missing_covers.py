"""Fill missing book covers from ISBN-based public APIs.

The script is resumable: it only selects books whose current thumbnail is empty.
Provider order defaults to OpenBD, Open Library, then Google Books.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from recommendation_utils import normalize_isbn


REPO_ROOT = Path(__file__).resolve().parent.parent
OPENBD_URL = "https://api.openbd.jp/v1/get"
OPEN_LIBRARY_URL = "https://openlibrary.org/api/books"
GOOGLE_BOOKS_URL = "https://www.googleapis.com/books/v1/volumes"


def request_json(url: str, timeout: float) -> Any:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "BooksearchCoverEnricher/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def https_url(value: str | None) -> str:
    return (value or "").replace("http://", "https://", 1)


def create_attempt_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS cover_fetch_attempts (
            book_id INTEGER NOT NULL,
            provider TEXT NOT NULL,
            status TEXT NOT NULL,
            attempted_at TEXT NOT NULL,
            PRIMARY KEY (book_id, provider),
            FOREIGN KEY (book_id) REFERENCES books(id)
        )
        """
    )


def missing_books(connection: sqlite3.Connection, limit: int | None = None, provider: str | None = None) -> list[sqlite3.Row]:
    suffix = "LIMIT ?" if limit else ""
    parameters: tuple[int, ...] = (limit,) if limit else ()
    attempt_join = ""
    attempt_filter = ""
    if provider:
        attempt_join = "LEFT JOIN cover_fetch_attempts cfa ON cfa.book_id = b.id AND cfa.provider = ?"
        attempt_filter = "AND cfa.book_id IS NULL"
        parameters = (provider, *parameters)
    return connection.execute(
        f"""
        SELECT b.id, b.title, b.authors, b.publisher, b.published_date, b.isbn
        FROM books b
        LEFT JOIN book_covers bc ON bc.book_id = b.id
        {attempt_join}
        WHERE COALESCE(bc.thumbnail, '') = ''
        {attempt_filter}
        ORDER BY b.id
        {suffix}
        """,
        parameters,
    ).fetchall()


def save_attempt(connection: sqlite3.Connection, book_id: int, provider: str, status: str) -> None:
    connection.execute(
        """
        INSERT INTO cover_fetch_attempts (book_id, provider, status, attempted_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(book_id, provider) DO UPDATE SET
            status=excluded.status, attempted_at=excluded.attempted_at
        """,
        (book_id, provider, status, datetime.now(timezone.utc).isoformat()),
    )


def save_cover(
    connection: sqlite3.Connection,
    book: sqlite3.Row,
    *,
    source: str,
    provider_id: str,
    thumbnail: str,
    small_thumbnail: str = "",
    info_link: str = "",
    raw: Any = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    connection.execute(
        """
        INSERT INTO book_covers (
            book_id, source, provider_id, matched_title, matched_authors_json,
            publisher, published_date, isbns_json, info_link, thumbnail,
            small_thumbnail, source_query, raw_json, error, fetched_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
        ON CONFLICT(book_id) DO UPDATE SET
            source=excluded.source, provider_id=excluded.provider_id,
            matched_title=excluded.matched_title,
            matched_authors_json=excluded.matched_authors_json,
            publisher=excluded.publisher, published_date=excluded.published_date,
            isbns_json=excluded.isbns_json, info_link=excluded.info_link,
            thumbnail=excluded.thumbnail, small_thumbnail=excluded.small_thumbnail,
            source_query=excluded.source_query, raw_json=excluded.raw_json,
            error=NULL, fetched_at=excluded.fetched_at
        """,
        (
            int(book["id"]),
            source,
            provider_id,
            str(book["title"] or ""),
            json.dumps([str(book["authors"] or "")], ensure_ascii=False),
            str(book["publisher"] or ""),
            str(book["published_date"] or ""),
            json.dumps([normalize_isbn(str(book["isbn"] or ""))], ensure_ascii=False),
            https_url(info_link) or None,
            https_url(thumbnail),
            https_url(small_thumbnail) or None,
            f'isbn:{normalize_isbn(str(book["isbn"] or ""))}',
            json.dumps(raw, ensure_ascii=False, separators=(",", ":")) if raw is not None else None,
            now,
        ),
    )


def chunks(values: list[sqlite3.Row], size: int) -> list[list[sqlite3.Row]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def fetch_openbd(connection: sqlite3.Connection, books: list[sqlite3.Row], timeout: float, batch_size: int) -> int:
    candidates = [book for book in books if normalize_isbn(str(book["isbn"] or ""))]
    saved = 0
    batches = chunks(candidates, batch_size)
    for index, batch in enumerate(batches, 1):
        isbns = [normalize_isbn(str(book["isbn"] or "")) for book in batch]
        url = f"{OPENBD_URL}?{urllib.parse.urlencode({'isbn': ','.join(isbns)})}"
        try:
            payload = request_json(url, timeout)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            print(f"openbd batch {index}/{len(batches)} error: {error}", flush=True)
            continue
        for book, isbn, record in zip(batch, isbns, payload):
            summary = record.get("summary") if isinstance(record, dict) else None
            cover = str((summary or {}).get("cover") or "")
            if cover:
                save_cover(connection, book, source="openbd", provider_id=isbn, thumbnail=cover, raw=record)
                saved += 1
            save_attempt(connection, int(book["id"]), "openbd", "found" if cover else "not_found")
        connection.commit()
        print(f"openbd batch {index}/{len(batches)} saved={saved}", flush=True)
    return saved


def fetch_open_library(connection: sqlite3.Connection, books: list[sqlite3.Row], timeout: float, batch_size: int) -> int:
    candidates = [book for book in books if normalize_isbn(str(book["isbn"] or ""))]
    saved = 0
    batches = chunks(candidates, batch_size)
    for index, batch in enumerate(batches, 1):
        isbns = [normalize_isbn(str(book["isbn"] or "")) for book in batch]
        bibkeys = [f"ISBN:{isbn}" for isbn in isbns]
        params = {"bibkeys": ",".join(bibkeys), "format": "json", "jscmd": "data"}
        try:
            payload = request_json(f"{OPEN_LIBRARY_URL}?{urllib.parse.urlencode(params)}", timeout)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            print(f"openlibrary batch {index}/{len(batches)} error: {error}", flush=True)
            continue
        for book, isbn, bibkey in zip(batch, isbns, bibkeys):
            record = payload.get(bibkey) or {}
            covers = record.get("cover") or {}
            cover = str(covers.get("large") or covers.get("medium") or covers.get("small") or "")
            if cover:
                save_cover(
                    connection,
                    book,
                    source="openlibrary",
                    provider_id=isbn,
                    thumbnail=cover,
                    small_thumbnail=str(covers.get("small") or ""),
                    info_link=str(record.get("url") or ""),
                    raw=record,
                )
                saved += 1
            save_attempt(connection, int(book["id"]), "openlibrary", "found" if cover else "not_found")
        connection.commit()
        print(f"openlibrary batch {index}/{len(batches)} saved={saved}", flush=True)
    return saved


def google_exact_volume(payload: dict[str, Any], isbn: str) -> dict[str, Any] | None:
    for volume in payload.get("items") or []:
        info = volume.get("volumeInfo") or {}
        identifiers = {
            normalize_isbn(str(item.get("identifier") or ""))
            for item in info.get("industryIdentifiers") or []
        }
        if isbn in identifiers:
            return volume
    return None


def google_lookup(book: sqlite3.Row, api_key: str | None, timeout: float) -> tuple[sqlite3.Row, dict[str, Any] | None, int | None, str | None]:
    isbn = normalize_isbn(str(book["isbn"] or ""))
    if not isbn:
        return book, None, None, "invalid ISBN"
    params = {"q": f"isbn:{isbn}", "maxResults": "5", "printType": "books"}
    if api_key:
        params["key"] = api_key
    url = f"{GOOGLE_BOOKS_URL}?{urllib.parse.urlencode(params)}"
    for retry_index in range(4):
        try:
            payload = request_json(url, timeout)
            return book, google_exact_volume(payload, isbn), None, None
        except urllib.error.HTTPError as error:
            if error.code in {429, 500, 502, 503, 504} and retry_index < 3:
                retry_after = error.headers.get("Retry-After")
                wait_seconds = float(retry_after) if retry_after and retry_after.isdigit() else 2 ** (retry_index + 1)
                time.sleep(wait_seconds)
                continue
            return book, None, error.code, f"HTTP {error.code}"
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            if retry_index < 3:
                time.sleep(2 ** retry_index)
                continue
            return book, None, None, str(error)
    return book, None, None, "retry exhausted"


def fetch_google(connection: sqlite3.Connection, books: list[sqlite3.Row], timeout: float, delay: float, workers: int) -> int:
    api_key = os.getenv("GOOGLE_BOOKS_API_KEY")
    saved = 0
    attempted = 0
    rate_limited = False
    batch_size = max(25, workers * 5)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for batch in chunks(books, batch_size):
            futures = [executor.submit(google_lookup, book, api_key, timeout) for book in batch]
            for future in as_completed(futures):
                book, volume, http_status, error = future.result()
                attempted += 1
                if http_status == 429:
                    rate_limited = True
                    continue
                if error or http_status:
                    if error == "invalid ISBN":
                        save_attempt(connection, int(book["id"]), "google", "not_found")
                    print(f"google request {attempted} error={error or http_status} saved={saved}", flush=True)
                    continue
                info = (volume or {}).get("volumeInfo") or {}
                image_links = info.get("imageLinks") or {}
                cover = str(image_links.get("thumbnail") or image_links.get("smallThumbnail") or "")
                if volume and cover:
                    isbn = normalize_isbn(str(book["isbn"] or ""))
                    save_cover(
                        connection,
                        book,
                        source="google_books",
                        provider_id=str(volume.get("id") or isbn),
                        thumbnail=cover,
                        small_thumbnail=str(image_links.get("smallThumbnail") or ""),
                        info_link=str(info.get("infoLink") or ""),
                        raw=volume,
                    )
                    save_attempt(connection, int(book["id"]), "google", "found")
                    saved += 1
                else:
                    save_attempt(connection, int(book["id"]), "google", "not_found")
            connection.commit()
            print(f"google request {attempted}/{len(books)} saved={saved}", flush=True)
            if rate_limited:
                print("google quota reached; stop and resume later", flush=True)
                break
            if delay:
                time.sleep(delay)
    return saved


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--providers", default="openbd,openlibrary,google")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--delay", type=float, default=0.35)
    parser.add_argument("--timeout", type=float, default=12)
    parser.add_argument("--workers", type=int, default=4)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = REPO_ROOT / db_path
    providers = [value.strip() for value in args.providers.split(",") if value.strip()]
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        create_attempt_schema(connection)
        before = len(missing_books(connection))
        totals: dict[str, int] = {}
        for provider in providers:
            books = missing_books(connection, args.limit, provider)
            if provider == "openbd":
                totals[provider] = fetch_openbd(connection, books, args.timeout, args.batch_size)
            elif provider == "openlibrary":
                totals[provider] = fetch_open_library(connection, books, args.timeout, args.batch_size)
            elif provider == "google":
                totals[provider] = fetch_google(connection, books, args.timeout, args.delay, max(1, args.workers))
            else:
                raise ValueError(f"unknown provider: {provider}")
        after = len(missing_books(connection))
    print(f"db: {db_path}")
    print(f"missing_before: {before}")
    print(f"saved: {totals}")
    print(f"missing_after: {after}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
