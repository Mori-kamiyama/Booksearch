#!/usr/bin/env python3
"""Benchmark Cerebras multimodal models on shelf crops."""

from __future__ import annotations

import argparse
import base64
import json
import os
import time
from pathlib import Path

from openai import OpenAI


PROMPT = "画像中の本の背表紙タイトルをすべて左からJSON文字列配列だけで返せ。著者名・出版社・説明・思考は出力しない。"


def data_url(path: Path) -> str:
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def parse_titles(raw: str) -> list[str]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        raw = raw.removeprefix("json").strip()
    value = json.loads(raw)
    if not isinstance(value, list):
        raise ValueError("response is not a JSON array")
    return [str(item).strip() for item in value if str(item).strip()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="qwen-3.8-27b")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--reasoning-effort", default="none")
    parser.add_argument("--retries", type=int, default=2)
    args = parser.parse_args()

    api_key = os.environ.get("CEREBRAS_API_KEY")
    if not api_key:
        raise RuntimeError("CEREBRAS_API_KEY is not set")
    client = OpenAI(base_url="https://api.cerebras.ai/v1", api_key=api_key)
    reference = json.loads(args.reference.read_text(encoding="utf-8"))
    source_rows = reference["results"][: args.limit]
    results = []
    wall_started = time.perf_counter()

    for index, row in enumerate(source_rows, 1):
        image_path = Path(row["image"])
        started = time.perf_counter()
        error = None
        for attempt in range(1, args.retries + 2):
            try:
                response = client.chat.completions.create(
                    model=args.model,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "image_url", "image_url": {"url": data_url(image_path)}},
                                {"type": "text", "text": PROMPT},
                            ],
                        }
                    ],
                    temperature=0,
                    max_completion_tokens=2_000,
                    reasoning_effort=args.reasoning_effort,
                )
                titles = parse_titles(response.choices[0].message.content or "")
                usage = response.usage
                result = {
                    "image": str(image_path),
                    "titles": titles,
                    "attempts": attempt,
                    "elapsed_sec": round(time.perf_counter() - started, 3),
                    "tokens": {
                        "prompt_tokens": usage.prompt_tokens if usage else None,
                        "completion_tokens": usage.completion_tokens if usage else None,
                    },
                }
                break
            except Exception as exc:
                error = exc
        else:
            result = {
                "image": str(image_path),
                "titles": [],
                "attempts": args.retries + 1,
                "elapsed_sec": round(time.perf_counter() - started, 3),
                "error": f"{type(error).__name__}: {error}",
            }
        results.append(result)
        print(f"[{index:02d}/{len(source_rows)}] {image_path.name}: {len(result['titles'])} titles, {result['elapsed_sec']:.3f}s")

    payload = {
        "model": args.model,
        "reasoning_effort": args.reasoning_effort,
        "n_images": len(results),
        "total_elapsed_sec": round(time.perf_counter() - wall_started, 3),
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "n_images": len(results)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
