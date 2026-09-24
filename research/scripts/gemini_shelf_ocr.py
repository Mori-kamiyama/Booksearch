#!/usr/bin/env python3
"""Benchmark the direct Gemini API on shelf crops."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from google import genai
from google.genai import types


PROMPT = "画像中の本の背表紙タイトルをすべて左から返せ。著者名・出版社は除外。"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="gemini-3.1-flash-lite")
    parser.add_argument("--retries", type=int, default=2)
    args = parser.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    client = genai.Client(api_key=api_key)
    source_rows = json.loads(args.reference.read_text(encoding="utf-8"))["results"]
    results = []
    wall_started = time.perf_counter()

    for index, row in enumerate(source_rows, 1):
        path = Path(row["image"])
        mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        started = time.perf_counter()
        error = None
        for attempt in range(1, args.retries + 2):
            try:
                response = client.models.generate_content(
                    model=args.model,
                    contents=[
                        types.Part.from_bytes(data=path.read_bytes(), mime_type=mime),
                        PROMPT,
                    ],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=list[str],
                        thinking_config=types.ThinkingConfig(thinking_level="MINIMAL"),
                        temperature=0,
                    ),
                )
                titles = json.loads(response.text)
                usage = response.usage_metadata
                result = {
                    "image": str(path),
                    "titles": titles,
                    "attempts": attempt,
                    "elapsed_sec": round(time.perf_counter() - started, 3),
                    "tokens": {
                        "prompt_tokens": usage.prompt_token_count,
                        "completion_tokens": usage.candidates_token_count,
                    },
                }
                break
            except Exception as exc:
                error = exc
        else:
            result = {
                "image": str(path),
                "titles": [],
                "attempts": args.retries + 1,
                "elapsed_sec": round(time.perf_counter() - started, 3),
                "error": f"{type(error).__name__}: {error}",
            }
        results.append(result)
        print(f"[{index:02d}/{len(source_rows)}] {path.name}: {len(result['titles'])} titles, {result['elapsed_sec']:.3f}s")

    payload = {
        "model": args.model,
        "thinking_level": "MINIMAL",
        "n_images": len(results),
        "total_elapsed_sec": round(time.perf_counter() - wall_started, 3),
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
