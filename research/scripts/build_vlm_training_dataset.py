"""Merge 4B/8B OCR results and build a leakage-safe VLM SFT dataset."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


PROMPT = """これは1冊の本の背表紙画像です。
画像に実際に見える文字を自然な読順で転記し、蔵書候補と照合して書名と著者を返してください。
JSONだけを返してください。"""


def auto_book(row: dict[str, Any], min_score: float) -> tuple[dict[str, Any], dict[str, Any]] | None:
    choices = []
    for book in ((row.get("parsed") or {}).get("books") or []):
        if not isinstance(book, dict):
            continue
        candidates = ((book.get("library_match") or {}).get("candidates") or [])
        if not candidates:
            continue
        candidate = candidates[0]
        score = float(candidate.get("score") or 0)
        if candidate.get("match_confidence") == "auto" and score >= min_score:
            choices.append((score, book, candidate))
    if not choices:
        return None
    _, book, candidate = max(choices, key=lambda value: value[0])
    return book, candidate


def split_for(library_id: Any, seed: str) -> str:
    digest = hashlib.sha256(f"{seed}:{library_id}".encode()).digest()
    bucket = int.from_bytes(digest[:4], "big") % 100
    if bucket < 90:
        return "train"
    if bucket < 95:
        return "validation"
    return "test"


def stable_rank(item_id: str, seed: str) -> str:
    return hashlib.sha256(f"{seed}:{item_id}".encode()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--four-b", type=Path, required=True)
    parser.add_argument("--eight-b", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--min-score", type=float, default=0.88)
    parser.add_argument("--max-per-book", type=int, default=8)
    parser.add_argument("--seed", default="booksearch-v1")
    args = parser.parse_args()

    four_payload = json.loads(args.four_b.read_text(encoding="utf-8"))
    eight_payload = json.loads(args.eight_b.read_text(encoding="utf-8"))
    eight_by_id = {str(row["item_id"]): row for row in eight_payload.get("results") or []}

    accepted_by_book: dict[str, list[dict[str, Any]]] = defaultdict(list)
    merged_rows = []
    source_counts: Counter[str] = Counter()
    rejection_counts: Counter[str] = Counter()

    for four_row in four_payload.get("results") or []:
        item_id = str(four_row["item_id"])
        selected_row = four_row
        selected_source = "4b"
        match = auto_book(four_row, args.min_score)
        if match is None and item_id in eight_by_id:
            selected_row = eight_by_id[item_id]
            selected_source = "8b_fallback"
            match = auto_book(selected_row, args.min_score)

        merged_rows.append(
            {
                "item_id": item_id,
                "selected_source": selected_source,
                "has_training_match": match is not None,
                "result": selected_row,
            }
        )
        if match is None:
            rejection_counts["no_high_confidence_match"] += 1
            continue

        book, candidate = match
        transcription = str(book.get("transcription") or "").strip()
        if len(transcription) < 2:
            rejection_counts["missing_transcription"] += 1
            continue
        library_id = str(candidate["library_db_id"])
        authors = candidate.get("authors") or []
        answer = {
            "transcription": transcription,
            "matched_book": {
                "library_db_id": candidate["library_db_id"],
                "title": candidate.get("title"),
                "authors": authors,
            },
        }
        sample = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "image": str(selected_row.get("crop_image") or item_id)},
                        {"type": "text", "text": PROMPT},
                    ],
                },
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": json.dumps(answer, ensure_ascii=False)}],
                },
            ],
            "metadata": {
                "item_id": item_id,
                "library_db_id": candidate["library_db_id"],
                "match_score": candidate.get("score"),
                "pseudo_label_model": selected_row.get("model"),
                "pseudo_label_source": selected_source,
                "ocr_confidence": book.get("confidence"),
                "detector_confidence": selected_row.get("detector_confidence"),
                "source_image": selected_row.get("source_image"),
            },
        }
        accepted_by_book[library_id].append(sample)
        source_counts[selected_source] += 1

    selected_samples = []
    dropped_for_cap = 0
    for library_id, samples in accepted_by_book.items():
        samples.sort(key=lambda sample: stable_rank(sample["metadata"]["item_id"], args.seed))
        selected_samples.extend(samples[: args.max_per_book])
        dropped_for_cap += max(0, len(samples) - args.max_per_book)

    splits: dict[str, list[dict[str, Any]]] = {"train": [], "validation": [], "test": []}
    for sample in selected_samples:
        split = split_for(sample["metadata"]["library_db_id"], args.seed)
        sample["metadata"]["split"] = split
        splits[split].append(sample)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, samples in {"all": selected_samples, **splits}.items():
        path = args.output_dir / f"{name}.jsonl"
        with path.open("w", encoding="utf-8") as handle:
            for sample in samples:
                handle.write(json.dumps(sample, ensure_ascii=False) + "\n")

    merged_path = args.output_dir / "merged_results.json"
    merged_path.write_text(json.dumps({"results": merged_rows}, ensure_ascii=False), encoding="utf-8")
    manifest = {
        "sources": {"4b": str(args.four_b.resolve()), "8b": str(args.eight_b.resolve())},
        "selection": {
            "minimum_library_match_score": args.min_score,
            "maximum_images_per_library_book": args.max_per_book,
            "split_unit": "library_db_id",
            "split_ratio": {"train": 0.90, "validation": 0.05, "test": 0.05},
            "seed": args.seed,
        },
        "stats": {
            "merged_crops": len(merged_rows),
            "eligible_before_cap": sum(len(samples) for samples in accepted_by_book.values()),
            "selected_samples": len(selected_samples),
            "unique_books": len(accepted_by_book),
            "dropped_for_per_book_cap": dropped_for_cap,
            "source_counts_before_cap": dict(source_counts),
            "rejections": dict(rejection_counts),
            "splits": {name: len(samples) for name, samples in splits.items()},
            "split_unique_books": {
                name: len({sample["metadata"]["library_db_id"] for sample in samples})
                for name, samples in splits.items()
            },
        },
    }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(manifest["stats"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
