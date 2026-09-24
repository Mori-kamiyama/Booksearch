"""Fine-tune PP-OCRv6-small recognition on reviewed book-spine crops."""

from __future__ import annotations

import json
import re
import subprocess
import tarfile
import time
from pathlib import Path

import modal


app = modal.App("booksearch-ppocrv6-spine-rec")
data_volume = modal.Volume.from_name("booksearch-ppocrv6-data", create_if_missing=True)
output_volume = modal.Volume.from_name("booksearch-ppocrv6-checkpoints", create_if_missing=True)

PADDLEOCR_COMMIT = "main"
PRETRAINED_URL = (
    "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_pretrained_model/"
    "PP-OCRv6_small_rec_pretrained.pdparams"
)

image = (
    modal.Image.from_registry(
        "paddlepaddle/paddle:3.2.0-gpu-cuda12.6-cudnn9.5",
        setup_dockerfile_commands=["ENTRYPOINT []"],
    )
    .apt_install("git", "libgl1", "libglib2.0-0", "libsm6", "libxrender1", "libxcb1")
    .pip_install("pyyaml", "pillow", "opencv-python-headless", "lmdb", "rapidfuzz")
    .run_commands(
        f"git clone --depth 1 --branch {PADDLEOCR_COMMIT} https://github.com/PaddlePaddle/PaddleOCR.git /opt/PaddleOCR",
        "python -m pip install -r /opt/PaddleOCR/requirements.txt",
        f"python -c \"import urllib.request; urllib.request.urlretrieve('{PRETRAINED_URL}', '/opt/PP-OCRv6_small_rec_pretrained.pdparams')\"",
    )
    .add_local_file(
        "modal_apps/ppocrv6_spine_rec.yml",
        "/opt/PaddleOCR/configs/rec/PP-OCRv6/PP-OCRv6_spine_rec.yml",
        copy=True,
    )
)


