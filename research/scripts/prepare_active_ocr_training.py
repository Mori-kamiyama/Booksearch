#!/usr/bin/env python3
"""Validate active-review exports and create deterministic DPO train/validation splits."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sft", type=Path, required=True)
    parser.add_argument("--preferences", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--validation-size", type=int, default=14)
    args = parser.parse_args()

    sft = load_jsonl(args.sft)
    preferences = load_jsonl(args.preferences)
    for row in sft:
        assert Path(row["images"][0]).is_file()
        assert row["messages"][-1]["content"].strip()
    for row in preferences:
        assert Path(row["images"][0]).is_file()
        assert row["messages"][-1]["content"].strip()
        assert row["rejected_response"].strip()
        assert row["messages"][-1]["content"].strip() != row["rejected_response"].strip()

    ranked = sorted(
        preferences,
        key=lambda row: hashlib.sha256(
            str((row.get("metadata") or {}).get("item_id") or row["images"][0]).encode()
        ).digest(),
    )
    validation = ranked[: args.validation_size]
    train = ranked[args.validation_size :]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.output_dir / "sft_train.jsonl", sft)
    write_jsonl(args.output_dir / "preferences_train.jsonl", train)
    write_jsonl(args.output_dir / "preferences_validation.jsonl", validation)
    manifest = {
        "sft_train": len(sft),
        "preferences_train": len(train),
        "preferences_validation": len(validation),
        "preference_split": "sha256(item_id), first validation_size rows held out",
        "notes": [
            "The one accepted multi_spine row is excluded by the review app export.",
            "All active-review images came from the original training split.",
            "The original 78-image human test split remains untouched for final comparison.",
        ],
    }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
