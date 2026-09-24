"""Select non-auto-matched Qwen OCR crops for a larger-model fallback."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def fallback_reasons(row: dict[str, Any]) -> list[str]:
    parsed = row.get("parsed")
    if not isinstance(parsed, dict):
        return ["invalid_json"]
    books = parsed.get("books")
    if not isinstance(books, list) or not books:
        return ["empty_books"]

    reasons: set[str] = set()
    for book in books:
        candidates = ((book.get("library_match") or {}).get("candidates") or [])
        status = candidates[0].get("match_confidence") if candidates else "none"
        if status != "auto":
            reasons.add(str(status))
    return sorted(reasons)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    result_payload = json.loads(args.results.read_text(encoding="utf-8"))
    catalog_payload = json.loads(args.catalog.read_text(encoding="utf-8"))
    reasons_by_id = {}
    reason_counts: dict[str, int] = {}
    for row in result_payload.get("results") or []:
        reasons = fallback_reasons(row)
        if not reasons:
            continue
        item_id = str(row["item_id"])
        reasons_by_id[item_id] = reasons
        for reason in reasons:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

    entries = []
    for entry in catalog_payload.get("entries") or []:
        reasons = reasons_by_id.get(str(entry.get("crop_image")))
        if not reasons:
            continue
        selected = dict(entry)
        selected["fallback_reasons"] = reasons
        entries.append(selected)

    output = {
        "source_catalog": str(args.catalog.resolve()),
        "source_results": str(args.results.resolve()),
        "selection": "invalid JSON, empty books, or any non-auto library match",
        "reason_counts": reason_counts,
        "entries": entries,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"selected": len(entries), "reason_counts": reason_counts}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
