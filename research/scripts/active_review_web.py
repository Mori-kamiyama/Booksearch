#!/usr/bin/env python3
"""Local active-learning review UI for human-SFT spine OCR predictions."""

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
WEB_ROOT = REPO_ROOT / "active_review_app"
DEFAULT_BATCH = REPO_ROOT / "outputs/ocr_active_review/v1/input.jsonl"
DEFAULT_PREDICTIONS = REPO_ROOT / "outputs/ocr_active_review/v1/predictions.jsonl"
DEFAULT_ANNOTATIONS = REPO_ROOT / "outputs/ocr_active_review/v1/annotations.json"


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_predictions(path: Path) -> dict[str, str]:
    predictions = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        predictions[Path(row["images"][0]["path"]).name] = str(row.get("response") or "")
    return predictions


class ActiveReviewStore:
    def __init__(self, args: argparse.Namespace) -> None:
        self.annotation_path = args.annotations.resolve()
        self.lock = threading.Lock()
        self.annotations = read_json(self.annotation_path)
        predictions = load_predictions(args.predictions)
        self.items: list[dict[str, Any]] = []
        for line in args.batch.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            metadata = row.get("metadata") or {}
            image_path = Path(row["images"][0]).resolve()
            name = image_path.name
            self.items.append(
                {
                    "item_id": str(metadata.get("item_id") or image_path),
                    "image_path": str(image_path),
                    "image_name": name,
                    "model_prediction": predictions.get(name, ""),
                    "pseudo_transcription": metadata.get("pseudo_transcription") or "",
                    "catalog_title": metadata.get("catalog_title") or "",
                    "catalog_authors": metadata.get("catalog_authors") or [],
                    "library_db_id": metadata.get("library_db_id"),
                    "match_score": metadata.get("match_score"),
                    "detector_confidence": metadata.get("detector_confidence"),
                    "random_rank": metadata.get("random_rank"),
                }
            )
        missing = [item["image_name"] for item in self.items if not item["model_prediction"]]
        if missing:
            raise ValueError(f"missing predictions for {len(missing)} items; first={missing[0]}")

    def summary(self, index: int, item: dict[str, Any]) -> dict[str, Any]:
        annotation = self.annotations.get(item["item_id"]) or {}
        return {
            "index": index,
            "item_id": item["item_id"],
            "image_name": item["image_name"],
            "catalog_title": item["catalog_title"],
            "status": annotation.get("status", "unreviewed"),
            "corrected": bool(
                annotation.get("status") == "accepted"
                and str(annotation.get("transcription") or "").strip()
                != item["model_prediction"].strip()
            ),
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
        result = {"total": len(self.items), "accepted": 0, "corrected": 0, "skipped": 0, "unreviewed": 0}
        for item in self.items:
            annotation = self.annotations.get(item["item_id"]) or {}
            status = annotation.get("status", "unreviewed")
            result[status if status in {"accepted", "skipped"} else "unreviewed"] += 1
            if status == "accepted" and str(annotation.get("transcription") or "").strip() != item["model_prediction"].strip():
                result["corrected"] += 1
        return result

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
            "model_prediction": item["model_prediction"],
            "corrected": status == "accepted" and transcription != item["model_prediction"].strip(),
            "flags": sorted(set(str(flag) for flag in payload.get("flags") or [])),
            "catalog_title": item["catalog_title"],
            "library_db_id": item["library_db_id"],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        with self.lock:
            self.annotations[item["item_id"]] = annotation
            self.annotation_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.annotation_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.annotations, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(self.annotation_path)
        return annotation

    def export_jsonl(self, preference: bool) -> bytes:
        lines = []
        for item in self.items:
            annotation = self.annotations.get(item["item_id"]) or {}
            if annotation.get("status") != "accepted" or annotation.get("flags"):
                continue
            chosen = str(annotation["transcription"])
            rejected = item["model_prediction"]
            if preference and chosen.strip() == rejected.strip():
                continue
            row = {
                "messages": [
                    {"role": "user", "content": "<image>OCR:"},
                    {"role": "assistant", "content": chosen},
                ],
                "images": [item["image_path"]],
                "metadata": {
                    "item_id": item["item_id"],
                    "library_db_id": item["library_db_id"],
                    "catalog_title": item["catalog_title"],
                    "source_model": "paddleocr-vl-1.6-human-sft-146",
                    "flags": annotation["flags"],
                },
            }
            if preference:
                row["rejected_response"] = rejected
            lines.append(json.dumps(row, ensure_ascii=False))
        return (("\n".join(lines) + "\n") if lines else "").encode("utf-8")


def make_handler(store: ActiveReviewStore) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def send_json(self, payload: Any, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            path = urllib.parse.urlparse(self.path).path
            try:
                if path == "/api/items":
                    self.send_json([store.summary(index, item) for index, item in enumerate(store.items)])
                    return
                if path == "/api/stats":
                    self.send_json(store.stats())
                    return
                if path in {"/api/export/sft", "/api/export/preferences"}:
                    preference = path.endswith("preferences")
                    body = store.export_jsonl(preference)
                    filename = "ocr_preferences.jsonl" if preference else "ocr_sft_annotations.jsonl"
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                    self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if path.startswith("/api/items/"):
                    self.send_json(store.detail(int(path.rsplit("/", 1)[-1])))
                    return
                if path.startswith("/api/images/"):
                    image_path = Path(store.items[int(path.rsplit("/", 1)[-1])]["image_path"])
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
            except (IndexError, ValueError) as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
            except Exception as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

        def do_POST(self) -> None:
            path = urllib.parse.urlparse(self.path).path
            if not path.startswith("/api/items/") or not path.endswith("/annotation"):
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                index = int(path.split("/")[3])
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
    parser = argparse.ArgumentParser(description="OCR active-review web app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4181)
    parser.add_argument("--batch", type=Path, default=DEFAULT_BATCH)
    parser.add_argument("--predictions", type=Path, default=DEFAULT_PREDICTIONS)
    parser.add_argument("--annotations", type=Path, default=DEFAULT_ANNOTATIONS)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    store = ActiveReviewStore(args)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(store))
    print(f"OCR active review: http://{args.host}:{args.port}/")
    print(f"items={len(store.items)} annotations={args.annotations.resolve()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
