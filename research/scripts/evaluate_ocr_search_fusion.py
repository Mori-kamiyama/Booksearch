#!/usr/bin/env python3
"""Fuse two OCR search rankings with label-free reciprocal rank fusion."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
from lookup import normalize_text


def hit_rank(candidates: list[dict], expected: object, field: str) -> int | None:
    for rank, candidate in enumerate(candidates, 1):
        value = candidate[field]
        if field == "title":
            value = normalize_text(value)
        if value == expected:
            return rank
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--left", type=Path, required=True)
    parser.add_argument("--left-key", required=True)
    parser.add_argument("--right", type=Path, required=True)
    parser.add_argument("--right-key", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rrf-k", type=int, default=60)
    args = parser.parse_args()

    left = json.loads(args.left.read_text(encoding="utf-8"))["details"][args.left_key]
    right = json.loads(args.right.read_text(encoding="utf-8"))["details"][args.right_key]
    if [row["item_id"] for row in left] != [row["item_id"] for row in right]:
        raise ValueError("Input rows are not aligned")

    details = []
    fallback_details = []
    for left_row, right_row in zip(left, right):
        scores: dict[int, float] = {}
        candidates: dict[int, dict] = {}
        for source in (left_row, right_row):
            for candidate in source["candidates"]:
                book_id = candidate["library_db_id"]
                scores[book_id] = scores.get(book_id, 0.0) + 1 / (args.rrf_k + candidate["rank"])
                candidates[book_id] = candidate
        ranked = sorted(candidates.values(), key=lambda row: scores[row["library_db_id"]], reverse=True)[:5]
        expected_id = left_row["expected_library_db_id"]
        expected_title = normalize_text(left_row["expected_title"])
        details.append(
            {
                "item_id": left_row["item_id"],
                "expected_library_db_id": expected_id,
                "expected_title": left_row["expected_title"],
                "record_rank": hit_rank(ranked, expected_id, "library_db_id"),
                "title_rank": hit_rank(ranked, expected_title, "title"),
                "candidates": [
                    {**candidate, "rank": rank, "rrf_score": scores[candidate["library_db_id"]]}
                    for rank, candidate in enumerate(ranked, 1)
                ],
            }
        )
        selected = right_row if not left_row["candidates"] else left_row
        fallback_details.append(
            {
                "item_id": left_row["item_id"],
                "used_fallback": not left_row["candidates"],
                "record_rank": selected["record_rank"],
                "title_rank": selected["title_rank"],
            }
        )

    total = len(details)
    metric = lambda field, k: sum(
        row[field] is not None and row[field] <= k for row in details
    ) / total
    report = {
        "items": total,
        "method": f"reciprocal rank fusion, k={args.rrf_k}",
        "left": args.left_key,
        "right": args.right_key,
        "record_recall_at_1": metric("record_rank", 1),
        "record_recall_at_5": metric("record_rank", 5),
        "title_recall_at_1": metric("title_rank", 1),
        "title_recall_at_5": metric("title_rank", 5),
    }
    fallback_metric = lambda field, k: sum(
        row[field] is not None and row[field] <= k for row in fallback_details
    ) / total
    fallback_report = {
        "items": total,
        "method": "use right OCR only when left search has no candidates",
        "fallback_calls": sum(row["used_fallback"] for row in fallback_details),
        "record_recall_at_1": fallback_metric("record_rank", 1),
        "record_recall_at_5": fallback_metric("record_rank", 5),
        "title_recall_at_1": fallback_metric("title_rank", 1),
        "title_recall_at_5": fallback_metric("title_rank", 5),
    }
    payload = {
        "report": report,
        "fallback_report": fallback_report,
        "details": details,
        "fallback_details": fallback_details,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(fallback_report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
