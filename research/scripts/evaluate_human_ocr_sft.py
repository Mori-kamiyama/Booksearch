#!/usr/bin/env python3
"""Compare ms-swift OCR inference outputs against human-reviewed JSONL."""

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


def load_predictions(path: Path) -> dict[str, str]:
    predictions = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        image = Path(row["images"][0]["path"]).name
        predictions[image] = row["response"]
    return predictions


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("reference", type=Path)
    parser.add_argument("predictions", nargs="+", type=Path)
    parser.add_argument("--names", nargs="+")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--benchmark-dir", type=Path)
    args = parser.parse_args()
    if args.names and len(args.names) != len(args.predictions):
        parser.error("--names must match the number of prediction files")

    references = []
    for line in args.reference.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        references.append(
            {
                "image": Path(row["images"][0]).name,
                "image_path": row["images"][0],
                "item_id": row["metadata"]["item_id"],
                "library_db_id": row["metadata"]["library_db_id"],
                "label": row["messages"][1]["content"],
                "title": row["metadata"]["catalog_title"],
            }
        )

    reports = []
    for index, path in enumerate(args.predictions):
        predictions = load_predictions(path)
        rows = [row for row in references if row["image"] in predictions]
        cers = []
        similarities = []
        title_similarities = []
        exact = 0
        contains = 0
        for row in rows:
            expected = compact(row["label"])
            predicted = compact(predictions[row["image"]])
            title = compact(row["title"])
            cers.append(edit_distance(expected, predicted) / max(1, len(expected)))
            similarities.append(SequenceMatcher(None, expected, predicted).ratio())
            title_similarities.append(SequenceMatcher(None, title, predicted).ratio())
            exact += expected == predicted
            contains += bool(title and title in predicted)
        reports.append(
            {
                "name": args.names[index] if args.names else path.stem,
                "prediction_file": str(path),
                "items": len(rows),
                "normalized_exact": exact / len(rows),
                "mean_compact_cer": statistics.fmean(cers),
                "median_compact_cer": statistics.median(cers),
                "mean_human_label_similarity": statistics.fmean(similarities),
                "catalog_title_exact_containment_rate": contains / len(rows),
                "mean_catalog_title_similarity": statistics.fmean(title_similarities),
            }
        )
        if args.benchmark_dir:
            benchmark_rows = []
            reference_by_image = {row["image"]: row for row in references}
            for image, prediction in predictions.items():
                reference = reference_by_image.get(image)
                if not reference:
                    continue
                benchmark_rows.append(
                    {
                        "item_id": reference["item_id"],
                        "image": reference["image_path"],
                        "library_db_id": reference["library_db_id"],
                        "title": reference["title"],
                        "expected": reference["label"],
                        "prediction": prediction,
                        "ok": True,
                    }
                )
            args.benchmark_dir.mkdir(parents=True, exist_ok=True)
            name = args.names[index] if args.names else path.stem
            (args.benchmark_dir / f"{name}.json").write_text(
                json.dumps({"model": name, "results": benchmark_rows}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

    rendered = json.dumps(reports, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
