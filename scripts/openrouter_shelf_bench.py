"""
OpenRouter経由で単一モデルを benchmark/ground_truth_shelf.json の全クロップ画像に対して実行する。
scripts/openrouter_ocr.py と同じ「画像 → JSON配列」方式で1画像=1リクエスト。

使い方:
  uv run python scripts/openrouter_shelf_bench.py --model google/gemini-3.1-flash-lite-preview
  uv run python scripts/openrouter_shelf_bench.py --model openai/gpt-5.4-mini --reasoning-effort high
  uv run python scripts/openrouter_shelf_bench.py --model google/gemini-3.5-flash --limit 10
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

from openai import OpenAI


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from openrouter_ocr import PROMPT, image_to_data_url, model_to_dir_name  # noqa: E402

GROUND_TRUTH_PATH = REPO_ROOT / "benchmark/ground_truth_shelf.json"
REQUEST_TIMEOUT_SEC = 180


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OpenRouter VLMを棚OCRベンチマークの全画像に対して実行します。")
    parser.add_argument("--model", required=True, help="OpenRouter モデル ID")
    parser.add_argument("--label", default=None, help="出力ディレクトリ名に使う識別子（省略時はモデルIDから生成）")
    parser.add_argument("--reasoning-effort", default=None, choices=["minimal", "low", "medium", "high"])
    parser.add_argument("--limit", type=int, default=None, help="実行する画像数の上限（デフォルト: 全件）")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--ground-truth", default=str(GROUND_TRUTH_PATH), help="正解データJSONのパス")
    parser.add_argument("--provider-order", nargs="+", default=None, help="優先するOpenRouterプロバイダ名（例: DeepInfra Together）")
    return parser.parse_args()


def extract_titles(
    client: OpenAI, model: str, image_path: Path, reasoning_effort: str | None, provider_order: list[str] | None
) -> tuple[list[str], float]:
    data_url = image_to_data_url(image_path)

    extra_body = {}
    if reasoning_effort:
        extra_body["reasoning"] = {"effort": reasoning_effort}
    if provider_order:
        extra_body["provider"] = {"order": provider_order, "allow_fallbacks": False}

    t0 = time.perf_counter()
    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": PROMPT},
                ],
            }
        ],
        extra_body=extra_body or None,
        timeout=REQUEST_TIMEOUT_SEC,
    )
    elapsed = time.perf_counter() - t0

    choice = response.choices[0]
    raw = (choice.message.content or "").strip()

    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        titles = json.loads(raw)
        if not isinstance(titles, list):
            titles = [str(titles)]
    except json.JSONDecodeError:
        titles = [line.strip().lstrip("-・ ") for line in raw.splitlines() if line.strip()]

    usage = getattr(response, "usage", None)
    tokens = {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
    } if usage else None

    return titles, elapsed, tokens


def main() -> int:
    args = parse_args()

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY 環境変数が未設定です。")

    ground_truth = json.loads(Path(args.ground_truth).read_text(encoding="utf-8"))
    samples = ground_truth["samples"]
    if args.limit:
        samples = samples[: args.limit]

    label = args.label or model_to_dir_name(args.model)
    output_dir = Path(args.output_dir) if args.output_dir else REPO_ROOT / f"outputs/shelf_ocr_bench/{label}"
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "summary.json"

    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)

    print(f"モデル: {args.model}  対象画像: {len(samples)}件")

    def save(results: list[dict]) -> None:
        total_elapsed = sum(r["elapsed_sec"] for r in results)
        payload = {
            "model": args.model,
            "reasoning_effort": args.reasoning_effort,
            "provider_order": args.provider_order,
            "n_images": len(results),
            "total_elapsed_sec": round(total_elapsed, 2),
            "results": results,
        }
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    results = []
    for i, sample in enumerate(samples, 1):
        image_path = REPO_ROOT / sample["image"]
        print(f"[{i}/{len(samples)}] {image_path.name} ...", end=" ", flush=True)
        try:
            titles, elapsed, tokens = extract_titles(
                client, args.model, image_path, args.reasoning_effort, args.provider_order
            )
            print(f"{len(titles)}件 {elapsed:.1f}s")
        except Exception as e:  # noqa: BLE001
            print(f"失敗: {e}")
            titles, elapsed, tokens = [], 0.0, None
        results.append(
            {
                "image": sample["image"],
                "titles": titles,
                "elapsed_sec": round(elapsed, 2),
                "tokens": tokens,
            }
        )
        save(results)  # 画像ごとに逐次保存（killされても途中結果が残る）

    print(f"保存: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
