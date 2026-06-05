"""
GLM-OCR (zai-org/GLM-OCR, 0.9B) で画像から書名を抽出するベンチマーク用スクリプト。
transformers でローカル実行するため API 費用なし。

使い方:
  uv run python scripts/glm_ocr.py outputs/add_tag/add_tag_warped.jpg
  uv run python scripts/glm_ocr.py data/add_tag.jpg --output-dir outputs/add_tag_glm
  uv run python scripts/glm_ocr.py --help
"""

import argparse
import json
import time
from pathlib import Path

import torch
from PIL import Image
from transformers import AutoModelForCausalLM, AutoProcessor


REPO_ROOT = Path(__file__).resolve().parent.parent
MODEL_ID = "zai-org/GLM-OCR"

PROMPT = "この画像に写っているすべてのテキストを認識してください。"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="GLM-OCR で画像からテキストを抽出します。")
    parser.add_argument("image", help="入力画像パス")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="結果の出力先ディレクトリ（省略時は outputs/add_tag_glm）",
    )
    parser.add_argument(
        "--device",
        choices=("auto", "mps", "cpu"),
        default="auto",
    )
    parser.add_argument("--max-new-tokens", type=int, default=2048)
    return parser.parse_args()


def resolve_device(device_arg: str) -> str:
    if device_arg == "cpu":
        return "cpu"
    if device_arg == "mps":
        if not torch.backends.mps.is_available():
            raise RuntimeError("MPS が利用できません。--device cpu を試してください。")
        return "mps"
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def load_model(device: str):
    print(f"モデル読み込み中: {MODEL_ID}  (device={device})")
    dtype = torch.float16 if device in ("mps", "cuda") else torch.float32
    processor = AutoProcessor.from_pretrained(MODEL_ID, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_ID,
        trust_remote_code=True,
        torch_dtype=dtype,
    ).to(device)
    model.eval()
    return processor, model


def run_ocr(processor, model, image_path: Path, max_new_tokens: int) -> tuple[str, float]:
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

    inputs = processor.apply_chat_template(
        messages,
        add_generation_prompt=True,
        return_tensors="pt",
        return_dict=True,
    )
    inputs = {k: v.to(model.device) for k, v in inputs.items()}

    t0 = time.perf_counter()
    with torch.inference_mode():
        output_ids = model.generate(**inputs, max_new_tokens=max_new_tokens)
    elapsed = time.perf_counter() - t0

    prompt_len = inputs["input_ids"].shape[1]
    generated = output_ids[0][prompt_len:]
    text = processor.decode(generated, skip_special_tokens=True).strip()
    return text, elapsed


def text_to_strings(text: str) -> list[str]:
    lines = []
    for line in text.splitlines():
        line = line.strip().lstrip("*#-・•●○▪︎ ")
        if len(line) >= 2:
            lines.append(line)
    return lines


def main() -> int:
    args = parse_args()
    image_path = REPO_ROOT / args.image
    if not image_path.exists():
        raise FileNotFoundError(f"画像が見つかりません: {image_path}")

    stem = image_path.stem
    output_dir = REPO_ROOT / (args.output_dir or f"outputs/{stem}_glm")
    output_dir.mkdir(parents=True, exist_ok=True)

    device = resolve_device(args.device)
    processor, model = load_model(device)

    print(f"推論中: {image_path.name}")
    raw_text, elapsed = run_ocr(processor, model, image_path, args.max_new_tokens)

    strings = text_to_strings(raw_text)

    print(f"\n推論時間: {elapsed:.1f}s")
    print(f"抽出テキスト行数: {len(strings)}")
    for s in strings:
        print(f"  {s}")

    payload = {
        "model": MODEL_ID,
        "image": str(image_path),
        "device": device,
        "elapsed_sec": round(elapsed, 2),
        "raw_text": raw_text,
        "strings": strings,
    }
    out_path = output_dir / "summary.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nsummary_json: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
