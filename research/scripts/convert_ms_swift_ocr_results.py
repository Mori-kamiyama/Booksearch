#!/usr/bin/env python3
"""Convert ms-swift JSONL inference output into the local OCR benchmark format."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path)
    parser.add_argument("reference", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--wall-sec", type=float, required=True)
    args = parser.parse_args()

    reference = json.loads(args.reference.read_text(encoding="utf-8"))
    by_name = {Path(row["image"]).name: row for row in reference["results"]}
    rows = []
    lines = args.results.read_text(encoding="utf-8").splitlines()
    mean_elapsed = args.wall_sec / max(1, len(lines))
    for line in lines:
        result = json.loads(line)
        image = result["images"][0]["path"]
        base = by_name[Path(image).name]
        rows.append(
            {
                **{key: value for key, value in base.items() if key not in {"prediction", "elapsed_sec", "ok"}},
                "ok": True,
                "prediction": result["response"],
                "elapsed_sec": mean_elapsed,
            }
        )

    payload = {
        "model_key": "paddleocr-vl-1.6-lora",
        "model": args.model,
        "revision": "checkpoint-702",
        "wall_sec": args.wall_sec,
        "results": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
