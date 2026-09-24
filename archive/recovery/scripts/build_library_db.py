"""
図書リスト CSV/XLSX からローカル検索用 SQLite DB を作る。

使い方:
  uv run python scripts/build_library_db.py
  uv run python scripts/build_library_db.py data/library.xlsx
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import time
import unicodedata
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from openpyxl import load_workbook


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = "data/神山まるごと高専蔵書(20250325).xlsx"
DEFAULT_DB = "outputs/library/library.db"


FIELD_ALIASES = {
    "title": ["書名", "書名1"],
    "authors": ["著者名", "著者名1"],
    "publisher": ["出版者"],
    "published_date": ["出版年月日"],
    "class_number": ["分類記号", "分類記号1"],
    "acquisition_type": ["受入区分"],
    "price": ["定価"],
    "registration_number": ["まるごと高専登録番号", "登録番号"],
    "isbn": ["ISBN1", "ISBN"],
}
FIELD_MAP = {
    header: field for field, headers in FIELD_ALIASES.items() for header in headers
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="図書リストからSQLite DBを作成します。")
    parser.add_argument("input", nargs="?", default=DEFAULT_INPUT, help="CSVまたはXLSX")
    parser.add_argument("--db", default=DEFAULT_DB, help="出力SQLite DB")
    parser.add_argument(
        "--fetch-covers",
        action="store_true",
        help="書影メタデータを取得してbook_coversに保存する",
    )
    parser.add_argument(
        "--cover-source",
        choices=["openbd", "google", "openbd-google"],
        default="openbd",
        help="書影取得元。openbd-googleはOpenBDで未取得の時だけGoogle Booksを使う",
    )
    parser.add_argument(
        "--cover-limit",
        type=int,
        default=None,
        help="書影取得件数の上限。省略時は全件",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.05,
        help="API呼び出し間隔",
    )
    parser.add_argument(
        "--api-timeout",
        type=float,
        default=4.0,
        help="APIの1リクエストあたりのタイムアウト秒数",
    )
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


def read_xlsx(path: Path) -> list[dict[str, Any]]:
    try:
        wb = load_workbook(path, read_only=True, data_only=True)
        ws = wb[wb.sheetnames[0]]
        rows = ws.iter_rows(values_only=True)
        headers = [str(h).strip() if h is not None else "" for h in next(rows)]
        records = []
        for row in rows:
            raw = {headers[i]: row[i] if i < len(row) else None for i in range(len(headers))}
            records.append(convert_record(raw))
        return records
    except TypeError:
        # Some exported XLSX files contain style XML that openpyxl rejects.
        # The worksheet values are still valid, so stream just the first sheet XML.
        return read_xlsx_values_from_xml(path)


def column_index(cell_ref: str) -> int:
    letters = re.sub(r"[^A-Z]", "", cell_ref.upper())
    index = 0
    for char in letters:
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1


def xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def cell_value(cell: ET.Element) -> str | None:
    value = None
    for child in cell.iter():
        if child is cell:
            continue
        if xml_local_name(child.tag) in {"v", "t"}:
            value = child.text
            break
    if value is None:
        return None
    return str(value).strip() or None


def read_xlsx_values_from_xml(path: Path) -> list[dict[str, Any]]:
    with zipfile.ZipFile(path) as archive:
        sheet_name = "xl/worksheets/sheet.xml"
        if sheet_name not in archive.namelist():
            sheet_name = sorted(
                name for name in archive.namelist()
                if name.startswith("xl/worksheets/") and name.endswith(".xml")
            )[0]

        headers_by_col: dict[int, str] = {}
        target_cols: dict[int, str] = {}
        records: list[dict[str, Any]] = []

        with archive.open(sheet_name) as source:
            for _, elem in ET.iterparse(source, events=("end",)):
                if xml_local_name(elem.tag) != "row":
                    continue

                row_values: dict[int, str | None] = {}
                for cell in elem:
                    if xml_local_name(cell.tag) != "c":
                        continue
                    ref = cell.attrib.get("r", "")
                    if not ref:
                        continue
                    row_values[column_index(ref)] = cell_value(cell)

                if not headers_by_col:
                    headers_by_col = {
                        col: str(value).strip()
                        for col, value in row_values.items()
                        if value is not None and str(value).strip()
                    }
                    target_headers = set(FIELD_MAP)
                    target_cols = {
                        col: header
                        for col, header in headers_by_col.items()
                        if header in target_headers
                    }
                else:
                    raw = {
                        header: row_values.get(col)
                        for col, header in target_cols.items()
                    }
                    records.append(convert_record(raw))

                elem.clear()

    return records


def read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return [convert_record(row) for row in csv.DictReader(f)]


def convert_record(raw: dict[str, Any]) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for field, headers in FIELD_ALIASES.items():
        record[field] = next(
            (raw.get(header) for header in headers if raw.get(header) not in {None, ""}),
            None,
        )
    for key, value in list(record.items()):
        if value is None:
            record[key] = None
        elif isinstance(value, str):
            record[key] = value.strip() or None
    record["title_norm"] = normalize_text(record.get("title"))
    record["authors_norm"] = normalize_text(record.get("authors"))
    record["publisher_norm"] = normalize_text(record.get("publisher"))
    record["isbn_norm"] = normalize_isbn(record.get("isbn"))
    return record


def google_books_lookup(
    record: dict[str, Any],
    timeout: float = 4.0,
    api_key: str | None = None,
) -> dict[str, Any]:
    isbn = normalize_isbn(record.get("isbn"))
    title = record.get("title")
    authors = record.get("authors")
    queries = []
    if isbn:
        queries.append(f"isbn:{isbn}")
    if title:
        parts = [f'intitle:"{title}"']
        if authors:
            parts.append(f'inauthor:"{authors}"')
        queries.append(" ".join(parts))

    last_error = None
    for query in queries:
        request_params = {
            "q": query,
            "maxResults": 5,
            "printType": "books",
            "langRestrict": "ja",
        }
        if api_key:
            request_params["key"] = api_key
        params = urllib.parse.urlencode(request_params)
        url = f"https://www.googleapis.com/books/v1/volumes?{params}"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            last_error = str(exc)
            continue

        for item in data.get("items", []):
            info = item.get("volumeInfo", {})
            image_links = info.get("imageLinks") or {}
            thumbnail = image_links.get("thumbnail") or image_links.get("smallThumbnail")
            if not thumbnail:
                continue
            identifiers = info.get("industryIdentifiers", [])
            isbns = [
                ident.get("identifier")
                for ident in identifiers
                if ident.get("type") in {"ISBN_10", "ISBN_13"} and ident.get("identifier")
            ]
            return {
                "source_query": query,
                "google_books_id": item.get("id"),
                "matched_title": info.get("title"),
                "matched_authors": info.get("authors", []),
                "publisher": info.get("publisher"),
                "published_date": info.get("publishedDate"),
                "isbns": isbns,
                "info_link": info.get("infoLink"),
                "thumbnail": thumbnail.replace("http://", "https://"),
                "small_thumbnail": (
                    image_links.get("smallThumbnail", "").replace("http://", "https://")
                    if image_links.get("smallThumbnail")
                    else None
                ),
                "raw": item,
                "error": None,
            }

    return {
        "source_query": queries[0] if queries else None,
        "google_books_id": None,
        "matched_title": None,
        "matched_authors": [],
        "publisher": None,
        "published_date": None,
        "isbns": [],
        "info_link": None,
        "thumbnail": None,
        "small_thumbnail": None,
        "raw": None,
        "error": last_error,
}


def chunked(values: list[Any], size: int) -> list[list[Any]]:
    return [values[i : i + size] for i in range(0, len(values), size)]


def normalize_cover_url(value: Any) -> str | None:
    if not value:
        return None
    url = str(value).strip()
    if not url:
        return None
    return url.replace("http://", "https://")


def openbd_book_title(data: dict[str, Any]) -> str | None:
    summary = data.get("summary") or {}
    if summary.get("title"):
        return summary["title"]
    title_details = (
        (data.get("onix") or {})
        .get("DescriptiveDetail", {})
        .get("TitleDetail", {})
    )
    if isinstance(title_details, list):
        title_details = title_details[0] if title_details else {}
    title_element = title_details.get("TitleElement", {})
    if isinstance(title_element, list):
        title_element = title_element[0] if title_element else {}
    return title_element.get("TitleText", {}).get("content")


def openbd_book_authors(data: dict[str, Any]) -> list[str]:
    summary = data.get("summary") or {}
    if summary.get("author"):
        return [summary["author"]]
    contributors = (
        (data.get("onix") or {})
        .get("DescriptiveDetail", {})
        .get("Contributor", [])
    )
    if isinstance(contributors, dict):
        contributors = [contributors]
    return [
        item.get("PersonName", {}).get("content")
        for item in contributors
        if item.get("PersonName", {}).get("content")
    ]


def openbd_book_publisher(data: dict[str, Any]) -> str | None:
    summary = data.get("summary") or {}
    if summary.get("publisher"):
        return summary["publisher"]
    publishing = (data.get("onix") or {}).get("PublishingDetail", {})
    publisher = publishing.get("Publisher", {})
    if isinstance(publisher, list):
        publisher = publisher[0] if publisher else {}
    return publisher.get("PublisherName")


def openbd_book_date(data: dict[str, Any]) -> str | None:
    summary = data.get("summary") or {}
    if summary.get("pubdate"):
        return summary["pubdate"]
    return (data.get("hanmoto") or {}).get("dateshuppan")


def openbd_cover_url(data: dict[str, Any]) -> str | None:
    summary_cover = normalize_cover_url((data.get("summary") or {}).get("cover"))
    if summary_cover:
        return summary_cover

    resources = (
        (data.get("onix") or {})
        .get("CollateralDetail", {})
        .get("SupportingResource", [])
    )
    if isinstance(resources, dict):
        resources = [resources]
    for resource in resources:
        url = normalize_cover_url(resource.get("ResourceLink"))
        if url:
            return url
    return None


def openbd_lookup_batch(
    rows: list[tuple[int, str | None]],
    timeout: float = 4.0,
) -> dict[int, dict[str, Any]]:
    clean_rows = [
        (book_id, normalize_isbn(isbn))
        for book_id, isbn in rows
        if normalize_isbn(isbn)
    ]
    if not clean_rows:
        return {}

    params = urllib.parse.urlencode(
        {"isbn": ",".join(isbn for _, isbn in clean_rows)}
    )
    url = f"https://api.openbd.jp/v1/get?{params}"
    with urllib.request.urlopen(url, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))

    results: dict[int, dict[str, Any]] = {}
    for (book_id, isbn), item in zip(clean_rows, data):
        if not item:
            results[book_id] = {
                "source": "openbd",
                "source_query": f"isbn:{isbn}",
                "provider_id": None,
                "matched_title": None,
                "matched_authors": [],
                "publisher": None,
                "published_date": None,
                "isbns": [],
                "info_link": None,
                "thumbnail": None,
                "small_thumbnail": None,
                "raw": None,
                "error": None,
            }
            continue

        cover = openbd_cover_url(item)
        results[book_id] = {
            "source": "openbd",
            "source_query": f"isbn:{isbn}",
            "provider_id": isbn,
            "matched_title": openbd_book_title(item),
            "matched_authors": openbd_book_authors(item),
            "publisher": openbd_book_publisher(item),
            "published_date": openbd_book_date(item),
            "isbns": [isbn],
            "info_link": f"https://openbd.jp/isbn/{isbn}",
            "thumbnail": cover,
            "small_thumbnail": cover,
            "raw": item,
            "error": None,
        }
    return results


def empty_cover_result(record: dict[str, Any], source: str, error: str | None = None) -> dict[str, Any]:
    isbn = normalize_isbn(record.get("isbn"))
    return {
        "source": source,
        "source_query": f"isbn:{isbn}" if isbn else None,
        "provider_id": None,
        "matched_title": None,
        "matched_authors": [],
        "publisher": None,
        "published_date": None,
        "isbns": [isbn] if isbn else [],
        "info_link": None,
        "thumbnail": None,
        "small_thumbnail": None,
        "raw": None,
        "error": error,
    }


def read_records(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".xlsx":
        return read_xlsx(path)
    if suffix == ".csv":
        return read_csv(path)
    raise ValueError(f"未対応のファイル形式です: {path}")


def build_db(
    records: list[dict[str, Any]],
    db_path: Path,
    source_path: Path,
    fetch_covers: bool = False,
    cover_source: str = "openbd",
    cover_limit: int | None = None,
    sleep: float = 0.12,
    api_timeout: float = 4.0,
) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    con = sqlite3.connect(db_path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(
        """
        CREATE TABLE books (
            id INTEGER PRIMARY KEY,
            title TEXT,
            authors TEXT,
            publisher TEXT,
            published_date TEXT,
            class_number TEXT,
            acquisition_type TEXT,
            price TEXT,
            registration_number TEXT,
            isbn TEXT,
            title_norm TEXT,
            authors_norm TEXT,
            publisher_norm TEXT,
            isbn_norm TEXT,
            source_file TEXT
        )
        """
    )
    con.execute(
        """
        CREATE TABLE book_covers (
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

    rows = []
    for record in records:
        if not record.get("title"):
            continue
        rows.append(
            (
                record.get("title"),
                record.get("authors"),
                record.get("publisher"),
                record.get("published_date"),
                record.get("class_number"),
                record.get("acquisition_type"),
                str(record.get("price")) if record.get("price") is not None else None,
                str(record.get("registration_number")) if record.get("registration_number") is not None else None,
                record.get("isbn"),
                record.get("title_norm"),
                record.get("authors_norm"),
                record.get("publisher_norm"),
                record.get("isbn_norm"),
                str(source_path),
            )
        )

    con.executemany(
        """
        INSERT INTO books (
            title, authors, publisher, published_date, class_number, acquisition_type,
            price, registration_number, isbn, title_norm, authors_norm, publisher_norm,
            isbn_norm, source_file
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    con.execute("CREATE INDEX idx_books_title_norm ON books(title_norm)")
    con.execute("CREATE INDEX idx_books_isbn_norm ON books(isbn_norm)")
    con.execute("CREATE INDEX idx_books_registration_number ON books(registration_number)")
    con.execute("CREATE INDEX idx_book_covers_thumbnail ON book_covers(thumbnail)")

    cover_count = 0
    if fetch_covers:
        book_rows = con.execute(
            """
            SELECT id, title, authors, isbn
            FROM books
            WHERE title IS NOT NULL
            ORDER BY id
            """
        ).fetchall()
        if cover_limit is not None:
            book_rows = book_rows[:cover_limit]

        fetched_at = datetime.now(timezone.utc).isoformat()
        print(f"covers: fetching {len(book_rows)} records", flush=True)
        processed = 0
        for batch in chunked(book_rows, 100 if cover_source.startswith("openbd") else 1):
            batch_results: dict[int, dict[str, Any]] = {}
            if cover_source in {"openbd", "openbd-google"}:
                try:
                    batch_results = openbd_lookup_batch(
                        [(book_id, isbn) for book_id, _, _, isbn in batch],
                        timeout=api_timeout,
                    )
                except Exception as exc:
                    batch_results = {
                        book_id: empty_cover_result(
                            {"isbn": isbn},
                            "openbd",
                            error=str(exc),
                        )
                        for book_id, _, _, isbn in batch
                    }

            for book_id, title, authors, isbn in batch:
                result = batch_results.get(book_id)
                if cover_source == "google":
                    result = google_books_lookup(
                        {"title": title, "authors": authors, "isbn": isbn},
                        timeout=api_timeout,
                    )
                    result["source"] = "google_books"
                    result["provider_id"] = result.pop("google_books_id", None)
                elif (
                    cover_source == "openbd-google"
                    and result
                    and not result.get("thumbnail")
                ):
                    result = empty_cover_result({"isbn": isbn}, cover_source)

                con.execute(
                    """
                    INSERT INTO book_covers (
                        book_id, source, provider_id, matched_title, matched_authors_json,
                        publisher, published_date, isbns_json, info_link, thumbnail,
                        small_thumbnail, source_query, raw_json, error, fetched_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        book_id,
                        result.get("source") or cover_source,
                        result.get("provider_id"),
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
                if result.get("thumbnail"):
                    cover_count += 1
                processed += 1

            print(
                f"covers: {processed}/{len(book_rows)} fetched, {cover_count} with thumbnails",
                flush=True,
            )
            con.commit()
            time.sleep(sleep)

    con.execute(
        """
        CREATE TABLE metadata (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    con.executemany(
        "INSERT INTO metadata(key, value) VALUES (?, ?)",
        [
            ("source_file", str(source_path)),
            ("record_count", str(len(rows))),
            ("cover_record_count", str(cover_count)),
            ("cover_fetch_enabled", str(fetch_covers)),
            ("cover_source", cover_source),
            ("columns", json.dumps(FIELD_ALIASES, ensure_ascii=False)),
        ],
    )
    con.commit()
    con.close()


def main() -> int:
    args = parse_args()
    input_path = resolve_path(args.input)
    db_path = resolve_path(args.db)
    records = read_records(input_path)
    build_db(
        records,
        db_path,
        input_path,
        fetch_covers=args.fetch_covers,
        cover_source=args.cover_source,
        cover_limit=args.cover_limit,
        sleep=args.sleep,
        api_timeout=args.api_timeout,
    )
    print(f"input: {input_path}")
    print(f"records: {len([r for r in records if r.get('title')])}")
    print(f"db: {db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
