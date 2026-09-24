"""LoRA smoke/full training for PaddleOCR-VL-1.6 using ms-swift on Modal.

Data volume layout:
  /data/crops/*.jpg
  /data/dataset/{train,validation,test}.jsonl
"""

from __future__ import annotations

import json
import subprocess
import tarfile
import time
from pathlib import Path

import modal


app = modal.App("booksearch-paddleocr-vl-lora")
data_volume = modal.Volume.from_name("booksearch-ocr-sft-data", create_if_missing=True)
cache_volume = modal.Volume.from_name("booksearch-hf-cache", create_if_missing=True)
output_volume = modal.Volume.from_name("booksearch-ocr-sft-checkpoints", create_if_missing=True)
HUMAN_DATASET_DIR = Path("/data/human_sft_538/ms_swift")
ACTIVE_REVIEW_DIR = Path("/data/active_review_v1/training_prepared")
PSEUDO_LORA_CHECKPOINT = (
    "/checkpoints/paddleocr-vl-1.6-lora-1790177779/"
    "v0-20260923-153640/checkpoint-702"
)
HUMAN_SFT_CHECKPOINT = (
    "/checkpoints/paddleocr-vl-1.6-human-sft-1790235336/"
    "v0-20260924-073602/checkpoint-146"
)

image = (
    modal.Image.from_registry(
        "vllm/vllm-openai:v0.30.0",
        add_python="3.12",
        setup_dockerfile_commands=["ENTRYPOINT []"],
    )
    .pip_install("ms-swift==4.5.3", "torchvision")
    .add_local_file(
        "modal_apps/ms_swift_paddleocr16_patch.py",
        "/root/ms_swift_paddleocr16_patch.py",
        copy=True,
    )
    .env({"HF_HOME": "/root/.cache/huggingface", "USE_HF": "1"})
)


def rewrite_paths(source: Path, destination: Path, limit: int | None = None) -> None:
    old_prefix = "/Users/yuta/date/classroom/Booksearch/outputs/qwen3_vl_modal_spines/crops/"
    with source.open(encoding="utf-8") as input_file, destination.open("w", encoding="utf-8") as output_file:
        for index, line in enumerate(input_file):
            if limit is not None and index >= limit:
                break
            row = json.loads(line)
            row["images"] = [path.replace(old_prefix, "/tmp/crops/") for path in row["images"]]
            output_file.write(json.dumps(row, ensure_ascii=False) + "\n")


@app.function(
    image=image,
    gpu="L4",
    timeout=60 * 60,
    volumes={
        "/data": data_volume,
        "/root/.cache/huggingface": cache_volume,
        "/checkpoints": output_volume,
    },
)
def train(max_steps: int = 5) -> dict:
    with tarfile.open("/data/crops.tar") as archive:
        archive.extractall("/tmp", filter="data")
    train_path = Path("/tmp/train.jsonl")
    validation_path = Path("/tmp/validation.jsonl")
    rewrite_paths(Path("/data/dataset/ms_swift/train.jsonl"), train_path)
    rewrite_paths(Path("/data/dataset/ms_swift/validation.jsonl"), validation_path)
    run_name = f"paddleocr-vl-1.6-lora-{int(time.time())}"
    output_dir = Path("/checkpoints") / run_name
    command = [
        "swift",
        "sft",
        "--model",
        "PaddlePaddle/PaddleOCR-VL-1.6",
        "--custom_register_path",
        "/root/ms_swift_paddleocr16_patch.py",
        "--dataset",
        str(train_path),
        "--val_dataset",
        str(validation_path),
        "--tuner_type",
        "lora",
        "--torch_dtype",
        "bfloat16",
        "--max_steps",
        str(max_steps),
        "--per_device_train_batch_size",
        "1",
        "--per_device_eval_batch_size",
        "1",
        "--gradient_accumulation_steps",
        "8",
        "--learning_rate",
        "1e-4",
        "--lora_rank",
        "16",
        "--lora_alpha",
        "32",
        "--target_modules",
        "all-linear",
        "--freeze_vit",
        "true",
        "--freeze_aligner",
        "true",
        "--max_length",
        "1024",
        "--max_pixels",
        "262144",
        "--eval_steps",
        str(max_steps),
        "--save_steps",
        str(max_steps),
        "--save_total_limit",
        "1",
        "--logging_steps",
        "1",
        "--warmup_ratio",
        "0.03",
        "--dataloader_num_workers",
        "2",
        "--dataset_num_proc",
        "4",
        "--output_dir",
        str(output_dir),
    ]
    started = time.perf_counter()
    subprocess.run(command, check=True)
    output_volume.commit()
    return {
        "run_name": run_name,
        "output_dir": str(output_dir),
        "max_steps": max_steps,
        "elapsed_sec": time.perf_counter() - started,
    }


