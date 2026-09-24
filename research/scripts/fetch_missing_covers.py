"""Fill missing book covers from ISBN-based public APIs.

The script is resumable: it only selects books whose current thumbnail is empty.
Provider order defaults to OpenBD, Open Library, Rakuten Books, then Google Books.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from recommendation_utils import author_similarity, normalize_isbn, normalize_text, text_similarity


REPO_ROOT = Path(__file__).resolve().parent.parent
OPENBD_URL = "https://api.openbd.jp/v1/get"
OPEN_LIBRARY_URL = "https://openlibrary.org/api/books"
GOOGLE_BOOKS_URL = "https://www.googleapis.com/books/v1/volumes"
RAKUTEN_BOOKS_URL = "https://openapi.rakuten.co.jp/services/api/BooksBook/Search/20170404"


def request_json(url: str, timeout: float, headers: dict[str, str] | None = None) -> Any:
    request_headers = {"Accept": "application/json", "User-Agent": "BooksearchCoverEnricher/1.0"}
    request_headers.update(headers or {})
    request = urllib.request.Request(url, headers=request_headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def https_url(value: str | None) -> str:
    return (value or "").replace("http://", "https://", 1)


def rakuten_cover(item: dict[str, Any] | None) -> str:
    cover = str((item or {}).get("largeImageUrl") or (item or {}).get("mediumImageUrl") or "")
    return "" if "noimage" in cover.lower() else cover


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
    source_query: str | None = None,
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
            source_query or f'isbn:{normalize_isbn(str(book["isbn"] or ""))}',
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


def rakuten_exact_item(payload: dict[str, Any], isbn: str) -> dict[str, Any] | None:
    for wrapped_item in payload.get("Items") or payload.get("items") or []:
        item = wrapped_item.get("Item", wrapped_item.get("item", wrapped_item))
        if normalize_isbn(str(item.get("isbn") or "")) == isbn:
            return item
    return None


def rakuten_lookup(
    book: sqlite3.Row,
    application_id: str,
    access_key: str,
    affiliate_id: str | None,
    origin: str,
    timeout: float,
) -> tuple[sqlite3.Row, dict[str, Any] | None, int | None, str | None]:
    isbn = normalize_isbn(str(book["isbn"] or ""))
    if not isbn:
        return book, None, None, "invalid ISBN"
    params = {
        "applicationId": application_id,
        "isbn": isbn,
        "outOfStockFlag": "1",
        "hits": "5",
        "format": "json",
        "formatVersion": "2",
    }
    if affiliate_id:
        params["affiliateId"] = affiliate_id
    url = f"{RAKUTEN_BOOKS_URL}?{urllib.parse.urlencode(params)}"
    headers = {"accessKey": access_key, "Origin": origin}
    for retry_index in range(4):
        try:
            payload = request_json(url, timeout, headers=headers)
            return book, rakuten_exact_item(payload, isbn), None, None
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


def fetch_rakuten(connection: sqlite3.Connection, books: list[sqlite3.Row], timeout: float, delay: float, workers: int) -> int:
    application_id = os.getenv("RAKUTEN_APPLICATION_ID", "").strip()
    access_key = os.getenv("RAKUTEN_ACCESS_KEY", "").strip()
    affiliate_id = os.getenv("RAKUTEN_AFFILIATE_ID", "").strip() or None
    origin = os.getenv("RAKUTEN_ALLOWED_ORIGIN", "https://d2uel8nex1m4w7.cloudfront.net").rstrip("/")
    if not application_id or not access_key:
        raise RuntimeError("RAKUTEN_APPLICATION_ID and RAKUTEN_ACCESS_KEY are required")

    saved = 0
    attempted = 0
    # Keep batches aligned with concurrency so --workers 1 --delay 1 stays at
    # the registered expected rate of roughly one request per second.
    batch_size = max(1, workers)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for batch in chunks(books, batch_size):
            futures = [
                executor.submit(rakuten_lookup, book, application_id, access_key, affiliate_id, origin, timeout)
                for book in batch
            ]
            for future in as_completed(futures):
                book, item, http_status, error = future.result()
                attempted += 1
                if error or http_status:
                    if error == "invalid ISBN":
                        save_attempt(connection, int(book["id"]), "rakuten", "not_found")
                    print(f"rakuten request {attempted} error={error or http_status} saved={saved}", flush=True)
                    continue
                cover = rakuten_cover(item)
                if item and cover:
                    isbn = normalize_isbn(str(book["isbn"] or ""))
                    save_cover(
                        connection,
                        book,
                        source="rakuten_books",
                        provider_id=isbn,
                        thumbnail=cover,
                        small_thumbnail=str(item.get("mediumImageUrl") or item.get("smallImageUrl") or ""),
                        info_link=str(item.get("affiliateUrl") or item.get("itemUrl") or ""),
                        raw=item,
                    )
                    save_attempt(connection, int(book["id"]), "rakuten", "found")
                    saved += 1
                else:
                    save_attempt(connection, int(book["id"]), "rakuten", "not_found")
            connection.commit()
            print(f"rakuten request {attempted}/{len(books)} saved={saved}", flush=True)
            if delay:
                time.sleep(delay)
    return saved


def rakuten_title_match(payload: dict[str, Any], book: sqlite3.Row) -> dict[str, Any] | None:
    local_title = str(book["title"] or "")
    local_authors = str(book["authors"] or "")
    local_publisher = str(book["publisher"] or "")
    local_year_match = re.search(r"\d{4}", str(book["published_date"] or ""))
    local_year = local_year_match.group(0) if local_year_match else ""
    normalized_local_title = normalize_text(local_title)
    best: tuple[float, dict[str, Any]] | None = None
    for wrapped_item in payload.get("Items") or payload.get("items") or []:
        item = wrapped_item.get("Item", wrapped_item.get("item", wrapped_item))
        if not rakuten_cover(item):
            continue
        candidate_title = str(item.get("title") or "")
        title_score = text_similarity(local_title, str(item.get("title") or ""))
        normalized_candidate_title = normalize_text(candidate_title)
        title_is_exact = normalized_candidate_title == normalized_local_title
        title_is_expanded = normalized_candidate_title.startswith(normalized_local_title)
        if not title_is_exact and not title_is_expanded:
            continue
        author_score = author_similarity(local_authors, str(item.get("author") or "")) if local_authors else 1.0
        if local_authors and author_score < 0.45:
            continue
        candidate_publisher = str(item.get("publisherName") or "")
        publisher_score = text_similarity(local_publisher, candidate_publisher) if local_publisher else 1.0
        if local_publisher and publisher_score < 0.75:
            continue
        candidate_year_match = re.search(r"\d{4}", str(item.get("salesDate") or ""))
        candidate_year = candidate_year_match.group(0) if candidate_year_match else ""
        if local_year and candidate_year and local_year != candidate_year:
            continue
        year_score = 1.0 if local_year and candidate_year == local_year else 0.5
        score = 0.5 * title_score + 0.2 * author_score + 0.15 * publisher_score + 0.15 * year_score
        if best is None or score > best[0]:
            best = (score, item)
    return best[1] if best else None


def rakuten_title_lookup(
    book: sqlite3.Row,
    application_id: str,
    access_key: str,
    affiliate_id: str | None,
    origin: str,
    timeout: float,
) -> tuple[sqlite3.Row, dict[str, Any] | None, int | None, str | None]:
    title = str(book["title"] or "").strip()
    if not title:
        return book, None, None, "missing title"
    params = {
        "applicationId": application_id,
        "title": title,
        "outOfStockFlag": "1",
        "hits": "30",
        "format": "json",
        "formatVersion": "2",
    }
    if affiliate_id:
        params["affiliateId"] = affiliate_id
    url = f"{RAKUTEN_BOOKS_URL}?{urllib.parse.urlencode(params)}"
    headers = {"accessKey": access_key, "Origin": origin}
    for retry_index in range(4):
        try:
            payload = request_json(url, timeout, headers=headers)
            return book, rakuten_title_match(payload, book), None, None
        except urllib.error.HTTPError as error:
            if error.code in {429, 500, 502, 503, 504} and retry_index < 3:
                time.sleep(2 ** (retry_index + 1))
                continue
            return book, None, error.code, f"HTTP {error.code}"
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            if retry_index < 3:
                time.sleep(2 ** retry_index)
                continue
            return book, None, None, str(error)
    return book, None, None, "retry exhausted"


def fetch_rakuten_title(connection: sqlite3.Connection, books: list[sqlite3.Row], timeout: float, delay: float) -> int:
    application_id = os.getenv("RAKUTEN_APPLICATION_ID", "").strip()
    access_key = os.getenv("RAKUTEN_ACCESS_KEY", "").strip()
    affiliate_id = os.getenv("RAKUTEN_AFFILIATE_ID", "").strip() or None
    origin = os.getenv("RAKUTEN_ALLOWED_ORIGIN", "https://d2uel8nex1m4w7.cloudfront.net").rstrip("/")
    if not application_id or not access_key:
        raise RuntimeError("RAKUTEN_APPLICATION_ID and RAKUTEN_ACCESS_KEY are required")

    candidates = [book for book in books if not normalize_isbn(str(book["isbn"] or ""))]
    saved = 0
    for attempted, book in enumerate(candidates, 1):
        book, item, http_status, error = rakuten_title_lookup(
            book, application_id, access_key, affiliate_id, origin, timeout
        )
        cover = rakuten_cover(item)
        if item and cover:
            save_cover(
                connection,
                book,
                source="rakuten_books",
                provider_id=normalize_isbn(str(item.get("isbn") or "")) or str(item.get("itemUrl") or ""),
                thumbnail=cover,
                small_thumbnail=str(item.get("mediumImageUrl") or item.get("smallImageUrl") or ""),
                info_link=str(item.get("affiliateUrl") or item.get("itemUrl") or ""),
                source_query=f'title:{str(book["title"] or "")}',
                raw=item,
            )
            save_attempt(connection, int(book["id"]), "rakuten-title", "found")
            saved += 1
        elif not error and not http_status:
            save_attempt(connection, int(book["id"]), "rakuten-title", "not_found")
        else:
            print(f"rakuten title request {attempted} error={error or http_status} saved={saved}", flush=True)
        connection.commit()
        print(f"rakuten title request {attempted}/{len(candidates)} saved={saved}", flush=True)
        if delay:
            time.sleep(delay)
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
    parser.add_argument("--providers", default="openbd,openlibrary,rakuten,google")
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
            elif provider == "rakuten":
                totals[provider] = fetch_rakuten(connection, books, args.timeout, args.delay, max(1, args.workers))
            elif provider == "rakuten-title":
                totals[provider] = fetch_rakuten_title(connection, books, args.timeout, args.delay)
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
