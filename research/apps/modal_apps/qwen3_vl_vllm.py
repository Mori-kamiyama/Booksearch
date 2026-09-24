"""High-throughput Qwen3-VL OCR on Modal using vLLM continuous batching.

Example benchmark:
  modal run modal_apps/qwen3_vl_vllm.py \
    --catalog outputs/qwen3_vl_modal_spines_smoke/catalog.json \
    --output outputs/qwen3_vl_modal_spines_smoke/qwen4b_vllm_results.json \
    --max-items 32 --batch-size 32
"""

import base64
import concurrent.futures
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import modal


APP_NAME = "booksearch-qwen3-vl-vllm"
MODELS = {
    "4b": (
        "Qwen/Qwen3-VL-4B-Instruct",
        "ebb281ec70b05090aa6165b016eac8ec08e71b17",
    ),
    "8b": (
        "Qwen/Qwen3-VL-8B-Instruct",
        "e0a319f4d147b3916275a053b0583ca82f351e90",
    ),
}
MODEL_CACHE = "/root/.cache/huggingface"
PORT = 8000
PROMPT = """これは1冊の本の背表紙を切り出して傾き補正した画像です。
画像に実際に見える文字だけを、縦書きなら自然な読順に直して正確に転記してください。
知識から推測して補完しないでください。判読できない部分は勝手に埋めず、読み取れた範囲だけを書いてください。
JSONだけを返してください。形式:
{"books":[{"position":1,"transcription":"見える全文","title":"書名またはnull","author":"著者またはnull","call_number":"請求記号またはnull","confidence":0.0}]}"""

app = modal.App(APP_NAME)
cache_volume = modal.Volume.from_name("booksearch-hf-cache", create_if_missing=True)
vllm_cache_volume = modal.Volume.from_name("booksearch-vllm-cache", create_if_missing=True)
vllm_image = (
    modal.Image.from_registry(
        "vllm/vllm-openai:v0.30.0",
        add_python="3.12",
        setup_dockerfile_commands=["ENTRYPOINT []"],
    )
    .env({"HF_HOME": MODEL_CACHE, "HF_XET_HIGH_PERFORMANCE": "1"})
)


