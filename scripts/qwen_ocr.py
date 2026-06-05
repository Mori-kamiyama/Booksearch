"""
Qwen2.5-VL-7B-Instruct で本棚画像から書名を抽出するスクリプト。
Gemini と同じ「画像全体 → JSON list」アプローチ。

使い方:
  uv run python scripts/qwen_ocr.py outputs/add_tag/add_tag_warped.jpg
  uv run python scripts/qwen_ocr.py outputs/add_tag/add_tag_warped.jpg --output-dir outputs/add_tag_qwen
"""

import argparse
import json
import time
from pathlib import Path

import torch
from PIL import Image
from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration


REPO_ROOT = Path(__file__).resolve().parent.parent
MODEL_ID = "Qwen/Qwen2.5-VL-7B-Instruct"

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
    parser = argparse.ArgumentParser(description="Qwen2.5-VL で書名を抽出します。")
    parser.add_argument("image", help="入力画像パス")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--device", choices=("auto", "mps", "cpu"), default="auto")
    parser.add_argument("--max-new-tokens", type=int, default=1024)
    return parser.parse_args()


def resolve_device(device_arg: str) -> str:
    if device_arg != "auto":
        return device_arg
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def load_model(device: str):
    print(f"モデル読み込み中: {MODEL_ID}  (device={device})")
    processor = AutoProcessor.from_pretrained(MODEL_ID)
    model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
        MODEL_ID,
        torch_dtype="auto",
        device_map=device,
    )
    model.eval()
    return processor, model


def extract_titles(processor, model, image_path: Path, max_new_tokens: int) -> tuple[list[str], float]:
    image = Image.open(image_path).convert("RGB")
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image", "image": image},
                {"type": "text", "text": PROMPT},
            ],
        }
    ]

    text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = processor(text=[text], images=[image], return_tensors="pt").to(model.device)

    t0 = time.perf_counter()
    with torch.inference_mode():
        output_ids = model.generate(**inputs, max_new_tokens=max_new_tokens)
    elapsed = time.perf_counter() - t0

    prompt_len = inputs["input_ids"].shape[1]
    generated = output_ids[0][prompt_len:]
    raw = processor.decode(generated, skip_special_tokens=True).strip()

    # JSONパース
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
        # JSON失敗時は改行で分割
        titles = [line.strip().lstrip("-・ ") for line in raw.splitlines() if line.strip()]

    return titles, elapsed


def main() -> int:
    args = parse_args()
    image_path = REPO_ROOT / args.image
    if not image_path.exists():
        raise FileNotFoundError(f"画像が見つかりません: {image_path}")

    stem = image_path.stem
    output_dir = REPO_ROOT / (args.output_dir or f"outputs/{stem}_qwen")
    output_dir.mkdir(parents=True, exist_ok=True)

    device = resolve_device(args.device)
    processor, model = load_model(device)

    print(f"推論中: {image_path.name}")
    titles, elapsed = extract_titles(processor, model, image_path, args.max_new_tokens)

    print(f"\n推論時間: {elapsed:.1f}s")
    print(f"検出書名: {len(titles)}件")
    for t in titles:
        print(f"  {t}")

    payload = {
        "model": MODEL_ID,
        "image": str(image_path),
        "device": device,
        "elapsed_sec": round(elapsed, 2),
        "titles": titles,
    }
    out_path = output_dir / "summary.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nsummary_json: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
