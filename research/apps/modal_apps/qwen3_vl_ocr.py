"""Run Qwen3-VL-8B-Instruct on Modal for Japanese book-spine OCR.

Smoke test:
  modal run modal_apps/qwen3_vl_ocr.py \
    --catalog outputs/qwen3_vl_modal/catalog.json \
    --output outputs/qwen3_vl_modal/qwen_results.json \
    --max-items 4

Resume/full run (already completed crops are skipped):
  modal run modal_apps/qwen3_vl_ocr.py \
    --catalog outputs/qwen3_vl_modal/catalog.json \
    --output outputs/qwen3_vl_modal/qwen_results.json
"""

from __future__ import annotations

import io
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import modal


APP_NAME = "booksearch-qwen3-vl-8b"
MODEL_ID = "Qwen/Qwen3-VL-8B-Instruct"
MODEL_REVISION = "e0a319f4d147b3916275a053b0583ca82f351e90"
MODEL_CACHE = "/root/.cache/huggingface"
DEFAULT_DB = "outputs/library/library.db"

SHELF_PROMPT = """これは本棚の一領域を切り出した画像です。
写っている本の背表紙を左から右へ順に読み取ってください。
画像に実際に見える文字だけを転記し、知識から書名や著者を補完しないでください。
一部しか読めない本も、読めた文字は残してください。箱、棚札、重複した同じ背表紙は本に数えません。
JSONだけを返してください。形式:
{"books":[{"position":1,"transcription":"背表紙に見える全文","title":"書名またはnull","author":"著者またはnull","call_number":"請求記号またはnull","confidence":0.0}]}
confidenceは文字の視認性だけを0〜1で表してください。"""

SPINE_PROMPT = """これは1冊の本の背表紙を切り出して傾き補正した画像です。
画像に実際に見える文字だけを、縦書きなら自然な読順に直して正確に転記してください。
知識から推測して補完しないでください。判読できない部分は勝手に埋めず、読み取れた範囲だけを書いてください。
JSONだけを返してください。形式:
{"books":[{"position":1,"transcription":"見える全文","title":"書名またはnull","author":"著者またはnull","call_number":"請求記号またはnull","confidence":0.0}]}"""

