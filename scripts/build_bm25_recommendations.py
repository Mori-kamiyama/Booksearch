"""Build related-book recommendations from local metadata using BM25 keywords.

Examples:
  uv run python scripts/build_bm25_recommendations.py --db path/to/library.db --dry-run
  uv run python scripts/build_bm25_recommendations.py --db path/to/library.db --top-k 6

This is an offline baseline: it does not send catalogue data to an external
service and does not infer reading or borrowing behaviour.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from enrich_book_metadata import create_schema as create_metadata_schema
from recommendation_utils import (
    BM25Index,
    author_similarity,
    bm25_scores,
    class_similarity,
    normalize_isbn,
    normalize_text,
    prepare_bm25,
    repeated_tokens,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
STRATEGY = "bm25-keyword-v1"


@dataclass(frozen=True)
class ConservativeBookMetadata:
    author: str
    class_prefix: str
    categories: frozenset[str]
    isbn: str
    title_author: tuple[str, str]


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


def create_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS book_recommendations (
            source_book_id INTEGER NOT NULL,
            recommended_book_id INTEGER NOT NULL,
            score REAL NOT NULL,
            strategy TEXT NOT NULL,
            reasons_json TEXT NOT NULL DEFAULT '[]',
            generated_at TEXT NOT NULL,
            PRIMARY KEY (source_book_id, recommended_book_id, strategy),
            FOREIGN KEY (source_book_id) REFERENCES books(id),
            FOREIGN KEY (recommended_book_id) REFERENCES books(id)
        )
        """
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_book_recommendations_source ON book_recommendations(source_book_id, strategy, score DESC)")


def categories(value: str | None) -> list[str]:
    try:
        decoded = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return [str(item) for item in decoded] if isinstance(decoded, list) else []


def document_tokens(book: sqlite3.Row) -> list[str]:
    # Field repetition is a simple, inspectable field-weighted BM25 baseline.
    tokens = (
        repeated_tokens(book["title"], 5)
        + repeated_tokens(book["authors"], 3)
        + repeated_tokens(book["categories_text"], 3)
        + repeated_tokens(book["class_number"], 2)
        + repeated_tokens(book["publisher"], 1)
        + repeated_tokens(book["description"], 1)
    )
    return tokens


def recommendation_reasons(
    source: sqlite3.Row,
    candidate: sqlite3.Row,
    *,
    conservative: bool = False,
    source_metadata: ConservativeBookMetadata | None = None,
    candidate_metadata: ConservativeBookMetadata | None = None,
) -> list[str]:
    if conservative:
        source_metadata = source_metadata or conservative_book_metadata(source)
        candidate_metadata = candidate_metadata or conservative_book_metadata(candidate)
        reasons: list[str] = []
        if source_metadata.author and source_metadata.author == candidate_metadata.author:
            reasons.append("同じ著者の作品")
        if source_metadata.class_prefix and source_metadata.class_prefix == candidate_metadata.class_prefix:
            reasons.append("分類が近い本")
        if source_metadata.categories & candidate_metadata.categories:
            reasons.append("同じカテゴリの本")
        return reasons

    reasons: list[str] = []
    if author_similarity(source["authors"], candidate["authors"]) >= 0.96:
        reasons.append("同じ著者の作品")
    similarity = class_similarity(source["class_number"], candidate["class_number"])
    if similarity >= 0.55:
        reasons.append("分類が近い本")
    elif similarity >= 0.12:
        reasons.append("同じ大分類の本")
    source_categories = set(categories(source["categories_json"]))
    candidate_categories = set(categories(candidate["categories_json"]))
    if source_categories & candidate_categories:
        reasons.append("同じカテゴリの本")
    if not reasons:
        reasons.append("内容のキーワードが近い本")
    return reasons


