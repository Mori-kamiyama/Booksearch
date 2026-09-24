"""
Sarashina2.2-OCR 実行スクリプト（transformers 4.57.1 専用）。
.venv_sarashina の Python で直接実行する。sarashina_bench.py からサブプロセスとして呼ばれる。

使い方（直接）:
  .venv_sarashina/bin/python scripts/sarashina_runner.py <image_path> <output_json>
"""

import json
import math
import sys
import time
import types
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

import torch
from huggingface_hub import hf_hub_download
from PIL import Image
from transformers import AutoModelForCausalLM, BaseVideoProcessor, LlamaTokenizerFast, set_seed
from transformers.utils import import_utils


MODEL_ID = "sbintuitions/sarashina2.2-ocr"


class DummyVideoProcessor(BaseVideoProcessor):
    model_input_names = []

    def preprocess(self, videos=None, **kwargs):
        raise NotImplementedError


def install_qwen2_vl_smart_resize_shim() -> None:
    module_name = "transformers.models.qwen2_vl.image_processing_qwen2_vl"
    if module_name in sys.modules:
        return
    shim = types.ModuleType(module_name)

    def smart_resize(height, width, factor=28, min_pixels=56 * 56, max_pixels=14 * 14 * 4 * 1280):
        if max(height, width) / min(height, width) > 200:
            raise ValueError("aspect ratio too large")
        h_bar = round(height / factor) * factor
        w_bar = round(width / factor) * factor
        if h_bar * w_bar > max_pixels:
            beta = math.sqrt((height * width) / max_pixels)
            h_bar = max(factor, math.floor(height / beta / factor) * factor)
            w_bar = max(factor, math.floor(width / beta / factor) * factor)
        elif h_bar * w_bar < min_pixels:
            beta = math.sqrt(min_pixels / (height * width))
            h_bar = math.ceil(height * beta / factor) * factor
            w_bar = math.ceil(width * beta / factor) * factor
        return h_bar, w_bar

    shim.smart_resize = smart_resize
    sys.modules[module_name] = shim


def relax_torchvision_backend_checks() -> None:
    import_utils.is_torchvision_available = lambda: True
    import_utils.is_torchvision_v2_available = lambda: False
    if "torchvision" in import_utils.BACKENDS_MAPPING:
        _, msg = import_utils.BACKENDS_MAPPING["torchvision"]
        import_utils.BACKENDS_MAPPING["torchvision"] = (lambda: True, msg)


def load_patched_processor_class():
    source_path = Path(hf_hub_download(MODEL_ID, "processing_sarashina2_vision.py", repo_type="model"))
    source = "\n".join(
        line
        for line in source_path.read_text(encoding="utf-8").splitlines()
        if ".register(" not in line and ".register_for_auto_class(" not in line
    )
    module = module_from_spec(spec_from_loader("patched_sarashina2_processing", loader=None))
    module.__file__ = str(source_path)
    exec(compile(source, str(source_path), "exec"), module.__dict__)
    return module.Sarashina2VisionProcessor, module.Sarashina2VisionImageProcessor, module.Sarashina2VisionVideoProcessor


def resolve_device() -> str:
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def load_model(device: str):
    install_qwen2_vl_smart_resize_shim()
    relax_torchvision_backend_checks()
    processor_class, image_processor_class, _ = load_patched_processor_class()
    image_processor = image_processor_class.from_pretrained(MODEL_ID, trust_remote_code=True)
    tokenizer = LlamaTokenizerFast.from_pretrained(MODEL_ID, trust_remote_code=True)
    processor = processor_class(
        image_processor=image_processor,
        video_processor=DummyVideoProcessor(),
        tokenizer=tokenizer,
        chat_template=tokenizer.chat_template,
    )
    dtype = torch.float16 if device == "mps" else torch.float32
    model = AutoModelForCausalLM.from_pretrained(MODEL_ID, torch_dtype=dtype, trust_remote_code=True).to(device)
    model.eval()
    return processor, model


def run_ocr(processor, model, image_path: Path) -> tuple[str, float]:
    with Image.open(image_path).convert("RGB") as image:
        inputs = processor.apply_chat_template(
            [{"role": "user", "content": [{"type": "image", "image": image}]}],
            tokenize=True,
            add_generation_prompt=True,
            return_dict=True,
            return_tensors="pt",
        )
    inputs = {k: v.to(model.device) for k, v in inputs.items()}
    t0 = time.perf_counter()
    with torch.inference_mode():
        output_ids = model.generate(
            **inputs, max_new_tokens=3000, temperature=0.0, top_p=0.95, repetition_penalty=1.2, use_cache=True
        )
    elapsed = time.perf_counter() - t0
    prompt_len = inputs["input_ids"].shape[1]
    markdown = processor.decode(output_ids[0][prompt_len:], skip_special_tokens=True).strip()
    return markdown, elapsed


def markdown_to_strings(markdown: str) -> list[str]:
    lines = []
    for line in markdown.splitlines():
        line = line.strip().lstrip("#*-|・ ")
        if len(line) >= 2:
            lines.append(line)
    return lines


def main() -> int:
    if len(sys.argv) < 3:
        print("使い方: sarashina_runner.py <image_path> <output_json>", file=sys.stderr)
        return 1

    image_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])

    set_seed(42)
    device = resolve_device()
    print(f"device={device}  モデル読み込み中...", file=sys.stderr)
    processor, model = load_model(device)

    print(f"推論中: {image_path.name}", file=sys.stderr)
    markdown, elapsed = run_ocr(processor, model, image_path)
    strings = markdown_to_strings(markdown)

    print(f"推論時間: {elapsed:.1f}s  行数: {len(strings)}", file=sys.stderr)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            {"model": MODEL_ID, "image": str(image_path), "device": device,
             "elapsed_sec": round(elapsed, 2), "markdown": markdown, "strings": strings},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    print(f"保存: {output_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
