#!/usr/bin/env python3
"""Profile exported OCR annotations before evaluation or training."""

from __future__ import annotations

import argparse
import json
import re
import statistics
import unicodedata
from collections import Counter
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def compact(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", value or "").casefold()
    return re.sub(r"[^0-9a-zぁ-んァ-ヶ一-龠々ー]+", "", text)


def predictions(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {Path(row["image"]).name: row["prediction"] for row in payload["results"]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    parse_errors = []
    rows = []
    for line_number, line in enumerate(args.input.read_text(encoding="utf-8").splitlines(), 1):
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            parse_errors.append({"line": line_number, "error": str(exc)})

    reference_paths = {
        "lora": REPO_ROOT / "outputs/small_ocr_benchmark/paddleocr_vl_1_6_lora_100.json",
        "base": REPO_ROOT / "outputs/small_ocr_benchmark/paddleocr_vl_1_6_100.json",
        "gemini": REPO_ROOT / "outputs/small_ocr_benchmark/gemini_3_1_flash_lite_100.json",
    }
    reference = {name: predictions(path) for name, path in reference_paths.items()}
    pseudo = {}
    splits = {}
    training_path = REPO_ROOT / "outputs/qwen3_vl_modal_spines/training_v1/all.jsonl"
    for line in training_path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        name = Path(row["metadata"]["item_id"]).name
        answer = json.loads(row["messages"][1]["content"][0]["text"])
        pseudo[name] = answer["transcription"]
        splits[name] = row["metadata"]["split"]

    ids = [row.get("metadata", {}).get("item_id") for row in rows]
    images = [(row.get("images") or [None])[0] for row in rows]
    labels = [((row.get("messages") or [{}, {}])[1].get("content") or "") for row in rows]
    source_counts = Counter(row.get("metadata", {}).get("selected_source") for row in rows)
    flag_counts = Counter(flag for row in rows for flag in row.get("metadata", {}).get("flags", []))
    split_counts = Counter(splits.get(Path(image).name) for image in images if image)
    exact_source = Counter()
    catalog_exact = 0
    catalog_contained_extra = 0
    catalog_missing = []

    for row, image, label in zip(rows, images, labels):
        name = Path(image).name if image else ""
        for source, values in reference.items():
            exact_source[source] += label == values.get(name)
        exact_source["pseudo"] += label == pseudo.get(name)
        title = compact(row.get("metadata", {}).get("catalog_title"))
        target = compact(label)
        if title == target and title:
            catalog_exact += 1
        elif title and title in target:
            catalog_contained_extra += 1
        else:
            catalog_missing.append(
                {
                    "image": name,
                    "catalog_title": row.get("metadata", {}).get("catalog_title"),
                    "transcription": label,
                    "selected_source": row.get("metadata", {}).get("selected_source"),
                }
            )

    lengths = sorted(map(len, labels)) or [0]
    report = {
        "input_name": args.input.name,
        "rows": len(rows),
        "parse_errors": parse_errors,
        "unique_item_ids": len(set(ids)),
        "duplicate_item_ids": len(ids) - len(set(ids)),
        "unique_images": len(set(images)),
        "missing_images": sum(not image or not Path(image).is_file() for image in images),
        "empty_labels": sum(not label.strip() for label in labels),
        "selected_sources": dict(source_counts),
        "flags": dict(flag_counts),
        "splits": dict(split_counts),
        "label_shape": {
            "median_characters": statistics.median(lengths),
            "mean_characters": statistics.fmean(lengths),
            "p95_characters": lengths[max(0, round(0.95 * len(lengths)) - 1)],
            "multiline": sum("\n" in label for label in labels),
            "single_line": sum("\n" not in label for label in labels),
        },
        "exact_matches": dict(exact_source),
        "catalog_alignment": {
            "normalized_exact": catalog_exact,
            "contained_with_extra_text": catalog_contained_extra,
            "not_contained": len(catalog_missing),
        },
        "catalog_not_contained_rows": catalog_missing,
        "assessment": {
            "schema_ready": not parse_errors
            and len(ids) == len(set(ids))
            and not any(not image or not Path(image).is_file() for image in images)
            and not any(not label.strip() for label in labels),
            "recommended_use": "locked human-reviewed evaluation set after resolving target inconsistency and flagged rows",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "catalog_not_contained_rows"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
