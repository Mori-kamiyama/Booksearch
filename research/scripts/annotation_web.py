#!/usr/bin/env python3
"""Local review UI for correcting spine OCR pseudo labels.

Usage:
  uv run python scripts/annotation_web.py
  open http://127.0.0.1:4180/
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import threading
import urllib.parse
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATASET = REPO_ROOT / "outputs/qwen3_vl_modal_spines/training_v1/all.jsonl"
DEFAULT_ANNOTATIONS = REPO_ROOT / "outputs/ocr_annotations/annotations.json"
WEB_ROOT = REPO_ROOT / "annotation_app"


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def prediction_map(path: Path) -> dict[str, dict[str, Any]]:
    payload = read_json(path)
    return {Path(row["image"]).name: row for row in payload.get("results", [])}


def search_map(path: Path, key: str) -> dict[str, dict[str, Any]]:
    payload = read_json(path)
    rows = (payload.get("details") or {}).get(key, [])
    return {Path(row["item_id"]).name: row for row in rows}


class AnnotationStore:
    def __init__(self, args: argparse.Namespace) -> None:
        self.annotation_path = args.annotations.resolve()
        self.lock = threading.Lock()
        self.annotations = read_json(self.annotation_path)
        self.items: list[dict[str, Any]] = []

        base = prediction_map(args.base)
        lora = prediction_map(args.lora)
        gemini = prediction_map(args.gemini)
        lora_search = search_map(args.search, "paddleocr_vl_1_6_lora_100:line_ensemble")
        gemini_search = search_map(args.gemini_search, "gemini_3_1_flash_lite_100:full")

        with args.dataset.open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                answer = json.loads(row["messages"][1]["content"][0]["text"])
                image_path = Path(row["messages"][0]["content"][0]["image"]).resolve()
                metadata = row.get("metadata") or {}
                name = image_path.name
                item_id = str(metadata.get("item_id") or image_path)
                self.items.append(
                    {
                        "item_id": item_id,
                        "image_path": str(image_path),
                        "image_name": name,
                        "pseudo_transcription": answer.get("transcription") or "",
                        "catalog_title": (answer.get("matched_book") or {}).get("title") or "",
                        "authors": (answer.get("matched_book") or {}).get("authors") or [],
                        "library_db_id": metadata.get("library_db_id"),
                        "match_score": metadata.get("match_score"),
                        "split": metadata.get("split"),
                        "pseudo_source": metadata.get("pseudo_label_source"),
                        "detector_confidence": metadata.get("detector_confidence"),
                        "base_prediction": (base.get(name) or {}).get("prediction") or "",
                        "lora_prediction": (lora.get(name) or {}).get("prediction") or "",
                        "gemini_prediction": (gemini.get(name) or {}).get("prediction") or "",
                        "lora_candidates": (lora_search.get(name) or {}).get("candidates") or [],
                        "gemini_candidates": (gemini_search.get(name) or {}).get("candidates") or [],
                    }
                )

    def summary(self, index: int, item: dict[str, Any]) -> dict[str, Any]:
        annotation = self.annotations.get(item["item_id"]) or {}
        return {
            "index": index,
            "item_id": item["item_id"],
            "image_name": item["image_name"],
            "catalog_title": item["catalog_title"],
            "split": item["split"],
            "status": annotation.get("status", "unreviewed"),
            "has_comparison": bool(item["lora_prediction"] or item["gemini_prediction"]),
        }

    def detail(self, index: int) -> dict[str, Any]:
        item = self.items[index]
        return {
            **{key: value for key, value in item.items() if key != "image_path"},
            "index": index,
            "image_url": f"/api/images/{index}",
            "annotation": self.annotations.get(item["item_id"]),
        }

    def stats(self) -> dict[str, int]:
        statuses = {"accepted": 0, "skipped": 0, "unreviewed": 0}
        for item in self.items:
            status = (self.annotations.get(item["item_id"]) or {}).get("status", "unreviewed")
            statuses[status if status in statuses else "unreviewed"] += 1
        return {"total": len(self.items), **statuses}

    def latest_accepted(self) -> dict[str, str]:
        accepted = [
            annotation
            for annotation in self.annotations.values()
            if annotation.get("status") == "accepted" and str(annotation.get("transcription") or "").strip()
        ]
        if not accepted:
            return {"transcription": ""}
        latest = max(accepted, key=lambda annotation: str(annotation.get("updated_at") or ""))
        return {"transcription": str(latest["transcription"])}

    def save(self, index: int, payload: dict[str, Any]) -> dict[str, Any]:
        item = self.items[index]
        status = str(payload.get("status") or "")
        if status not in {"accepted", "skipped"}:
            raise ValueError("status must be accepted or skipped")
        transcription = str(payload.get("transcription") or "").strip()
        if status == "accepted" and not transcription:
            raise ValueError("accepted annotation requires transcription")
        annotation = {
            "item_id": item["item_id"],
            "image_path": item["image_path"],
            "status": status,
            "transcription": transcription,
            "selected_source": str(payload.get("selected_source") or "edited"),
            "flags": sorted(set(str(flag) for flag in payload.get("flags") or [])),
            "catalog_title": item["catalog_title"],
            "library_db_id": item["library_db_id"],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        with self.lock:
            self.annotations[item["item_id"]] = annotation
            self.annotation_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.annotation_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(self.annotations, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.annotation_path)
        return annotation

    def export_jsonl(self) -> bytes:
        lines = []
        for item in self.items:
            annotation = self.annotations.get(item["item_id"]) or {}
            if annotation.get("status") != "accepted":
                continue
            row = {
                "messages": [
                    {"role": "user", "content": "<image>OCR:"},
                    {"role": "assistant", "content": annotation["transcription"]},
                ],
                "images": [item["image_path"]],
                "metadata": {
                    "item_id": item["item_id"],
                    "library_db_id": item["library_db_id"],
                    "catalog_title": item["catalog_title"],
                    "selected_source": annotation["selected_source"],
                    "flags": annotation["flags"],
                },
            }
            lines.append(json.dumps(row, ensure_ascii=False))
        return (("\n".join(lines) + "\n") if lines else "").encode("utf-8")


def make_handler(store: AnnotationStore) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def send_json(self, payload: Any, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            try:
                if path == "/api/items":
                    self.send_json([store.summary(index, item) for index, item in enumerate(store.items)])
                    return
                if path == "/api/stats":
                    self.send_json(store.stats())
                    return
                if path == "/api/latest-accepted":
                    self.send_json(store.latest_accepted())
                    return
                if path == "/api/export":
                    body = store.export_jsonl()
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                    self.send_header("Content-Disposition", 'attachment; filename="ocr_annotations.jsonl"')
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if path.startswith("/api/items/"):
                    index = int(path.rsplit("/", 1)[-1])
                    self.send_json(store.detail(index))
                    return
                if path.startswith("/api/images/"):
                    index = int(path.rsplit("/", 1)[-1])
                    image_path = Path(store.items[index]["image_path"])
                    body = image_path.read_bytes()
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", mimetypes.guess_type(image_path.name)[0] or "image/jpeg")
                    self.send_header("Cache-Control", "public, max-age=86400")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                static_path = WEB_ROOT / ("index.html" if path == "/" else path.lstrip("/"))
                if static_path.resolve().is_relative_to(WEB_ROOT.resolve()) and static_path.is_file():
                    body = static_path.read_bytes()
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", mimetypes.guess_type(static_path.name)[0] or "text/plain")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_error(HTTPStatus.NOT_FOUND)
            except (IndexError, ValueError):
                self.send_json({"error": "item not found"}, HTTPStatus.NOT_FOUND)
            except Exception as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

        def do_POST(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if not parsed.path.startswith("/api/items/") or not parsed.path.endswith("/annotation"):
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                index = int(parsed.path.split("/")[3])
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                annotation = store.save(index, payload)
                self.send_json({"annotation": annotation, "stats": store.stats()})
            except (IndexError, ValueError, json.JSONDecodeError) as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except Exception as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

        def log_message(self, format: str, *args: object) -> None:
            if args and str(args[1]).startswith("4"):
                super().log_message(format, *args)

    return Handler


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OCR annotation web app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4180)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--annotations", type=Path, default=DEFAULT_ANNOTATIONS)
    parser.add_argument("--base", type=Path, default=REPO_ROOT / "outputs/small_ocr_benchmark/paddleocr_vl_1_6_100.json")
    parser.add_argument("--lora", type=Path, default=REPO_ROOT / "outputs/small_ocr_benchmark/paddleocr_vl_1_6_lora_100.json")
    parser.add_argument("--gemini", type=Path, default=REPO_ROOT / "outputs/small_ocr_benchmark/gemini_3_1_flash_lite_100.json")
    parser.add_argument("--search", type=Path, default=REPO_ROOT / "outputs/small_ocr_benchmark/search_retrieval_at_k.json")
    parser.add_argument("--gemini-search", type=Path, default=REPO_ROOT / "outputs/small_ocr_benchmark/gemini_search_retrieval_at_k.json")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    store = AnnotationStore(args)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(store))
    print(f"OCR annotation app: http://{args.host}:{args.port}/")
    print(f"items={len(store.items)} annotations={args.annotations.resolve()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
