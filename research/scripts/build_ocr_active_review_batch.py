#!/usr/bin/env python3
"""Build a deterministic, book-diverse OCR active-review batch."""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--exclude", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--seed", default="booksearch-active-v1")
    args = parser.parse_args()

    excluded = set()
    for path in args.exclude:
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            excluded.add(Path(row["images"][0]).name)

    by_book: dict[str, list[dict]] = defaultdict(list)
    for line in args.dataset.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        metadata = row.get("metadata") or {}
        image = Path(metadata.get("item_id") or row["messages"][0]["content"][0]["image"])
        if metadata.get("split") != "train" or image.name in excluded:
            continue
        by_book[str(metadata["library_db_id"])].append(row)

    rng = random.Random(args.seed)
    book_ids = list(by_book)
    rng.shuffle(book_ids)
    selected = []
    for book_id in book_ids[: args.limit]:
        candidates = by_book[book_id]
        selected.append(rng.choice(candidates))
    rng.shuffle(selected)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for rank, row in enumerate(selected):
            answer = json.loads(row["messages"][1]["content"][0]["text"])
            image = row["messages"][0]["content"][0]["image"]
            metadata = row["metadata"]
            output = {
                "messages": [
                    {"role": "user", "content": "<image>OCR:"},
                    {"role": "assistant", "content": answer["transcription"]},
                ],
                "images": [image],
                "metadata": {
                    "item_id": metadata["item_id"],
                    "library_db_id": metadata["library_db_id"],
                    "catalog_title": (answer.get("matched_book") or {}).get("title") or "",
                    "catalog_authors": (answer.get("matched_book") or {}).get("authors") or [],
                    "pseudo_transcription": answer["transcription"],
                    "match_score": metadata.get("match_score"),
                    "detector_confidence": metadata.get("detector_confidence"),
                    "split": metadata["split"],
                    "active_batch": "v1",
                    "random_rank": rank,
                },
            }
            handle.write(json.dumps(output, ensure_ascii=False) + "\n")

    manifest = {
        "dataset": str(args.dataset.resolve()),
        "excluded_files": [str(path.resolve()) for path in args.exclude],
        "seed": args.seed,
        "requested": args.limit,
        "selected": len(selected),
        "selection": "one random crop per library_db_id from train split, then shuffled",
        "eligible_unique_books": len(by_book),
        "excluded_image_count": len(excluded),
    }
    manifest_path = args.output.with_name("manifest.json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
