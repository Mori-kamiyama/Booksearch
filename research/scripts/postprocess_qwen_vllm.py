"""Match Qwen OCR JSONL to the local library DB with a fast fuzzy index."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz, process


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
from lookup import normalize_text, row_to_record, score_text


def load_rows(db_path: Path) -> list[dict[str, Any]]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = [dict(row) for row in con.execute("SELECT * FROM books").fetchall()]
    con.close()
    return rows


def candidate_record(row: dict[str, Any], score: float) -> dict[str, Any]:
    return {
        "source": "library_db",
        "score": score,
        "match_confidence": "auto" if score >= 0.85 else "review",
        "title": row.get("title"),
        "authors": [row["authors"]] if row.get("authors") else [],
        "publisher": row.get("publisher"),
        "published_date": row.get("published_date"),
        "class_number": row.get("class_number"),
        "acquisition_type": row.get("acquisition_type"),
        "registration_number": row.get("registration_number"),
        "isbns": [row["isbn"]] if row.get("isbn") else [],
        "library_db_id": row.get("id"),
    }


class LibraryIndex:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.fields = {
            "title": [row.get("title_norm") or "" for row in rows],
            "authors": [row.get("authors_norm") or "" for row in rows],
            "publisher": [row.get("publisher_norm") or "" for row in rows],
        }
        self.cache: dict[str, dict[str, Any] | None] = {}

    def lookup(self, query: str | None) -> dict[str, Any] | None:
        if not query:
            return None
        if query in self.cache:
            return self.cache[query]
        query_norm = normalize_text(query)
        candidate_indexes: set[int] = set()
        for values in self.fields.values():
            for _, _, index in process.extract(
                query_norm,
                values,
                scorer=fuzz.ratio,
                limit=8,
                score_cutoff=25,
            ):
                candidate_indexes.add(index)
        candidates = []
        for index in candidate_indexes:
            row = self.rows[index]
            score = max(
                score_text(query_norm, row.get("title_norm") or ""),
                score_text(query_norm, row.get("authors_norm") or "") * 0.9,
                score_text(query_norm, row.get("publisher_norm") or "") * 0.8,
            )
            if score >= 0.72:
                candidates.append(candidate_record(row, score))
        candidates.sort(key=lambda item: item["score"], reverse=True)
        result = {"query": query, "source": "library_db", "candidates": candidates[:5]}
        self.cache[query] = result
        return result


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return rows


def group_books(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        for book in ((row.get("parsed") or {}).get("books") or []):
            candidates = ((book.get("library_match") or {}).get("candidates") or [])
            if not candidates:
                continue
            candidate = candidates[0]
            key = str(candidate["library_db_id"])
            item = grouped.setdefault(
                key,
                {
                    "library_db_id": candidate["library_db_id"],
                    "title": candidate.get("title"),
                    "authors": candidate.get("authors") or [],
                    "best_score": 0.0,
                    "crop_ids": [],
                    "source_images": [],
                },
            )
            item["best_score"] = max(item["best_score"], float(candidate["score"]))
            item["crop_ids"].append(row.get("box_id"))
            for source in [row.get("source_image"), *[
                appearance.get("source_image") for appearance in row.get("appearances") or []
            ]]:
                if source and source not in item["source_images"]:
                    item["source_images"].append(source)
    return sorted(grouped.values(), key=lambda item: (item["title"] or "", item["library_db_id"]))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--library-db", type=Path, default=Path("outputs/library/library.db"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    rows = load_jsonl(args.input)
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    catalog_map = {str(entry["crop_image"]): entry for entry in catalog.get("entries") or []}
    index = LibraryIndex(load_rows(args.library_db))
    match_stats = {"auto": 0, "review": 0, "none": 0}

    for row in rows:
        entry = catalog_map.get(str(row.get("item_id"))) or {}
        for key in (
            "source_image",
            "crop_image",
            "box_id",
            "polygon_xy",
            "appearances",
            "detector_confidence",
        ):
            row[key] = entry.get(key, row.get(key))
        for book in ((row.get("parsed") or {}).get("books") or []):
            matches = [index.lookup(value) for value in (book.get("title"), book.get("transcription")) if value]
            matches = [match for match in matches if match]
            matches.sort(
                key=lambda match: float(((match.get("candidates") or [{}])[0]).get("score") or 0),
                reverse=True,
            )
            book["library_match"] = matches[0] if matches else None
            candidates = ((book.get("library_match") or {}).get("candidates") or [])
            status = candidates[0].get("match_confidence") if candidates else "none"
            match_stats[status if status in match_stats else "none"] += 1

    unique_books = group_books(rows)
    first_row = rows[0] if rows else {}
    result = {
        "catalog": str(args.catalog.resolve()),
        "model": first_row.get("model"),
        "model_revision": first_row.get("model_revision"),
        "engine": first_row.get("engine", "vllm-0.30.0"),
        "gpu": "L40S",
        "library_db": str(args.library_db.resolve()),
        "results": rows,
        "unique_library_books": unique_books,
        "stats": {
            "crops": len(rows),
            "successful": sum(bool(row.get("ok")) for row in rows),
            "valid_json": sum(row.get("parsed") is not None for row in rows),
            "books": sum(len(((row.get("parsed") or {}).get("books") or [])) for row in rows),
            "library_matches": match_stats,
            "unique_library_books": len(unique_books),
        },
    }
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["stats"], ensure_ascii=False, indent=2))
    print(f"output={args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
