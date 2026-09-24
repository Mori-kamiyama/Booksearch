#!/usr/bin/env python3
"""Sample OCR comparisons and run both predictions through the library index."""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from PIL import Image

from postprocess_qwen_vllm import LibraryIndex, load_rows


def candidates(index: LibraryIndex, text: str) -> list[dict]:
    result = index.lookup(text) or {}
    return [
        {
            "title": row.get("title"),
            "authors": row.get("authors") or [],
            "score": round(float(row.get("score") or 0), 3),
            "library_db_id": row.get("library_db_id"),
        }
        for row in (result.get("candidates") or [])[:3]
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--lora", type=Path, required=True)
    parser.add_argument("--library-db", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--count", type=int, default=6)
    parser.add_argument("--seed", type=int, default=20260924)
    args = parser.parse_args()

    base = json.loads(args.base.read_text(encoding="utf-8"))["results"]
    lora = json.loads(args.lora.read_text(encoding="utf-8"))["results"]
    lora_by_image = {Path(row["image"]).name: row for row in lora}
    rng = random.Random(args.seed)
    selected = rng.sample(base, min(args.count, len(base)))
    index = LibraryIndex(load_rows(args.library_db))
    args.output_dir.mkdir(parents=True, exist_ok=True)

    samples = []
    for sample_number, base_row in enumerate(selected, 1):
        source = Path(base_row["image"])
        lora_row = lora_by_image[source.name]
        png_name = f"sample_{sample_number:02d}_{source.stem}.png"
        with Image.open(source) as image:
            image.convert("RGB").save(args.output_dir / png_name, optimize=True)
        samples.append(
            {
                "sample": sample_number,
                "source_image": str(source),
                "png": str((args.output_dir / png_name).resolve()),
                "library_db_id": base_row["library_db_id"],
                "catalog_title": base_row["title"],
                "pseudo_ground_truth": base_row["expected"],
                "base_prediction": base_row["prediction"],
                "base_search": candidates(index, base_row["prediction"]),
                "lora_prediction": lora_row["prediction"],
                "lora_search": candidates(index, lora_row["prediction"]),
            }
        )

    payload = {
        "sampling": {"population": len(base), "count": len(samples), "seed": args.seed},
        "note": "pseudo_ground_truth is the selected Qwen transcription, not human-corrected text",
        "samples": samples,
    }
    (args.output_dir / "comparison.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