@app.function(
    image=image,
    gpu="L4",
    timeout=30 * 60,
    volumes={
        "/data": data_volume,
        "/root/.cache/huggingface": cache_volume,
        "/checkpoints": output_volume,
    },
)
def train_human(max_steps: int = 146, learning_rate: str = "2e-5") -> dict:
    """Continue the pseudo-label LoRA using the conservative human-reviewed split."""
    with tarfile.open("/data/crops.tar") as archive:
        archive.extractall("/tmp", filter="data")
    train_path = Path("/tmp/human_train.jsonl")
    validation_path = Path("/tmp/human_validation.jsonl")
    rewrite_paths(HUMAN_DATASET_DIR / "train.jsonl", train_path)
    rewrite_paths(HUMAN_DATASET_DIR / "validation.jsonl", validation_path)
    run_name = f"paddleocr-vl-1.6-human-sft-{int(time.time())}"
    output_dir = Path("/checkpoints") / run_name
    command = [
        "swift",
        "sft",
        "--model",
        "PaddlePaddle/PaddleOCR-VL-1.6",
        "--custom_register_path",
        "/root/ms_swift_paddleocr16_patch.py",
        "--dataset",
        str(train_path),
        "--val_dataset",
        str(validation_path),
        "--tuner_type",
        "lora",
        "--adapters",
        PSEUDO_LORA_CHECKPOINT,
        "--torch_dtype",
        "bfloat16",
        "--max_steps",
        str(max_steps),
        "--per_device_train_batch_size",
        "1",
        "--per_device_eval_batch_size",
        "1",
        "--gradient_accumulation_steps",
        "8",
        "--learning_rate",
        learning_rate,
        "--lora_rank",
        "16",
        "--lora_alpha",
        "32",
        "--target_modules",
        "all-linear",
        "--freeze_vit",
        "true",
        "--freeze_aligner",
        "true",
        "--max_length",
        "1024",
        "--max_pixels",
        "262144",
        "--eval_steps",
        "49",
        "--save_steps",
        str(max_steps),
        "--save_total_limit",
        "1",
        "--logging_steps",
        "1",
        "--warmup_ratio",
        "0.03",
        "--dataloader_num_workers",
        "2",
        "--dataset_num_proc",
        "4",
        "--output_dir",
        str(output_dir),
    ]
    started = time.perf_counter()
    subprocess.run(command, check=True)
    output_volume.commit()
    return {
        "run_name": run_name,
        "output_dir": str(output_dir),
        "max_steps": max_steps,
        "learning_rate": learning_rate,
        "starting_adapter": PSEUDO_LORA_CHECKPOINT,
        "elapsed_sec": time.perf_counter() - started,
    }


