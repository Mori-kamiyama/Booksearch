"""Benchmark sub-1B OCR VLMs on the held-out book-spine split."""

import base64
import concurrent.futures
import json
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any

import modal


MODELS = {
    "ovisocr2": {
        "id": "ATH-MaaS/OvisOCR2",
        "revision": "1fc9221b7823a371d6e97f92d527cc847e24e107",
        "prompt": (
            "Extract all readable content from this Japanese book-spine image in natural "
            "human reading order. Preserve the original text without translation, correction, "
            "or paraphrasing. Output only the transcription."
        ),
    },
    "paddleocr-vl-1.6": {
        "id": "PaddlePaddle/PaddleOCR-VL-1.6",
        "revision": "c5630abae1d940eafe0697512a0325494b02ab42",
        "prompt": "OCR:",
    },
}

app = modal.App("booksearch-small-ocr-benchmark")
cache_volume = modal.Volume.from_name("booksearch-hf-cache", create_if_missing=True)
compile_volume = modal.Volume.from_name("booksearch-vllm-cache", create_if_missing=True)
image = (
    modal.Image.from_registry(
        "vllm/vllm-openai:v0.30.0",
        add_python="3.12",
        setup_dockerfile_commands=["ENTRYPOINT []"],
    )
    .env({"HF_HOME": "/root/.cache/huggingface", "HF_XET_HIGH_PERFORMANCE": "1"})
)


@app.cls(
    image=image,
    gpu="L4",
    timeout=60 * 60,
    scaledown_window=5 * 60,
    volumes={
        "/root/.cache/huggingface": cache_volume,
        "/root/.cache/vllm": compile_volume,
    },
)
class OCRServer:
    model_key: str = modal.parameter()

    @modal.enter()
    def start(self) -> None:
        config = MODELS[self.model_key]
        self.model_id = config["id"]
        self.prompt = config["prompt"]
        command = [
            "vllm",
            "serve",
            self.model_id,
            "--revision",
            config["revision"],
            "--served-model-name",
            self.model_id,
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
            "--dtype",
            "bfloat16",
            "--max-model-len",
            "4096",
            "--gpu-memory-utilization",
            "0.90",
            "--generation-config",
            "vllm",
            "--limit-mm-per-prompt",
            '{"image":1}',
            "--trust-remote-code",
            "--disable-uvicorn-access-log",
        ]
        if self.model_key == "ovisocr2":
            command.extend(["--gdn-prefill-backend", "triton"])
        self.server = subprocess.Popen(command)
        deadline = time.monotonic() + 15 * 60
        while time.monotonic() < deadline:
            if self.server.poll() is not None:
                raise RuntimeError(f"vLLM exited with {self.server.returncode}")
            try:
                with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=2) as response:
                    if response.status == 200:
                        return
            except Exception:
                pass
            time.sleep(2)
        raise TimeoutError("vLLM startup timed out")

    @modal.exit()
    def stop(self) -> None:
        if getattr(self, "server", None):
            self.server.terminate()

    def _infer(self, sample: dict[str, Any]) -> dict[str, Any]:
        image_bytes = sample["image_bytes"]
        encoded = base64.b64encode(image_bytes).decode("ascii")
        body = {
            "model": self.model_id,
            "temperature": 0,
            "max_tokens": 160,
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}},
                        {"type": "text", "text": self.prompt},
                    ],
                }
            ],
        }
        request = urllib.request.Request(
            "http://127.0.0.1:8000/v1/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                payload = json.loads(response.read())
            prediction = payload["choices"][0]["message"]["content"].strip()
            result = {key: value for key, value in sample.items() if key != "image_bytes"}
            return {**result, "ok": True, "prediction": prediction, "elapsed_sec": time.perf_counter() - started}
        except Exception as exc:
            result = {key: value for key, value in sample.items() if key != "image_bytes"}
            return {**result, "ok": False, "error": f"{type(exc).__name__}: {exc}"}

    @modal.method()
    def infer(self, samples: list[dict[str, Any]], concurrency: int = 32) -> list[dict[str, Any]]:
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            return list(pool.map(self._infer, samples))


def load_samples(dataset: Path, max_items: int) -> list[dict[str, Any]]:
    samples = []
    for line in dataset.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        answer = json.loads(row["messages"][1]["content"][0]["text"])
        samples.append(
            {
                "item_id": row["metadata"]["item_id"],
                "library_db_id": row["metadata"]["library_db_id"],
                "image": row["messages"][0]["content"][0]["image"],
                "expected": answer["transcription"],
                "title": answer["matched_book"]["title"],
            }
        )
        if len(samples) >= max_items:
            break
    return samples


@app.local_entrypoint()
def main(dataset: str, output: str, model_key: str, max_items: int = 100, concurrency: int = 32) -> None:
    if model_key not in MODELS:
        raise ValueError(f"model_key must be one of {sorted(MODELS)}")
    samples = load_samples(Path(dataset), max_items)
    for sample in samples:
        sample["image_bytes"] = Path(sample["image"]).read_bytes()
    started = time.perf_counter()
    results = OCRServer(model_key=model_key).infer.remote(samples, concurrency=concurrency)
    payload = {
        "model_key": model_key,
        "model": MODELS[model_key]["id"],
        "revision": MODELS[model_key]["revision"],
        "wall_sec": time.perf_counter() - started,
        "results": results,
    }
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"model": payload["model"], "items": len(results), "successful": sum(r["ok"] for r in results), "wall_sec": payload["wall_sec"]}, ensure_ascii=False))
