import argparse
import json
from pathlib import Path

import torch
from PIL import Image
from transformers import AutoModelForCausalLM, AutoProcessor, set_seed


MODEL_ID = "sbintuitions/sarashina2.2-ocr"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sarashina2.2-OCR で画像を OCR して Markdown 文字列を保存します。"
    )
    parser.add_argument("image", help="OCR したい画像ファイル")
    parser.add_argument(
        "--output",
        help="結果を書き出す JSON ファイル。省略時は outputs/sarashina/<stem>.json",
    )
    parser.add_argument("--max-new-tokens", type=int, default=3000)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--device",
        choices=("auto", "mps", "cpu"),
        default="auto",
        help="実行デバイス。auto は MPS 優先",
    )
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
    return "cpu"


def build_output_path(image_path: Path, explicit_output: str | None) -> Path:
    if explicit_output:
        return Path(explicit_output)
    return Path("outputs/sarashina") / f"{image_path.stem}.json"


def load_model(device: str):
    processor = AutoProcessor.from_pretrained(MODEL_ID, trust_remote_code=True)
    if device == "mps":
        model = AutoModelForCausalLM.from_pretrained(
            MODEL_ID,
            torch_dtype=torch.float16,
            trust_remote_code=True,
        ).to("mps")
    else:
        model = AutoModelForCausalLM.from_pretrained(
            MODEL_ID,
            torch_dtype=torch.float32,
            trust_remote_code=True,
        ).to("cpu")
    model.eval()
    return processor, model


def run_ocr(
    processor,
    model,
    image_path: Path,
    max_new_tokens: int,
    temperature: float,
) -> str:
    with Image.open(image_path).convert("RGB") as image:
        message = [{"role": "user", "content": [{"type": "image", "image": image}]}]
        inputs = processor.apply_chat_template(
            message,
            tokenize=True,
            add_generation_prompt=True,
            return_dict=True,
            return_tensors="pt",
        )

    inputs = {key: value.to(model.device) for key, value in inputs.items()}
    generation_kwargs = {
        **inputs,
        "max_new_tokens": max_new_tokens,
        "temperature": temperature,
        "top_p": 0.95,
        "repetition_penalty": 1.2,
        "use_cache": True,
    }
    with torch.inference_mode():
        output_ids = model.generate(**generation_kwargs)

    prompt_length = inputs["input_ids"].shape[1]
    generated_ids = output_ids[0][prompt_length:]
    return processor.decode(generated_ids, skip_special_tokens=True).strip()


def main() -> int:
    args = parse_args()
    image_path = Path(args.image)
    if not image_path.exists():
        raise FileNotFoundError(f"画像が見つかりません: {image_path}")

    output_path = build_output_path(image_path, args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    device = resolve_device(args.device)
    set_seed(args.seed)
    processor, model = load_model(device)
    markdown = run_ocr(
        processor,
        model,
        image_path=image_path,
        max_new_tokens=args.max_new_tokens,
        temperature=args.temperature,
    )

    payload = {
        "model_id": MODEL_ID,
        "image": str(image_path),
        "device": device,
        "max_new_tokens": args.max_new_tokens,
        "temperature": args.temperature,
        "markdown": markdown,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"output_json: {output_path}")
    print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
