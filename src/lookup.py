"""Book title and ISBN lookup utilities."""

from __future__ import annotations

import json
import re
import sqlite3
import time
import unicodedata
import urllib.parse
import urllib.request
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
KNOWN_BOOKS_PATH = REPO_ROOT / "data" / "known_books.json"


def normalize_text(value: Any) -> str:
    """Normalize Japanese book metadata for fuzzy matching."""

    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).lower()
    return re.sub(r"[\s　・:：,，.．。『』「」\"'“”‘’!?！？\-‐‑‒–—―（）()【】\[\]]+", "", text)


def normalize_isbn(value: Any) -> str:
    """Keep only ISBN digits and X."""

    if value is None:
        return ""
    return re.sub(r"[^0-9xX]", "", str(value)).upper()


def score_text(query_norm: str, value_norm: str) -> float:
    """Score normalized text with exact, substring, then fuzzy matching."""

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
    """Convert a library DB row into API-friendly metadata."""

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


def known_book_candidates(
    title: str | None,
    path: Path = KNOWN_BOOKS_PATH,
    min_score: float = 0.65,
) -> list[dict[str, Any]]:
    """Find user-confirmed titles that are not present in the library DB."""

    if not title or not path.exists():
        return []
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []

    query_norm = normalize_text(title)
    candidates = []
    for record in records:
        title_value = record.get("title")
        aliases = [title_value, *(record.get("aliases") or [])]
        score = max(score_text(query_norm, normalize_text(alias)) for alias in aliases)
        if score < min_score:
            continue
        candidates.append(
            {
                "source": "known_books",
                "score": score,
                "match_confidence": "auto" if score >= 0.75 else "review",
                "title": title_value,
                "authors": record.get("authors") or [],
                "publisher": record.get("publisher"),
                "published_date": record.get("published_date"),
                "class_number": record.get("class_number"),
                "acquisition_type": record.get("acquisition_type"),
                "registration_number": record.get("registration_number"),
                "isbns": record.get("isbns") or [],
                "library_db_id": None,
                "thumbnail": record.get("thumbnail"),
                "info_link": record.get("info_link"),
            }
        )

    candidates.sort(key=lambda item: item["score"], reverse=True)
    return candidates


def search_library(
    db_path: Path,
    query: str,
    limit: int = 10,
    include_isbn: bool = False,
) -> list[dict[str, Any]]:
    """Search the local SQLite library DB.

    OCR-derived queries should use title/author/publisher only. ISBN matching is
    reserved for explicit user-entered ISBN searches.
    """

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
        where.append("(title_norm LIKE ? OR authors_norm LIKE ? OR publisher_norm LIKE ?)")
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


def library_db_lookup(
    title: str | None,
    db_path: Path,
    limit: int = 5,
    min_score: float = 0.75,
    review_min_score: float = 0.65,
) -> dict[str, Any] | None:
    """Find metadata candidates for one OCR title in the local library DB."""

    if not title or not db_path.exists():
        return None

    candidates = []
    for item in search_library(db_path, title, limit=limit):
        score = float(item.get("score") or 0.0)
        if score < review_min_score:
            continue
        candidates.append(
            {
                "source": "library_db",
                "score": score,
                "match_confidence": "auto" if score >= min_score else "review",
                "title": item.get("title"),
                "authors": [item["authors"]] if item.get("authors") else [],
                "publisher": item.get("publisher"),
                "published_date": item.get("published_date"),
                "class_number": item.get("class_number"),
                "acquisition_type": item.get("acquisition_type"),
                "registration_number": item.get("registration_number"),
                "isbns": [item["isbn"]] if item.get("isbn") else [],
                "library_db_id": item.get("id"),
                "thumbnail": item.get("thumbnail"),
                "info_link": item.get("info_link"),
            }
        )
    if not candidates:
        candidates = known_book_candidates(title, min_score=review_min_score)[:limit]
    return {"query": title, "source": "library_db", "candidates": candidates}


def google_books_lookup(title: str | None) -> dict[str, Any] | None:
    """Find metadata candidates for one title with Google Books."""

    if not title:
        return None

    query = f'intitle:"{title}"'
    params = urllib.parse.urlencode(
        {"q": query, "maxResults": 5, "printType": "books", "langRestrict": "ja"}
    )
    url = f"https://www.googleapis.com/books/v1/volumes?{params}"

    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return {"error": str(exc), "query": query}

    candidates = []
    for item in data.get("items", []):
        info = item.get("volumeInfo", {})
        identifiers = info.get("industryIdentifiers", [])
        isbns = [
            ident.get("identifier")
            for ident in identifiers
            if ident.get("type") in {"ISBN_10", "ISBN_13"} and ident.get("identifier")
        ]
        candidates.append(
            {
                "source": "google_books",
                "title": info.get("title"),
                "authors": info.get("authors", []),
                "publisher": info.get("publisher"),
                "published_date": info.get("publishedDate"),
                "description": info.get("description"),
                "page_count": info.get("pageCount"),
                "categories": info.get("categories", []),
                "language": info.get("language"),
                "isbns": isbns,
                "google_books_id": item.get("id"),
                "info_link": info.get("infoLink"),
                "thumbnail": (info.get("imageLinks") or {}).get("thumbnail"),
            }
        )

    return {"query": query, "source": "google_books", "candidates": candidates}


def lookup_title_metadata(
    title: str | None,
    db_path: Path | None = None,
    google_fallback: bool = False,
) -> dict[str, Any] | None:
    """Lookup book metadata from an OCR title."""

    lookup = library_db_lookup(title, db_path) if db_path else None
    if google_fallback and (not lookup or not lookup.get("candidates")):
        lookup = google_books_lookup(title)
        time.sleep(0.2)
    return lookup
