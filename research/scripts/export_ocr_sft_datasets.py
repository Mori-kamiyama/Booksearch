#!/usr/bin/env python3
"""Export the spine dataset for PaddleOCR-VL ERNIEKit and ms-swift SFT."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def parse_answer(row: dict) -> dict:
    return json.loads(row["messages"][1]["content"][0]["text"])


def image_path(row: dict) -> str:
    return row["messages"][0]["content"][0]["image"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--minimum-match-score", type=float, default=0.88)
    args = parser.parse_args()

    counts: Counter[str] = Counter()
    sources: Counter[str] = Counter()
    for split in ("train", "validation", "test"):
        input_path = args.input_dir / f"{split}.jsonl"
        ernie_path = args.output_dir / "erniekit" / f"{split}.jsonl"
        swift_path = args.output_dir / "ms_swift" / f"{split}.jsonl"
        ernie_path.parent.mkdir(parents=True, exist_ok=True)
        swift_path.parent.mkdir(parents=True, exist_ok=True)

        with (
            input_path.open(encoding="utf-8") as source,
            ernie_path.open("w", encoding="utf-8") as ernie_output,
            swift_path.open("w", encoding="utf-8") as swift_output,
        ):
            for line in source:
                row = json.loads(line)
                if row["metadata"]["match_score"] < args.minimum_match_score:
                    continue
                answer = parse_answer(row)
                transcription = answer["transcription"].strip()
                if not transcription:
                    continue
                image = image_path(row)
                ernie = {
                    "image_info": [{"matched_text_index": 0, "image_url": image}],
                    "text_info": [
                        {"text": "OCR:", "tag": "mask"},
                        {"text": transcription, "tag": "no_mask"},
                    ],
                }
                swift = {
                    "messages": [
                        {"role": "user", "content": "<image>OCR:"},
                        {"role": "assistant", "content": transcription},
                    ],
                    "images": [image],
                }
                ernie_output.write(json.dumps(ernie, ensure_ascii=False) + "\n")
                swift_output.write(json.dumps(swift, ensure_ascii=False) + "\n")
                counts[split] += 1
                sources[row["metadata"]["pseudo_label_source"]] += 1

    manifest = {
        "source": str(args.input_dir.resolve()),
        "target": "visible spine transcription",
        "prompt": "OCR:",
        "minimum_match_score": args.minimum_match_score,
        "splits": dict(counts),
        "pseudo_label_sources": dict(sources),
        "formats": {
            "erniekit": "PaddleOCR-VL image_info/text_info JSONL",
            "ms_swift": "OpenAI-style messages plus images JSONL",
        },
        "warning": "Targets are pseudo labels and require a human-clean validation/test set before accuracy claims.",
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