@app.function(
    image=image,
    gpu="L4",
    timeout=30 * 60,
    volumes={
        "/data": data_volume,
        "/root/.cache/huggingface": cache_volume,
        "/checkpoints": output_volume,
    },
)
def train_active_sft(max_steps: int = 92, learning_rate: str = "1e-5") -> dict:
    """Continue the human-SFT adapter on the clean active-review acceptances."""
    with tarfile.open("/data/crops.tar") as archive:
        archive.extractall("/tmp", filter="data")
    train_path = Path("/tmp/active_sft_train.jsonl")
    validation_path = Path("/tmp/human_validation.jsonl")
    rewrite_paths(ACTIVE_REVIEW_DIR / "sft_train.jsonl", train_path)
    rewrite_paths(HUMAN_DATASET_DIR / "validation.jsonl", validation_path)
    run_name = f"paddleocr-vl-1.6-active-sft-{int(time.time())}"
    output_dir = Path("/checkpoints") / run_name
    command = [
        "swift", "sft",
        "--model", "PaddlePaddle/PaddleOCR-VL-1.6",
        "--custom_register_path", "/root/ms_swift_paddleocr16_patch.py",
        "--dataset", str(train_path),
        "--val_dataset", str(validation_path),
        "--tuner_type", "lora",
        "--adapters", HUMAN_SFT_CHECKPOINT,
        "--torch_dtype", "bfloat16",
        "--max_steps", str(max_steps),
        "--per_device_train_batch_size", "1",
        "--per_device_eval_batch_size", "1",
        "--gradient_accumulation_steps", "8",
        "--learning_rate", learning_rate,
        "--lora_rank", "16",
        "--lora_alpha", "32",
        "--target_modules", "all-linear",
        "--freeze_vit", "true",
        "--freeze_aligner", "true",
        "--max_length", "1024",
        "--max_pixels", "262144",
        "--eval_steps", "46",
        "--save_steps", str(max_steps),
        "--save_total_limit", "1",
        "--logging_steps", "1",
        "--warmup_ratio", "0.03",
        "--dataloader_num_workers", "2",
        "--dataset_num_proc", "4",
        "--output_dir", str(output_dir),
    ]
    started = time.perf_counter()
    subprocess.run(command, check=True)
    output_volume.commit()
    return {
        "run_name": run_name,
        "output_dir": str(output_dir),
        "max_steps": max_steps,
        "learning_rate": learning_rate,
        "starting_adapter": HUMAN_SFT_CHECKPOINT,
        "elapsed_sec": time.perf_counter() - started,
    }


@app.function(
    image=image,
    gpu="L4",
    timeout=45 * 60,
    volumes={
        "/data": data_volume,
        "/root/.cache/huggingface": cache_volume,
        "/checkpoints": output_volume,
    },
)
def train_active_dpo(
    adapter_dir: str,
    max_steps: int = 32,
    learning_rate: str = "5e-6",
) -> dict:
    """Conservative DPO with chosen-NLL regularization on corrected predictions."""
    with tarfile.open("/data/crops.tar") as archive:
        archive.extractall("/tmp", filter="data")
    train_path = Path("/tmp/preferences_train.jsonl")
    validation_path = Path("/tmp/preferences_validation.jsonl")
    rewrite_paths(ACTIVE_REVIEW_DIR / "preferences_train.jsonl", train_path)
    rewrite_paths(ACTIVE_REVIEW_DIR / "preferences_validation.jsonl", validation_path)
    run_name = f"paddleocr-vl-1.6-active-dpo-{int(time.time())}"
    output_dir = Path("/checkpoints") / run_name
    command = [
        "swift", "rlhf",
        "--rlhf_type", "dpo",
        "--model", "PaddlePaddle/PaddleOCR-VL-1.6",
        "--custom_register_path", "/root/ms_swift_paddleocr16_patch.py",
        "--dataset", str(train_path),
        "--val_dataset", str(validation_path),
        "--tuner_type", "lora",
        "--adapters", adapter_dir,
        "--ref_adapters", adapter_dir,
        "--torch_dtype", "bfloat16",
        "--max_steps", str(max_steps),
        "--per_device_train_batch_size", "1",
        "--per_device_eval_batch_size", "1",
        "--gradient_accumulation_steps", "8",
        "--learning_rate", learning_rate,
        "--lora_rank", "16",
        "--lora_alpha", "32",
        "--target_modules", "all-linear",
        "--freeze_vit", "true",
        "--freeze_aligner", "true",
        "--max_length", "1024",
        "--max_pixels", "262144",
        "--beta", "0.1",
        "--rpo_alpha", "0.5",
        "--eval_steps", "16",
        "--save_steps", str(max_steps),
        "--save_total_limit", "1",
        "--logging_steps", "1",
        "--warmup_ratio", "0.03",
        "--dataloader_num_workers", "2",
        "--dataset_num_proc", "4",
        "--output_dir", str(output_dir),
    ]
    started = time.perf_counter()
    subprocess.run(command, check=True)
    output_volume.commit()
    return {
        "run_name": run_name,
        "output_dir": str(output_dir),
        "max_steps": max_steps,
        "learning_rate": learning_rate,
        "starting_adapter": adapter_dir,
        "elapsed_sec": time.perf_counter() - started,
    }