def conservative_book_metadata(book: sqlite3.Row) -> ConservativeBookMetadata:
    author = normalize_text(book["authors"])
    class_number = normalize_text(book["class_number"])
    return ConservativeBookMetadata(
        author=author,
        class_prefix=class_number[:3] if len(class_number) >= 3 else "",
        categories=frozenset(categories(book["categories_json"])),
        isbn=normalize_isbn(str(book["isbn"] or "")) if "isbn" in book.keys() else "",
        title_author=(normalize_text(book["title"]), author),
    )


def prepare_conservative_metadata(books: list[sqlite3.Row]) -> list[ConservativeBookMetadata]:
    return [conservative_book_metadata(book) for book in books]


def conservative_match(source_metadata: ConservativeBookMetadata, candidate_metadata: ConservativeBookMetadata) -> bool:
    same_author = bool(source_metadata.author and source_metadata.author == candidate_metadata.author)
    same_class = bool(source_metadata.class_prefix and source_metadata.class_prefix == candidate_metadata.class_prefix)
    shared_categories = bool(source_metadata.categories & candidate_metadata.categories)
    return same_author or same_class or shared_categories


def excluded_conservative_candidate(
    source_metadata: ConservativeBookMetadata,
    candidate_metadata: ConservativeBookMetadata,
) -> bool:
    if source_metadata.isbn and source_metadata.isbn == candidate_metadata.isbn:
        return True
    return source_metadata.title_author != ("", "") and source_metadata.title_author == candidate_metadata.title_author


