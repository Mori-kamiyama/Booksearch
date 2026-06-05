"""
OpenRouter 経由で VLM を使って本棚画像から書名を抽出するベンチマーク用スクリプト。
Gemini と同じ「画像全体 → JSON list」アプローチ。

使い方:
  uv run python scripts/openrouter_ocr.py --model anthropic/claude-3-haiku outputs/add_tag/add_tag_warped.jpg
  uv run python scripts/openrouter_ocr.py --model meta-llama/llama-3.2-11b-vision-instruct outputs/add_tag/add_tag_warped.jpg
  uv run python scripts/openrouter_ocr.py --model amazon/nova-lite-v1 outputs/add_tag/add_tag_warped.jpg
"""

import argparse
import base64
import json
import os
import time
from pathlib import Path

from openai import OpenAI


REPO_ROOT = Path(__file__).resolve().parent.parent

PROMPT = """\
この画像は本棚の一区画です。
写っているすべての本の背表紙タイトルを読み取り、JSON 配列として返してください。

ルール:
- 見えているタイトルをすべて列挙する（部分的にしか見えないものも含む）
- 1冊につき1つのエントリ
- タイトルのみ（著者名・出版社は除く）
- 読み取れない本は除外
- 余計な説明は不要。JSON 配列だけ返すこと

出力例:
["タイトルA", "タイトルB", "タイトルC"]
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OpenRouter VLM で書名を抽出します。")
    parser.add_argument("image", help="入力画像パス")
    parser.add_argument("--model", required=True, help="OpenRouter モデル ID (例: anthropic/claude-3-haiku)")
    parser.add_argument("--output-dir", default=None)
    return parser.parse_args()


def model_to_dir_name(model_id: str) -> str:
    return model_id.replace("/", "_").replace(".", "_").replace("-", "_")


def image_to_data_url(image_path: Path) -> str:
    suffix = image_path.suffix.lower()
    mime = "image/jpeg" if suffix in (".jpg", ".jpeg") else "image/png"
    b64 = base64.b64encode(image_path.read_bytes()).decode()
    return f"data:{mime};base64,{b64}"


def extract_titles(client: OpenAI, model: str, image_path: Path) -> tuple[list[str], float]:
    data_url = image_to_data_url(image_path)

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
    )
    elapsed = time.perf_counter() - t0

    raw = response.choices[0].message.content.strip()

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

    return titles, elapsed


def main() -> int:
    args = parse_args()
    image_path = REPO_ROOT / args.image
    if not image_path.exists():
        raise FileNotFoundError(f"画像が見つかりません: {image_path}")

    dir_name = model_to_dir_name(args.model)
    output_dir = REPO_ROOT / (args.output_dir or f"outputs/add_tag_{dir_name}")
    output_dir.mkdir(parents=True, exist_ok=True)

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY 環境変数が未設定です。")

    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)

    print(f"画像: {image_path.name}")
    print(f"モデル: {args.model}")
    print("推論中...")

    titles, elapsed = extract_titles(client, args.model, image_path)

    print(f"\n推論時間: {elapsed:.1f}s")
    print(f"検出書名: {len(titles)}件")
    for t in titles:
        print(f"  {t}")

    payload = {
        "model": args.model,
        "image": str(image_path),
        "elapsed_sec": round(elapsed, 2),
        "titles": titles,
    }
    out_path = output_dir / "summary.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nsummary_json: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
