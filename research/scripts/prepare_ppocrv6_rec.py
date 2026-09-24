#!/usr/bin/env python3
"""Build a leakage-safe PP-OCRv6 recognition dataset from reviewed spine OCR.

PP-OCR recognition consumes one text sequence per image.  Book spines are tall,
so each crop is rendered in both 90-degree orientations for training.  Validation
and test keep the two orientations in separate label files so they can be scored
independently.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from collections import Counter
from pathlib import Path

from PIL import Image, ImageOps


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HUMAN_DIR = REPO_ROOT / "outputs/ocr_annotations/human_sft_538/ms_swift"
DEFAULT_ACTIVE_TRAIN = (
    REPO_ROOT / "outputs/ocr_active_review/v1/training_prepared/sft_train.jsonl"
)
DEFAULT_OUTPUT = REPO_ROOT / "outputs/ppocrv6_spine_rec/data"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--human-dir", type=Path, default=DEFAULT_HUMAN_DIR)
    parser.add_argument("--active-train", type=Path, default=DEFAULT_ACTIVE_TRAIN)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-label-length", type=int, default=80)
    return parser.parse_args()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def label_of(row: dict) -> str:
    value = row["messages"][1]["content"]
    return re.sub(r"\s+", " ", value).strip()


def image_of(row: dict) -> Path:
    return Path(row["images"][0]).resolve()


def save_rotations(rows: list[dict], split: str, output_dir: Path) -> dict[str, int]:
    image_dir = output_dir / "images" / split
    image_dir.mkdir(parents=True, exist_ok=True)
    labels = {"cw": [], "ccw": []}
    for row in rows:
        source = image_of(row)
        with Image.open(source) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
            variants = {
                "cw": image.transpose(Image.Transpose.ROTATE_270),
                "ccw": image.transpose(Image.Transpose.ROTATE_90),
            }
            for orientation, rotated in variants.items():
                relative = Path("images") / split / f"{source.stem}_{orientation}.jpg"
                rotated.save(output_dir / relative, quality=95, subsampling=0)
                labels[orientation].append(f"{relative.as_posix()}\t{label_of(row)}\n")

    if split == "train":
        combined = labels["cw"] + labels["ccw"]
        (output_dir / "train.txt").write_text("".join(combined), encoding="utf-8")
    else:
        for orientation, lines in labels.items():
            (output_dir / f"{split}_{orientation}.txt").write_text(
                "".join(lines), encoding="utf-8"
            )
    return {orientation: len(lines) for orientation, lines in labels.items()}


def main() -> int:
    args = parse_args()
    if args.output_dir.exists():
        shutil.rmtree(args.output_dir)
    args.output_dir.mkdir(parents=True)

    human_train = read_jsonl(args.human_dir / "train.jsonl")
    active_train = read_jsonl(args.active_train)
    # Active-review rows are newer; retain them if an image occurs in both inputs.
    train_by_image = {image_of(row).name: row for row in human_train}
    train_by_image.update({image_of(row).name: row for row in active_train})
    splits = {
        "train": list(train_by_image.values()),
        "validation": read_jsonl(args.human_dir / "validation.jsonl"),
        "test": read_jsonl(args.human_dir / "test.jsonl"),
    }

    seen: dict[str, str] = {}
    for split, rows in splits.items():
        for row in rows:
            name = image_of(row).name
            previous = seen.setdefault(name, split)
            if previous != split:
                raise ValueError(f"split leakage: {name} occurs in {previous} and {split}")
            label = label_of(row)
            if not label:
                raise ValueError(f"empty label: {name}")
            if len(label) > args.max_label_length:
                raise ValueError(f"label too long ({len(label)}): {name}: {label!r}")
            if not image_of(row).is_file():
                raise FileNotFoundError(image_of(row))

    generated = {
        split: save_rotations(rows, split, args.output_dir)
        for split, rows in splits.items()
    }
    lengths = [len(label_of(row)) for rows in splits.values() for row in rows]
    characters = Counter(char for rows in splits.values() for row in rows for char in label_of(row))
    manifest = {
        "sources": {
            "human_reviewed": str(args.human_dir.resolve()),
            "active_review_train": str(args.active_train.resolve()),
        },
        "base_rows": {split: len(rows) for split, rows in splits.items()},
        "generated_rows": generated,
        "rotation": {
            "cw": "90 degrees clockwise",
            "ccw": "90 degrees counter-clockwise",
            "train": "both orientations",
            "validation_test": "separate files per orientation",
        },
        "label_normalization": "collapse all whitespace, including newlines, to one ASCII space",
        "max_label_length": max(lengths),
        "unique_characters": len(characters),
        "fixed_test_is_excluded_from_training": True,
    }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
