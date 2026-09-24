#!/usr/bin/env python3
"""Evaluate OCR text combined with the local library search at Recall@K."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
from lookup import library_db_lookup, normalize_text


def rank_of(candidates: list[dict], expected_id: int, expected_title: str, key: str) -> int | None:
    for rank, candidate in enumerate(candidates, 1):
        if key == "record" and candidate.get("library_db_id") == expected_id:
            return rank
        if key == "title" and normalize_text(candidate.get("title")) == normalize_text(expected_title):
            return rank
    return None


def query_variants(text: str, strategy: str) -> list[str]:
    if strategy == "full":
        return [text]
    lines = [line.strip() for line in text.splitlines() if len(normalize_text(line)) >= 4]
    variants = [text, *lines]
    variants.extend("".join(lines[index : index + 2]) for index in range(len(lines) - 1))
    return list(dict.fromkeys(variant for variant in variants if variant))


def retrieve(text: str, db_path: Path, strategy: str) -> list[dict]:
    merged: dict[tuple[str, object], dict] = {}
    for query in query_variants(text, strategy):
        lookup = library_db_lookup(query, db_path=db_path, limit=5)
        for candidate in (lookup or {}).get("candidates") or []:
            key = (str(candidate.get("source")), candidate.get("library_db_id") or candidate.get("title"))
            previous = merged.get(key)
            if previous is None or float(candidate.get("score") or 0) > float(previous.get("score") or 0):
                merged[key] = candidate
    return sorted(merged.values(), key=lambda row: float(row.get("score") or 0), reverse=True)[:5]


def evaluate(path: Path, db_path: Path, strategy: str) -> tuple[dict, list[dict]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    details = []
    for row in payload["results"]:
        candidates = retrieve(row["prediction"], db_path, strategy)
        record_rank = rank_of(candidates, row["library_db_id"], row["title"], "record")
        title_rank = rank_of(candidates, row["library_db_id"], row["title"], "title")
        details.append(
            {
                "item_id": row["item_id"],
                "expected_library_db_id": row["library_db_id"],
                "expected_title": row["title"],
                "prediction": row["prediction"],
                "candidate_count": len(candidates),
                "record_rank": record_rank,
                "title_rank": title_rank,
                "candidates": [
                    {
                        "rank": rank,
                        "library_db_id": candidate.get("library_db_id"),
                        "title": candidate.get("title"),
                        "score": candidate.get("score"),
                    }
                    for rank, candidate in enumerate(candidates, 1)
                ],
            }
        )

    total = len(details)
    metric = lambda key, k: sum(
        row[key] is not None and row[key] <= k for row in details
    ) / total
    report = {
        "input": str(path),
        "model": payload["model"],
        "query_strategy": strategy,
        "items": total,
        "queries_with_candidates": sum(row["candidate_count"] > 0 for row in details),
        "queries_without_candidates": sum(row["candidate_count"] == 0 for row in details),
        "record_recall_at_1": metric("record_rank", 1),
        "record_recall_at_5": metric("record_rank", 5),
        "title_recall_at_1": metric("title_rank", 1),
        "title_recall_at_5": metric("title_rank", 5),
        "record_mrr_at_5": sum(
            0 if row["record_rank"] is None else 1 / row["record_rank"] for row in details
        ) / total,
        "title_mrr_at_5": sum(
            0 if row["title_rank"] is None else 1 / row["title_rank"] for row in details
        ) / total,
    }
    return report, details


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--library-db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    reports = []
    detail_payload = {}
    for path in args.inputs:
        for strategy in ("full", "line_ensemble"):
            report, details = evaluate(path, args.library_db, strategy)
            reports.append(report)
            detail_payload[f"{Path(path).stem}:{strategy}"] = details

    output = {
        "method": {
            "query_strategies": {
                "full": "complete OCR transcription",
                "line_ensemble": "full transcription, lines >=4 normalized chars, and adjacent two-line joins; merge by max score",
            },
            "engine": "src.lookup.library_db_lookup",
            "candidate_limit": 5,
            "review_min_score": 0.72,
            "record_metric": "exact library_db_id",
            "title_metric": "NFKC normalized exact catalog title",
        },
        "reports": reports,
        "details": detail_payload,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"method": output["method"], "reports": reports}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
