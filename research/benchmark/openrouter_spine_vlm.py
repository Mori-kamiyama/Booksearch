"""OpenRouter 上の小型 VLM で背表紙 OCR を比較する。

例:
  uv run python benchmark/openrouter_spine_vlm.py \
    --manifest benchmark/spine_vlm_samples.json \
    --image-root /tmp/bookfinder-probe/vlm-crops-all \
    --output /tmp/bookfinder-probe/vlm-benchmark-results.json
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODELS = [
    "google/gemma-3-4b-it",
    "mistralai/ministral-3b-2512",
    "qwen/qwen3-vl-8b-instruct",
    "qwen/qwen3.5-9b",
    "openai/gpt-4o-mini",
]
PROMPT = """これは1冊の本の背表紙を切り出して傾き補正した画像です。
画像に実際に見える文字だけを、縦書きなら自然な読順に直して正確に転記してください。
知識から推測して補完しないでください。判読できない部分は勝手に埋めず、読み取れた範囲だけを書いてください。
JSONだけを返してください。形式:
{"transcription":"見える全文","title":"書名またはnull","author":"著者またはnull","call_number":"請求記号またはnull","confidence":0.0}"""


def normalize(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[\s・･:：,，.。\-―ー_『』「」\[\]()（）]+", "", value).casefold()


def parse_json_content(content: str | None) -> dict[str, Any] | None:
    if not content:
        return None
    text = content.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        # Some models emit raw newlines inside quoted fields. Preserve the raw
        # response for diagnosis instead of attempting a lossy repair.
        return None


def data_url(path: Path) -> str:
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def request_one(api_key: str, model: str, sample: dict[str, str], image_root: Path) -> dict[str, Any]:
    image_path = image_root / sample["file"]
    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": 300,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {"type": "image_url", "image_url": {"url": data_url(image_path)}},
                ],
            }
        ],
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://localhost/booksearch",
            "X-Title": "Booksearch spine VLM benchmark",
        },
        method="POST",
    )

    started = time.perf_counter()
    last_error = ""
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=90) as response:
                payload = json.loads(response.read().decode("utf-8"))
            elapsed = time.perf_counter() - started
            choice = (payload.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            content = message.get("content")
            parsed = parse_json_content(content)
            gt = normalize(sample["title"])
            title = normalize(parsed.get("title")) if parsed else ""
            transcription = normalize(parsed.get("transcription")) if parsed else ""
            return {
                "sample_id": sample["id"],
                "file": sample["file"],
                "ground_truth": sample["title"],
                "model": model,
                "ok": True,
                "elapsed_sec": round(elapsed, 3),
                "finish_reason": choice.get("finish_reason"),
                "content": content,
                "reasoning": message.get("reasoning"),
                "parsed": parsed,
                "valid_json": parsed is not None,
                "title_exact": bool(gt and title == gt),
                "title_visible_in_output": bool(gt and (gt in title or gt in transcription)),
                "usage": payload.get("usage") or {},
                "provider": payload.get("provider"),
            }
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            last_error = f"HTTP {exc.code}: {detail}"
            if exc.code not in {408, 409, 429, 500, 502, 503, 504}:
                break
            time.sleep(1.5 * (attempt + 1))
        except Exception as exc:  # noqa: BLE001 - benchmark must retain failures
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(1.5 * (attempt + 1))

    return {
        "sample_id": sample["id"],
        "file": sample["file"],
        "ground_truth": sample["title"],
        "model": model,
        "ok": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "error": last_error,
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for model in sorted({row["model"] for row in results}):
        rows = [row for row in results if row["model"] == model]
        successful = [row for row in rows if row.get("ok")]
        costs = [float((row.get("usage") or {}).get("cost") or 0) for row in successful]
        summary[model] = {
            "requests": len(rows),
            "successful": len(successful),
            "valid_json": sum(bool(row.get("valid_json")) for row in rows),
            "title_exact": sum(bool(row.get("title_exact")) for row in rows),
            "title_visible_in_output": sum(bool(row.get("title_visible_in_output")) for row in rows),
            "mean_elapsed_sec": round(sum(row["elapsed_sec"] for row in rows) / len(rows), 3),
            "total_cost_usd": round(sum(costs), 8),
        }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--image-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit("OPENROUTER_API_KEY is not set")
    samples = json.loads(args.manifest.read_text(encoding="utf-8"))["samples"]
    missing = [sample["file"] for sample in samples if not (args.image_root / sample["file"]).is_file()]
    if missing:
        raise SystemExit(f"missing images: {missing}")

    jobs = [(model, sample) for model in args.models for sample in samples]
    results: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(request_one, api_key, model, sample, args.image_root): (model, sample["id"])
            for model, sample in jobs
        }
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results.append(result)
            status = "ok" if result.get("ok") else "error"
            print(f"{status:5} {result['model']:34} {result['sample_id']:>2} {result['elapsed_sec']:>7.3f}s")

    results.sort(key=lambda row: (row["model"], row["sample_id"]))
    payload = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "prompt": PROMPT,
        "samples": samples,
        "summary": summarize(results),
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