def run(command: list[str], cwd: str = "/opt/PaddleOCR") -> None:
    print("RUN", " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def extract_dataset() -> Path:
    data_volume.reload()
    dataset_dir = Path("/tmp/spine_rec_data")
    dataset_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open("/data/spine_rec_data.tar") as archive:
        archive.extractall(dataset_dir, filter="data")
    return dataset_dir


def eval_checkpoint(checkpoint: str, destination: Path) -> dict:
    dataset_dir = Path("/tmp/spine_rec_data")
    config = "configs/rec/PP-OCRv6/PP-OCRv6_spine_rec.yml"
    metrics = {}
    destination.mkdir(parents=True, exist_ok=True)
    for split in ("validation", "test"):
        for orientation in ("cw", "ccw"):
            key = f"{split}_{orientation}"
            label_file = dataset_dir / f"{key}.txt"
            completed = subprocess.run(
                [
                    "python", "tools/eval.py", "-c", config, "-o",
                    f"Global.checkpoints={checkpoint}",
                    f"Eval.dataset.label_file_list=['{label_file}']",
                ],
                cwd="/opt/PaddleOCR", check=True, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
            (destination / f"eval_{key}.log").write_text(completed.stdout, encoding="utf-8")
            values = {}
            for name in ("acc", "norm_edit_dis", "fps"):
                matches = re.findall(rf"{name}:\s*([0-9.eE+-]+)", completed.stdout)
                values[name] = float(matches[-1]) if matches else None
            metrics[key] = values
    return metrics


@app.function(
    image=image,
    gpu="L4",
    timeout=60 * 60,
    volumes={"/data": data_volume, "/outputs": output_volume},
)
def train(epochs: int = 30, learning_rate: float = 0.0001) -> dict:
    dataset_dir = extract_dataset()
    run_dir = Path("/outputs") / f"ppocrv6-small-spines-{int(time.time())}"
    config = "configs/rec/PP-OCRv6/PP-OCRv6_spine_rec.yml"
    common = [
        "-o",
        "Global.pretrained_model=/opt/PP-OCRv6_small_rec_pretrained.pdparams",
        f"Global.save_model_dir={run_dir}",
        f"Global.epoch_num={epochs}",
        f"Optimizer.lr.learning_rate={learning_rate}",
    ]
    started = time.perf_counter()
    run(["python", "tools/train.py", "-c", config, *common])
    checkpoint = run_dir / "best_accuracy"
    if not Path(f"{checkpoint}.pdparams").exists():
        checkpoint = run_dir / "latest"
    evaluations = {}
    for split in ("validation", "test"):
        for orientation in ("cw", "ccw"):
            label_file = f"{dataset_dir}/{split}_{orientation}.txt"
            result_path = run_dir / f"eval_{split}_{orientation}.log"
            command = [
                "python", "tools/eval.py", "-c", config,
                "-o", f"Global.checkpoints={checkpoint}",
                f"Eval.dataset.label_file_list=['{label_file}']",
            ]
            completed = subprocess.run(
                command, cwd="/opt/PaddleOCR", check=True, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
            result_path.write_text(completed.stdout, encoding="utf-8")
            evaluations[f"{split}_{orientation}"] = str(result_path)

    run([
        "python", "tools/export_model.py", "-c", config,
        "-o", f"Global.pretrained_model={checkpoint}",
        f"Global.save_inference_dir={run_dir / 'inference'}",
    ])
    summary = {
        "run_dir": str(run_dir),
        "checkpoint": str(checkpoint),
        "epochs": epochs,
        "learning_rate": learning_rate,
        "elapsed_sec": time.perf_counter() - started,
        "evaluation_logs": evaluations,
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    output_volume.commit()
    return summary


@app.function(
    image=image,
    gpu="L4",
    timeout=15 * 60,
    volumes={"/data": data_volume, "/outputs": output_volume},
)
def evaluate_pretrained() -> dict:
    extract_dataset()
    destination = Path("/outputs/ppocrv6-small-pretrained-baseline")
    metrics = eval_checkpoint("/opt/PP-OCRv6_small_rec_pretrained", destination)
    (destination / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    output_volume.commit()
    return metrics


@app.function(
    image=image,
    gpu="L4",
    timeout=30 * 60,
    volumes={"/data": data_volume, "/outputs": output_volume},
)
def evaluate_saved_checkpoints(run_name: str) -> dict:
    extract_dataset()
    run_dir = Path("/outputs") / run_name
    all_metrics = {}
    for epoch in (5, 10, 15, 20, 25, 30):
        checkpoint = run_dir / f"iter_epoch_{epoch}"
        destination = run_dir / "checkpoint_evaluation" / f"epoch_{epoch}"
        all_metrics[str(epoch)] = eval_checkpoint(str(checkpoint), destination)
    result_path = run_dir / "checkpoint_evaluation" / "metrics.json"
    result_path.write_text(
        json.dumps(all_metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    output_volume.commit()
    return all_metrics


@app.function(
    image=image,
    gpu="L4",
    timeout=15 * 60,
    volumes={"/outputs": output_volume},
)
def export_saved_checkpoint(run_name: str, epoch: int) -> dict:
    run_dir = Path("/outputs") / run_name
    checkpoint = run_dir / f"iter_epoch_{epoch}"
    destination = run_dir / f"inference_epoch_{epoch}"
    run([
        "python", "tools/export_model.py", "-c",
        "configs/rec/PP-OCRv6/PP-OCRv6_spine_rec.yml", "-o",
        f"Global.pretrained_model={checkpoint}",
        f"Global.save_inference_dir={destination}",
    ])
    output_volume.commit()
    return {"checkpoint": str(checkpoint), "inference_dir": str(destination)}


@app.local_entrypoint()
def main(
    epochs: int = 30,
    learning_rate: float = 0.0001,
    evaluate_only: bool = False,
    evaluate_run: str = "",
    export_epoch: int = 0,
) -> None:
    if evaluate_run and export_epoch:
        result = export_saved_checkpoint.remote(evaluate_run, export_epoch)
    elif evaluate_run:
        result = evaluate_saved_checkpoints.remote(evaluate_run)
    elif evaluate_only:
        result = evaluate_pretrained.remote()
    else:
        result = train.remote(epochs, learning_rate)
    print(json.dumps(result, ensure_ascii=False, indent=2))