def parse_json_response(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


@app.cls(
    image=vllm_image,
    gpu="L40S",
    timeout=60 * 60,
    scaledown_window=5 * 60,
    volumes={
        MODEL_CACHE: cache_volume,
        "/root/.cache/vllm": vllm_cache_volume,
    },
)
class QwenVLLM:
    model_size: str = modal.parameter(default="4b")

    @modal.enter()
    def start_server(self) -> None:
        if self.model_size not in MODELS:
            raise ValueError(f"Unsupported model_size={self.model_size!r}")
        self.model_id, self.model_revision = MODELS[self.model_size]
        command = [
            "vllm",
            "serve",
            "--model",
            self.model_id,
            "--revision",
            self.model_revision,
            "--served-model-name",
            self.model_id,
            "--host",
            "127.0.0.1",
            "--port",
            str(PORT),
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
        ]
        self.server = subprocess.Popen(command)
        health_url = f"http://127.0.0.1:{PORT}/health"
        deadline = time.monotonic() + 15 * 60
        last_error = ""
        while time.monotonic() < deadline:
            if self.server.poll() is not None:
                raise RuntimeError(f"vLLM server exited with code {self.server.returncode}")
            try:
                with urllib.request.urlopen(health_url, timeout=2) as response:
                    if response.status == 200:
                        return
            except Exception as exc:  # server is still loading
                last_error = str(exc)
            time.sleep(2)
        raise TimeoutError(f"vLLM health check timed out: {last_error}")

    @modal.exit()
    def stop_server(self) -> None:
        if getattr(self, "server", None) is not None:
            self.server.terminate()

    def _request_one(self, item: tuple[str, bytes]) -> dict[str, Any]:
        item_id, image_bytes = item
        encoded = base64.b64encode(image_bytes).decode("ascii")
        body = {
            "model": self.model_id,
            "temperature": 0,
            "max_tokens": 320,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
                        },
                    ],
                }
            ],
        }
        request = urllib.request.Request(
            f"http://127.0.0.1:{PORT}/v1/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                payload = json.loads(response.read().decode("utf-8"))
            content = payload["choices"][0]["message"]["content"]
            return {
                "item_id": item_id,
                "ok": True,
                "raw": content,
                "parsed": parse_json_response(content),
                "elapsed_sec": round(time.perf_counter() - started, 3),
                "usage": payload.get("usage") or {},
            }
        except Exception as exc:
            return {
                "item_id": item_id,
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "elapsed_sec": round(time.perf_counter() - started, 3),
            }

    @modal.method()
    def infer_batch(
        self,
        items: list[tuple[str, bytes]],
        concurrency: int = 32,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            results = list(pool.map(self._request_one, items))
        return {
            "wall_sec": round(time.perf_counter() - started, 3),
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "results": results,
        }


def checkpoint_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return rows


def add_library_matches(rows: list[dict[str, Any]], db_path: Path) -> dict[str, int]:
    stats = {"auto": 0, "review": 0, "none": 0}
    if not db_path.exists():
        return stats
    repo_root = Path(__file__).resolve().parent.parent
    src_dir = repo_root / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
    from lookup import library_db_lookup

    match_cache: dict[str, dict[str, Any] | None] = {}
    for row in rows:
        books = ((row.get("parsed") or {}).get("books") or [])
        for book in books if isinstance(books, list) else []:
            if not isinstance(book, dict):
                continue
            queries = []
            for value in (book.get("title"), book.get("transcription")):
                if value and value not in queries:
                    queries.append(value)
            for query in queries:
                if query not in match_cache:
                    match_cache[query] = library_db_lookup(query, db_path=db_path)
            matches = [match_cache[query] for query in queries]
            matches = [match for match in matches if match]
            matches.sort(
                key=lambda match: float(
                    ((match.get("candidates") or [{}])[0]).get("score") or 0.0
                ),
                reverse=True,
            )
            book["library_match"] = matches[0] if matches else None
            candidates = ((book.get("library_match") or {}).get("candidates") or [])
            status = candidates[0].get("match_confidence") if candidates else "none"
            stats[status if status in stats else "none"] += 1
    return stats


def group_library_books(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        for book in ((row.get("parsed") or {}).get("books") or []):
            candidates = ((book.get("library_match") or {}).get("candidates") or [])
            if not candidates or candidates[0].get("library_db_id") is None:
                continue
            candidate = candidates[0]
            key = str(candidate["library_db_id"])
            item = grouped.setdefault(
                key,
                {
                    "library_db_id": candidate["library_db_id"],
                    "title": candidate.get("title"),
                    "authors": candidate.get("authors") or [],
                    "best_score": 0.0,
                    "crop_ids": [],
                    "source_images": [],
                },
            )
            item["best_score"] = max(item["best_score"], float(candidate.get("score") or 0))
            item["crop_ids"].append(row.get("box_id"))
            source_images = [row.get("source_image")]
            source_images.extend(
                appearance.get("source_image") for appearance in row.get("appearances") or []
            )
            for source_image in source_images:
                if source_image and source_image not in item["source_images"]:
                    item["source_images"].append(source_image)
    return sorted(grouped.values(), key=lambda item: (item["title"] or "", item["library_db_id"]))


@app.local_entrypoint()
def main(
    catalog: str,
    output: str,
    library_db: str = "outputs/library/library.db",
    batch_size: int = 64,
    concurrency: int = 32,
    max_items: int | None = None,
    model_size: str = "4b",
    skip_postprocess: bool = False,
) -> None:
    model_size = model_size.lower()
    if model_size not in MODELS:
        raise ValueError(f"model_size must be one of {sorted(MODELS)}, got {model_size!r}")
    model_id, model_revision = MODELS[model_size]
    catalog_path = Path(catalog).resolve()
    output_path = Path(output).resolve()
    db_path = Path(library_db).resolve()
    checkpoint_path = output_path.with_suffix(".jsonl")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    entries = payload.get("entries") or []
    entry_map = {str(entry["crop_image"]): entry for entry in entries}
    existing = checkpoint_rows(checkpoint_path)
    done = {str(row["item_id"]) for row in existing if row.get("ok")}
    pending = [entry for entry in entries if str(entry["crop_image"]) not in done]
    if max_items is not None:
        pending = pending[:max_items]
    print(f"entries={len(entries)} done={len(done)} pending={len(pending)}")

    engine = QwenVLLM(model_size=model_size)
    batch_stats = []
    for start in range(0, len(pending), batch_size):
        batch_entries = pending[start : start + batch_size]
        request_items = [
            (str(entry["crop_image"]), Path(entry["crop_image"]).read_bytes())
            for entry in batch_entries
        ]
        response = engine.infer_batch.remote(request_items, concurrency=concurrency)
        batch_stats.append(
            {
                "items": len(batch_entries),
                "wall_sec": response["wall_sec"],
                "items_per_sec": round(len(batch_entries) / response["wall_sec"], 3),
            }
        )
        with checkpoint_path.open("a", encoding="utf-8") as handle:
            for result in response["results"]:
                entry = entry_map[result["item_id"]]
                result.update(
                    {
                        "source_image": entry.get("source_image"),
                        "crop_image": entry.get("crop_image"),
                        "box_id": entry.get("box_id"),
                        "polygon_xy": entry.get("polygon_xy"),
                        "appearances": entry.get("appearances") or [],
                        "detector_confidence": entry.get("detector_confidence"),
                        "model": model_id,
                        "model_revision": model_revision,
                        "engine": "vllm-0.30.0",
                    }
                )
                handle.write(json.dumps(result, ensure_ascii=False) + "\n")
        print(json.dumps(batch_stats[-1], ensure_ascii=False), flush=True)

    if skip_postprocess:
        print(f"checkpoint={checkpoint_path}")
        return

    rows = checkpoint_rows(checkpoint_path)
    match_stats = add_library_matches(rows, db_path)
    unique_books = group_library_books(rows)
    summary = {
        "catalog": str(catalog_path),
        "model": model_id,
        "model_revision": model_revision,
        "engine": "vllm-0.30.0",
        "gpu": "L40S",
        "library_db": str(db_path) if db_path.exists() else None,
        "results": rows,
        "unique_library_books": unique_books,
        "batch_stats": batch_stats,
        "stats": {
            "crops": len(rows),
            "successful": sum(bool(row.get("ok")) for row in rows),
            "valid_json": sum(row.get("parsed") is not None for row in rows),
            "books": sum(
                len(((row.get("parsed") or {}).get("books") or [])) for row in rows
            ),
            "library_matches": match_stats,
            "unique_library_books": len(unique_books),
        },
    }
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary["stats"], ensure_ascii=False))
    print(f"output={output_path}")
