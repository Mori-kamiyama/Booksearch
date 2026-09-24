#!/usr/bin/env python3
"""Score small OCR benchmark outputs against pseudo labels and catalog titles."""

from __future__ import annotations

import argparse
import json
import statistics
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path


def compact(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    return "".join(char for char in text if unicodedata.category(char)[0] in {"L", "N"})


def edit_distance(left: str, right: str) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for row, left_char in enumerate(left, 1):
        current = [row]
        for column, right_char in enumerate(right, 1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    reports = []
    for path in args.inputs:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = [row for row in payload["results"] if row["ok"]]
        cers = []
        ratios = []
        title_ratios = []
        title_contains = 0
        long_outputs = 0
        for row in rows:
            expected = compact(row["expected"])
            predicted = compact(row["prediction"])
            title = compact(row["title"])
            cers.append(edit_distance(expected, predicted) / max(1, len(expected)))
            ratios.append(SequenceMatcher(None, expected, predicted).ratio())
            title_ratios.append(SequenceMatcher(None, title, predicted).ratio())
            title_contains += bool(title and title in predicted)
            long_outputs += len(row["prediction"]) >= 500

        report = {
            "path": str(path),
            "model": payload["model"],
            "items": len(payload["results"]),
            "successful": len(rows),
            "wall_sec_including_startup": payload["wall_sec"],
            "mean_request_sec": statistics.fmean(row["elapsed_sec"] for row in rows),
            "median_request_sec": statistics.median(row["elapsed_sec"] for row in rows),
            "mean_compact_cer": statistics.fmean(cers),
            "median_compact_cer": statistics.median(cers),
            "mean_pseudo_label_similarity": statistics.fmean(ratios),
            "catalog_title_exact_containment_rate": title_contains / len(rows),
            "mean_catalog_title_similarity": statistics.fmean(title_ratios),
            "outputs_500_chars_or_more": long_outputs,
        }
        reports.append(report)

    rendered = json.dumps(reports, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