def ranked_recommendations(
    books: list[sqlite3.Row],
    source_index: int,
    top_k: int,
    *,
    documents: list[list[str]] | None = None,
    bm25_index: BM25Index | None = None,
    conservative: bool = False,
    conservative_metadata: list[ConservativeBookMetadata] | None = None,
) -> list[tuple[sqlite3.Row, float, list[str]]]:
    source = books[source_index]
    documents = documents if documents is not None else [document_tokens(book) for book in books]
    bm25_index = bm25_index or prepare_bm25(documents)
    if conservative:
        conservative_metadata = conservative_metadata if conservative_metadata is not None else prepare_conservative_metadata(books)
        source_metadata = conservative_metadata[source_index]
    else:
        source_metadata = None
    scores = bm25_scores(
        documents,
        documents[source_index],
        prepared=bm25_index,
        query_frequencies=bm25_index.term_frequencies[source_index],
    )
    scored: list[tuple[int, sqlite3.Row, float]] = []
    for index, candidate in enumerate(books):
        if index == source_index:
            continue
        candidate_metadata = conservative_metadata[index] if conservative_metadata is not None else None
        if conservative and (
            excluded_conservative_candidate(source_metadata, candidate_metadata)
            or not conservative_match(source_metadata, candidate_metadata)
        ):
            continue
        score = scores[index]
        author_bonus = 0.45 if author_similarity(source["authors"], candidate["authors"]) >= 0.96 else 0.0
        classification_bonus = 0.35 * class_similarity(source["class_number"], candidate["class_number"])
        final_score = score + author_bonus + classification_bonus
        if final_score > 0:
            scored.append((index, candidate, final_score))
    scored.sort(key=lambda item: (-item[2], int(item[1]["id"])))
    ranked: list[tuple[sqlite3.Row, float, list[str]]] = []
    seen_title_authors: set[tuple[str, str]] = set()
    for index, candidate, score in scored:
        if conservative:
            title_author = conservative_metadata[index].title_author
            if title_author != ("", "") and title_author in seen_title_authors:
                continue
            if title_author != ("", ""):
                seen_title_authors.add(title_author)
        ranked.append(
            (
                candidate,
                score,
                recommendation_reasons(
                    source,
                    candidate,
                    conservative=conservative,
                    source_metadata=source_metadata,
                    candidate_metadata=conservative_metadata[index] if conservative else None,
                ),
            )
        )
        if len(ranked) >= top_k:
            break
    return ranked


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="SQLite catalogue DB path")
    parser.add_argument("--top-k", type=int, default=6, help="recommendations per book (default: 6)")
    parser.add_argument("--limit-books", type=int, help="only process the first N eligible books")
    parser.add_argument("--book-id", type=int, action="append", help="only process this source book ID; may be repeated")
    parser.add_argument("--conservative", action="store_true", help="only keep author/class/category-supported recommendations")
    parser.add_argument("--dry-run", action="store_true", help="calculate but do not change the DB")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = resolve_path(args.db)
    if not db_path.is_file():
        print(f"DBが見つかりません: {db_path}", file=sys.stderr)
        return 2
    if args.top_k <= 0 or (args.limit_books is not None and args.limit_books <= 0):
        print("--top-k と --limit-books は正の値で指定してください。", file=sys.stderr)
        return 2

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        create_schema(connection)
        create_metadata_schema(connection)
        where = "WHERE trim(b.title) != ''"
        parameters: list[Any] = []
        limit = f"LIMIT {args.limit_books}" if args.limit_books else ""
        book_columns = {row[1] for row in connection.execute("PRAGMA table_info(books)")}
        isbn_select = "COALESCE(b.isbn, '')" if "isbn" in book_columns else "''"
        books = connection.execute(
            f"""
            SELECT b.id, b.title, COALESCE(b.authors, '') AS authors,
                   {isbn_select} AS isbn,
                   COALESCE(b.publisher, '') AS publisher,
                   COALESCE(b.class_number, '') AS class_number,
                   COALESCE(m.description, '') AS description,
                   COALESCE(m.categories_json, '[]') AS categories_json,
                   COALESCE(m.categories_json, '[]') AS categories_text
            FROM books b
            LEFT JOIN book_metadata m ON m.book_id = b.id AND m.fetch_status = 'matched'
            {where}
            ORDER BY b.id
            {limit}
            """,
            parameters,
        ).fetchall()
        if len(books) < 2:
            print("推薦計算にはタイトルを持つ本が2冊以上必要です。", file=sys.stderr)
            connection.rollback()
            return 1

        source_indexes = [i for i, book in enumerate(books) if not args.book_id or int(book["id"]) in args.book_id]
        if not source_indexes:
            print("対象の本がありません。", file=sys.stderr)
            connection.rollback()
            return 1
        generated_at = datetime.now(timezone.utc).isoformat()
        written = 0
        documents = [document_tokens(book) for book in books]
        bm25_index = prepare_bm25(documents)
        conservative_metadata = prepare_conservative_metadata(books) if args.conservative else None
        if not args.dry_run:
            source_ids = [int(books[i]["id"]) for i in source_indexes]
            placeholders = ",".join("?" for _ in source_ids)
            connection.execute(
                f"DELETE FROM book_recommendations WHERE strategy = ? AND source_book_id IN ({placeholders})",
                (STRATEGY, *source_ids),
            )
        for source_index in source_indexes:
            source = books[source_index]
            ranked = ranked_recommendations(
                books,
                source_index,
                args.top_k,
                documents=documents,
                bm25_index=bm25_index,
                conservative=args.conservative,
                conservative_metadata=conservative_metadata,
            )
            if not args.dry_run:
                connection.executemany(
                    """
                    INSERT INTO book_recommendations (
                        source_book_id, recommended_book_id, score, strategy, reasons_json, generated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (int(source["id"]), int(candidate["id"]), score, STRATEGY, json.dumps(reasons, ensure_ascii=False), generated_at)
                        for candidate, score, reasons in ranked
                    ],
                )
            written += len(ranked)
        if args.dry_run:
            connection.rollback()

    print(f"db: {db_path}")
    print(f"eligible_books: {len(books)}")
    print(f"recommendations: {written}")
    print(f"strategy: {STRATEGY}")
    print(f"db_update: {'skipped (dry-run)' if args.dry_run else 'completed'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