app = modal.App(APP_NAME)
cache_volume = modal.Volume.from_name("booksearch-hf-cache", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(
        "torch==2.8.0",
        "torchvision==0.23.0",
        "transformers==4.57.6",
        "accelerate==1.12.0",
        "safetensors>=0.6.2",
        "pillow>=11.0.0",
        "huggingface-hub[hf-xet]>=0.36.0",
    )
    .env(
        {
            "HF_HOME": MODEL_CACHE,
            "HF_XET_HIGH_PERFORMANCE": "1",
            "TOKENIZERS_PARALLELISM": "false",
        }
    )
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
    image=image,
    gpu="L4",
    timeout=60 * 60,
    scaledown_window=5 * 60,
    volumes={MODEL_CACHE: cache_volume},
)
class QwenBookOCR:
    @modal.enter()
    def load(self) -> None:
        import torch
        from transformers import AutoProcessor, Qwen3VLForConditionalGeneration

        self.processor = AutoProcessor.from_pretrained(
            MODEL_ID,
            revision=MODEL_REVISION,
            min_pixels=256 * 256,
            max_pixels=1536 * 1536,
        )
        self.model = Qwen3VLForConditionalGeneration.from_pretrained(
            MODEL_ID,
            revision=MODEL_REVISION,
            torch_dtype=torch.bfloat16,
            device_map="cuda",
            low_cpu_mem_usage=True,
        ).eval()

    def _infer_one(self, image_bytes: bytes, mode: str) -> dict[str, Any]:
        import torch
        from PIL import Image, ImageOps

        prompt = SPINE_PROMPT if mode == "spine" else SHELF_PROMPT
        with Image.open(io.BytesIO(image_bytes)) as source:
            pil_image = ImageOps.exif_transpose(source).convert("RGB")
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": pil_image},
                    {"type": "text", "text": prompt},
                ],
            }
        ]
        inputs = self.processor.apply_chat_template(
            messages,
            add_generation_prompt=True,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
        ).to(self.model.device)
        input_length = inputs["input_ids"].shape[-1]
        started = time.perf_counter()
        with torch.inference_mode():
            generated = self.model.generate(
                **inputs,
                do_sample=False,
                max_new_tokens=1200 if mode == "shelf" else 320,
            )
        raw = self.processor.decode(
            generated[0][input_length:],
            skip_special_tokens=True,
        ).strip()
        return {
            "raw": raw,
            "parsed": parse_json_response(raw),
            "elapsed_sec": round(time.perf_counter() - started, 3),
        }

    @modal.method()
    def infer_batch(
        self,
        items: list[tuple[str, bytes]],
        mode: str = "shelf",
    ) -> list[dict[str, Any]]:
        results = []
        for item_id, image_bytes in items:
            try:
                result = self._infer_one(image_bytes, mode)
                result.update({"item_id": item_id, "ok": True})
            except Exception as exc:  # Keep the rest of a long batch resumable.
                result = {
                    "item_id": item_id,
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            results.append(result)
        return results


def prepare_image(path: Path) -> bytes:
    """Read the YOLO-produced JPEG without adding local CLI dependencies."""
    return path.read_bytes()


def completed_ids(output: Path) -> set[str]:
    checkpoint = output.with_suffix(".jsonl")
    if not checkpoint.exists():
        return set()
    done = set()
    for line in checkpoint.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("ok") and row.get("item_id"):
            done.add(str(row["item_id"]))
    return done


def load_checkpoint(output: Path) -> list[dict[str, Any]]:
    checkpoint = output.with_suffix(".jsonl")
    rows = []
    if checkpoint.exists():
        for line in checkpoint.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def add_catalog_metadata(
    rows: list[dict[str, Any]],
    entries: list[dict[str, Any]],
) -> None:
    entry_map = {str(entry.get("crop_image")): entry for entry in entries}
    for row in rows:
        entry = entry_map.get(str(row.get("item_id")))
        if not entry:
            continue
        row["source_image"] = entry.get("source_image")
        row["crop_image"] = entry.get("crop_image")
        row["box_id"] = entry.get("box_id")
        row["bbox_xyxy"] = entry.get("bbox_xyxy")
        row["polygon_xy"] = entry.get("polygon_xy")
        row["detector_confidence"] = entry.get("detector_confidence")
        row["appearances"] = entry.get("appearances") or []


def add_library_matches(rows: list[dict[str, Any]], db_path: Path) -> None:
    if not db_path.exists():
        return
    repo_root = Path(__file__).resolve().parent.parent
    src_dir = repo_root / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
    from lookup import library_db_lookup

    for row in rows:
        books = ((row.get("parsed") or {}).get("books") or [])
        if not isinstance(books, list):
            continue
        for book in books:
            if not isinstance(book, dict):
                continue
            queries = []
            for value in (book.get("title"), book.get("transcription")):
                if value and value not in queries:
                    queries.append(value)
            matches = [library_db_lookup(query, db_path=db_path) for query in queries]
            matches = [match for match in matches if match]
            matches.sort(
                key=lambda match: float(
                    ((match.get("candidates") or [{}])[0]).get("score") or 0.0
                ),
                reverse=True,
            )
            book["library_match"] = matches[0] if matches else None


@app.local_entrypoint()
def main(
    catalog: str,
    output: str,
    library_db: str = DEFAULT_DB,
    mode: str = "shelf",
    batch_size: int = 2,
    max_items: int | None = None,
) -> None:
    catalog_path = Path(catalog).resolve()
    output_path = Path(output).resolve()
    db_path = Path(library_db).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path = output_path.with_suffix(".jsonl")

    payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    entries = payload.get("entries") or []
    done = completed_ids(output_path)
    pending = [entry for entry in entries if str(entry["crop_image"]) not in done]
    if max_items is not None:
        pending = pending[:max_items]

    print(f"catalog={catalog_path} entries={len(entries)} done={len(done)} pending={len(pending)}")
    model = QwenBookOCR()
    for start in range(0, len(pending), batch_size):
        batch_entries = pending[start : start + batch_size]
        request = []
        for entry in batch_entries:
            crop = Path(entry["crop_image"])
            if not crop.is_absolute():
                crop = (catalog_path.parent / crop).resolve()
            request.append((str(entry["crop_image"]), prepare_image(crop)))
        results = model.infer_batch.remote(request, mode=mode)
        entry_map = {str(entry["crop_image"]): entry for entry in batch_entries}
        with checkpoint_path.open("a", encoding="utf-8") as handle:
            for offset, result in enumerate(results):
                entry = entry_map[result["item_id"]]
                result["source_image"] = entry.get("source_image")
                result["crop_image"] = entry.get("crop_image")
                result["box_id"] = entry.get("box_id")
                result["bbox_xyxy"] = entry.get("bbox_xyxy")
                result["detector_confidence"] = entry.get("detector_confidence")
                result["polygon_xy"] = entry.get("polygon_xy")
                result["appearances"] = entry.get("appearances") or []
                result["model"] = MODEL_ID
                result["model_revision"] = MODEL_REVISION
                result["mode"] = mode
                handle.write(json.dumps(result, ensure_ascii=False) + "\n")
                print(
                    f"[{start + offset + 1}/{len(pending)}] {result['item_id']} "
                    f"ok={result['ok']} sec={result.get('elapsed_sec', '-')}"
                )

    rows = load_checkpoint(output_path)
    add_catalog_metadata(rows, entries)
    add_library_matches(rows, db_path)
    summary = {
        "catalog": str(catalog_path),
        "model": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "mode": mode,
        "library_db": str(db_path) if db_path.exists() else None,
        "crop_results": rows,
        "stats": {
            "crops": len(rows),
            "successful": sum(bool(row.get("ok")) for row in rows),
            "books": sum(
                len(((row.get("parsed") or {}).get("books") or []))
                for row in rows
            ),
        },
    }
    output_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary["stats"], ensure_ascii=False))
    print(f"output={output_path}")
