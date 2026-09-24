#!/usr/bin/env python3
"""Prepare conservative human-reviewed OCR SFT splits and a review queue."""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SPLIT_SOURCE = REPO_ROOT / "outputs/qwen3_vl_modal_spines/training_v1/all.jsonl"


def compact(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", value or "").casefold()
    return re.sub(r"[^0-9a-zぁ-んァ-ヶ一-龠々ー]+", "", text)


def load_splits(path: Path) -> dict[str, str]:
    splits = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        name = Path(row["metadata"]["item_id"]).name
        splits[name] = row["metadata"]["split"]
    return splits


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("annotations", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--split-source", type=Path, default=DEFAULT_SPLIT_SOURCE)
    args = parser.parse_args()

    splits = load_splits(args.split_source)
    accepted: dict[str, list[dict]] = {"train": [], "validation": [], "test": []}
    review = []
    reason_counts: Counter[str] = Counter()

    for line_number, line in enumerate(args.annotations.read_text(encoding="utf-8").splitlines(), 1):
        row = json.loads(line)
        image = Path(row["images"][0])
        split = splits.get(image.name)
        if split not in accepted:
            raise ValueError(f"line {line_number}: split not found for {image.name}")

        metadata = row.get("metadata") or {}
        label = row["messages"][1]["content"]
        title = metadata.get("catalog_title")
        reasons = []
        if metadata.get("flags"):
            reasons.append("flagged")
        if not compact(title) or compact(title) not in compact(label):
            reasons.append("catalog_title_not_contained")

        row["metadata"] = {**metadata, "split": split, "human_reviewed": True}
        if reasons:
            row["metadata"]["review_reasons"] = reasons
            review.append(row)
            reason_counts.update(reasons)
        else:
            accepted[split].append(row)

    swift_dir = args.output_dir / "ms_swift"
    swift_dir.mkdir(parents=True, exist_ok=True)
    for split, rows in accepted.items():
        path = swift_dir / f"{split}.jsonl"
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )

    review_path = args.output_dir / "review.jsonl"
    review_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in review),
        encoding="utf-8",
    )
    manifest = {
        "source": str(args.annotations.resolve()),
        "split_source": str(args.split_source.resolve()),
        "target": "visible spine transcription",
        "quality_rule": "no flags and normalized catalog title is contained in the transcription",
        "accepted_splits": {split: len(rows) for split, rows in accepted.items()},
        "review_rows": len(review),
        "review_reasons": dict(reason_counts),
        "notes": [
            "Keep validation and test out of training.",
            "Catalog non-containment is a review heuristic, not proof that the transcription is wrong.",
            "Flagged multi-spine and unreadable crops are excluded from SFT until reviewed or recropped.",
        ],
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
