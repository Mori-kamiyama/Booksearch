#!/usr/bin/env python3
"""Run Gemini title OCR on the same held-out crops used by the local OCR benchmark."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
from src.ocr import gemini_title_ocr


def infer(row: dict, model: str, retries: int) -> dict:
    started = time.perf_counter()
    error = None
    for attempt in range(retries + 1):
        try:
            books = gemini_title_ocr(Path(row["image"]), model=model)
            prediction = "\n".join(book["title"] for book in books if book.get("title"))
            return {
                **{key: value for key, value in row.items() if key not in {"prediction", "elapsed_sec", "ok"}},
                "ok": True,
                "prediction": prediction,
                "elapsed_sec": time.perf_counter() - started,
            }
        except Exception as exc:  # API errors are retried and preserved in output.
            error = f"{type(exc).__name__}: {exc}"
            if attempt < retries:
                time.sleep(2**attempt)
    return {
        **{key: value for key, value in row.items() if key not in {"prediction", "elapsed_sec", "ok"}},
        "ok": False,
        "prediction": "",
        "error": error,
        "elapsed_sec": time.perf_counter() - started,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="gemini-3.1-flash-lite-preview")
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--retries", type=int, default=3)
    args = parser.parse_args()

    reference = json.loads(args.reference.read_text(encoding="utf-8"))
    rows = reference["results"]
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(infer, row, args.model, args.retries) for row in rows]
        results = [future.result() for future in futures]

    payload = {
        "model_key": "gemini-title-ocr",
        "model": args.model,
        "revision": None,
        "prompt": "src.ocr.TITLE_OCR_PROMPT (title only)",
        "wall_sec": time.perf_counter() - started,
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "model": args.model,
                "items": len(results),
                "successful": sum(row["ok"] for row in results),
                "nonempty": sum(bool(row["prediction"]) for row in results),
                "wall_sec": payload["wall_sec"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
