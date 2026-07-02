"""Build shelf-level book data from a catalog and optionally import it to SQLite.

Usage:
  uv run python scripts/import_bookshelf_catalog.py \
    --catalog outputs/book_catalog_data_260702/catalog.json \
    --db outputs/library/library.db \
    --output outputs/book_catalog_data_260702/bookshelf_data.json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


def best_candidate(book: dict[str, Any]) -> dict[str, Any] | None:
    candidates = ((book.get("book_lookup") or {}).get("candidates") or [])
    return candidates[0] if candidates else None


def evidence_score(candidate: dict[str, Any]) -> float:
    return float(candidate.get("score") or 0.0)


def confidence(scores: list[float]) -> float:
    if not scores:
        return 0.0
    avg = sum(scores) / len(scores)
    return avg * (1 - 0.72 ** len(scores))


def build_bookshelf_data(
    catalog: dict[str, Any],
    min_score: float,
) -> tuple[dict[str, Any], dict[tuple[int, str], list[float]]]:
    shelves: dict[str, dict[str, Any]] = {}
    observations: dict[tuple[int, str], list[float]] = defaultdict(list)
    rejected: list[dict[str, Any]] = []

    for entry in catalog.get("entries", []):
        shelf_id = entry.get("shelf_id")
        if not shelf_id:
            continue
        shelf = shelves.setdefault(
            shelf_id,
            {
                "shelf_id": shelf_id,
                "source_boxes": [],
                "books": [],
                "_books_by_id": {},
                "rejected_books": [],
            },
        )
        shelf["source_boxes"].append(
            {
                "box_id": entry.get("box_id"),
                "source_image": entry.get("source_image"),
                "crop_image": entry.get("crop_image"),
                "detector_confidence": entry.get("detector_confidence"),
            }
        )

        for book in entry.get("books", []):
            candidate = best_candidate(book)
            score = evidence_score(candidate) if candidate else 0.0
            book_id = candidate.get("library_db_id") if candidate else None
            row = {
                "ocr_title": book.get("title"),
                "match": candidate,
                "source_box": entry.get("box_id"),
                "crop_image": entry.get("crop_image"),
            }
            if book_id and score >= min_score:
                observations[(int(book_id), shelf_id)].append(score)
                grouped = shelf["_books_by_id"].setdefault(
                    int(book_id),
                    {
                        "book_id": int(book_id),
                        "title": candidate.get("title"),
                        "authors": candidate.get("authors") or [],
                        "publisher": candidate.get("publisher"),
                        "published_date": candidate.get("published_date"),
                        "class_number": candidate.get("class_number"),
                        "registration_number": candidate.get("registration_number"),
                        "isbns": candidate.get("isbns") or [],
                        "score_max": score,
                        "observations": 0,
                        "ocr_titles": [],
                        "source_boxes": [],
                    },
                )
                grouped["score_max"] = max(float(grouped["score_max"]), score)
                grouped["observations"] += 1
                if book.get("title") and book.get("title") not in grouped["ocr_titles"]:
                    grouped["ocr_titles"].append(book.get("title"))
                grouped["source_boxes"].append(
                    {
                        "box_id": entry.get("box_id"),
                        "crop_image": entry.get("crop_image"),
                        "source_image": entry.get("source_image"),
                    }
                )
            else:
                row["reject_reason"] = "no_candidate" if not candidate else "low_score"
                shelf["rejected_books"].append(row)
                rejected.append({"shelf_id": shelf_id, **row})

    for shelf in shelves.values():
        shelf["books"] = list(shelf.pop("_books_by_id").values())
        shelf["books"].sort(
            key=lambda item: (
                str(item.get("class_number") or ""),
                str(item.get("title") or ""),
            )
        )

    bookshelf = {
        "schema_version": 1,
        "source_catalog": catalog.get("source"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "min_score": min_score,
        "summary": {
            "shelves": len(shelves),
            "accepted_observations": sum(len(v) for v in observations.values()),
            "unique_book_shelf_pairs": len(observations),
            "rejected_books": len(rejected),
        },
        "shelves": [shelves[key] for key in sorted(shelves)],
    }
    return bookshelf, observations


def import_observations(
    db_path: Path,
    observations: dict[tuple[int, str], list[float]],
    observed_at: str,
    replace_candidates: bool = False,
) -> None:
    with sqlite3.connect(db_path) as con:
        if replace_candidates:
            con.execute("DELETE FROM book_shelf_candidates")
        for (book_id, shelf_id), scores in observations.items():
            conf = confidence(scores)
            con.execute(
                """
                INSERT INTO book_shelf_candidates
                    (book_id, shelf_id, confidence, observations, last_seen_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(book_id, shelf_id) DO UPDATE SET
                    confidence = excluded.confidence,
                    observations = excluded.observations,
                    last_seen_at = excluded.last_seen_at
                """,
                (book_id, shelf_id, conf, len(scores), observed_at),
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--db", default="outputs/library/library.db")
    parser.add_argument("--output", required=True)
    parser.add_argument("--min-score", type=float, default=0.75)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--replace-candidates", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    catalog_path = resolve_path(args.catalog)
    db_path = resolve_path(args.db)
    output_path = resolve_path(args.output)

    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    bookshelf, observations = build_bookshelf_data(catalog, args.min_score)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(bookshelf, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    observed_at = datetime.now(timezone.utc).isoformat()
    if not args.dry_run:
        import_observations(db_path, observations, observed_at, args.replace_candidates)

    print(f"bookshelf_data: {output_path}")
    print(f"shelves: {bookshelf['summary']['shelves']}")
    print(f"accepted_observations: {bookshelf['summary']['accepted_observations']}")
    print(f"unique_book_shelf_pairs: {bookshelf['summary']['unique_book_shelf_pairs']}")
    print(f"rejected_books: {bookshelf['summary']['rejected_books']}")
    print(f"db_import: {'skipped' if args.dry_run else db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