@app.function(
    image=image,
    gpu="L4",
    timeout=30 * 60,
    volumes={
        "/data": data_volume,
        "/root/.cache/huggingface": cache_volume,
        "/checkpoints": output_volume,
    },
)
def evaluate(adapter_dir: str, limit: int = 100, human_test: bool = False) -> dict:
    """Run deterministic inference on the held-out test split."""
    with tarfile.open("/data/crops.tar") as archive:
        archive.extractall("/tmp", filter="data")
    test_path = Path("/tmp/test.jsonl")
    source = HUMAN_DATASET_DIR / "test.jsonl" if human_test else Path("/data/dataset/ms_swift/test.jsonl")
    rewrite_paths(source, test_path, limit=limit)
    result_path = Path(adapter_dir) / f"{'human_' if human_test else ''}test_predictions_{limit}.jsonl"
    command = [
        "swift",
        "infer",
        "--model",
        "PaddlePaddle/PaddleOCR-VL-1.6",
        "--adapters",
        adapter_dir,
        "--custom_register_path",
        "/root/ms_swift_paddleocr16_patch.py",
        "--val_dataset",
        str(test_path),
        "--infer_backend",
        "transformers",
        "--temperature",
        "0",
        "--max_new_tokens",
        "160",
        "--max_batch_size",
        "8",
        "--result_path",
        str(result_path),
    ]
    started = time.perf_counter()
    subprocess.run(command, check=True)
    output_volume.commit()
    return {
        "adapter_dir": adapter_dir,
        "result_path": str(result_path),
        "items": limit,
        "elapsed_sec": time.perf_counter() - started,
    }


@app.function(
    image=image,
    gpu="L4",
    timeout=45 * 60,
    volumes={
        "/data": data_volume,
        "/root/.cache/huggingface": cache_volume,
        "/checkpoints": output_volume,
    },
)
def infer_active_review(adapter_dir: str, batch: str = "v1") -> dict:
    """Precompute model suggestions for a smooth local review workflow."""
    with tarfile.open("/data/crops.tar") as archive:
        archive.extractall("/tmp", filter="data")
    input_path = Path("/tmp/active_review.jsonl")
    rewrite_paths(Path(f"/data/active_review_{batch}/input.jsonl"), input_path)
    result_path = Path(adapter_dir) / f"active_review_{batch}_predictions.jsonl"
    command = [
        "swift",
        "infer",
        "--model",
        "PaddlePaddle/PaddleOCR-VL-1.6",
        "--adapters",
        adapter_dir,
        "--custom_register_path",
        "/root/ms_swift_paddleocr16_patch.py",
        "--val_dataset",
        str(input_path),
        "--infer_backend",
        "transformers",
        "--temperature",
        "0",
        "--max_new_tokens",
        "160",
        "--max_batch_size",
        "8",
        "--result_path",
        str(result_path),
    ]
    started = time.perf_counter()
    subprocess.run(command, check=True)
    output_volume.commit()
    return {
        "adapter_dir": adapter_dir,
        "batch": batch,
        "result_path": str(result_path),
        "elapsed_sec": time.perf_counter() - started,
    }


@app.local_entrypoint()
def main(
    max_steps: int = 5,
    adapter_dir: str = "",
    test_limit: int = 100,
    human: bool = False,
    human_test: bool = False,
    active_review: bool = False,
    active_batch: str = "v1",
    active_sft: bool = False,
    active_dpo: bool = False,
) -> None:
    if active_dpo:
        if not adapter_dir:
            raise ValueError("--adapter-dir is required for --active-dpo")
        result = train_active_dpo.remote(adapter_dir=adapter_dir, max_steps=max_steps)
    elif active_sft:
        result = train_active_sft.remote(max_steps=max_steps)
    elif adapter_dir and active_review:
        result = infer_active_review.remote(adapter_dir=adapter_dir, batch=active_batch)
    elif adapter_dir:
        result = evaluate.remote(adapter_dir=adapter_dir, limit=test_limit, human_test=human_test)
    elif human:
        result = train_human.remote(max_steps=max_steps)
    else:
        result = train.remote(max_steps=max_steps)
    print(json.dumps(result, ensure_ascii=False, indent=2))
