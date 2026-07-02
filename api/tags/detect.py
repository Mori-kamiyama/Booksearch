from __future__ import annotations

import cgi
import json
import math
import mimetypes
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

import cv2
import numpy as np


MAP_PATH = Path(__file__).with_name("apriltag_library_map.json")
DIST_DIR = Path(__file__).resolve().parents[2] / "frontend" / "tag-placement" / "dist"

ARUCO_DICTIONARIES = {
    "DICT_4X4_50": cv2.aruco.DICT_4X4_50,
    "DICT_4X4_100": cv2.aruco.DICT_4X4_100,
    "DICT_5X5_100": cv2.aruco.DICT_5X5_100,
    "DICT_6X6_250": cv2.aruco.DICT_6X6_250,
    "DICT_APRILTAG_16h5": cv2.aruco.DICT_APRILTAG_16h5,
    "DICT_APRILTAG_25h9": cv2.aruco.DICT_APRILTAG_25h9,
    "DICT_APRILTAG_36h10": cv2.aruco.DICT_APRILTAG_36h10,
    "DICT_APRILTAG_36h11": cv2.aruco.DICT_APRILTAG_36h11,
}


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in ("", "/"):
            self._file(DIST_DIR / "index.html")
            return

        requested = (DIST_DIR / path.lstrip("/")).resolve()
        if not str(requested).startswith(str(DIST_DIR.resolve())):
            self._json(403, {"error": "forbidden"})
            return
        if requested.is_file():
            self._file(requested)
            return
        self._file(DIST_DIR / "index.html")

    def do_POST(self) -> None:
        try:
            form = cgi.FieldStorage(
                fp=self.rfile,
                headers=self.headers,
                environ={
                    "REQUEST_METHOD": "POST",
                    "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                },
            )
            field = form["image"] if "image" in form else None
            if field is None or not getattr(field, "file", None):
                self._json(400, {"error": "image field required"})
                return

            image_bytes = field.file.read()
            image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                self._json(400, {"error": "image could not be read"})
                return

            mapping = json.loads(MAP_PATH.read_text(encoding="utf-8"))
            self._json(200, detect_tags(image, mapping))
        except Exception as exc:
            self._json(500, {"error": str(exc)})

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path) -> None:
        if not path.is_file():
            self._json(404, {"error": "not found"})
            return
        body = path.read_bytes()
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        if path.name == "index.html":
            content_type = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "public, max-age=300" if path.name != "index.html" else "public, max-age=0, must-revalidate")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def detect_tags(image: np.ndarray, mapping: dict[str, Any]) -> dict[str, Any]:
    primary = mapping.get("dictionary", "DICT_APRILTAG_36h11")
    candidates = [primary] + [name for name in ARUCO_DICTIONARIES if name != primary]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    best_corners: list[Any] = []
    best_ids = None
    best_dict = primary
    diagnostics: dict[str, Any] = {"tried": [], "selected": None, "raw_ids": []}

    for name in candidates:
        if name not in ARUCO_DICTIONARIES:
            continue
        aruco_dict = cv2.aruco.getPredefinedDictionary(ARUCO_DICTIONARIES[name])
        detector = cv2.aruco.ArucoDetector(aruco_dict)
        corners, ids, _ = detector.detectMarkers(gray)
        count = 0 if ids is None else len(ids)
        diagnostics["tried"].append({"dict": name, "count": count})
        if ids is not None and count > len(best_corners):
            best_corners = list(corners)
            best_ids = ids
            best_dict = name
        if ids is not None and count >= 2:
            break

    diagnostics["selected"] = best_dict
    height, width = image.shape[:2]
    if best_ids is None or len(best_ids) == 0:
        return {"tags": [], "diagnostics": diagnostics, "image_width": width, "image_height": height}

    raw_ids = [int(tag_id) for tag_id in best_ids.flatten()]
    diagnostics["raw_ids"] = raw_ids
    tag_cfg = mapping.get("tags", {})

    tags = []
    for marker, raw_id in zip(best_corners, raw_ids):
        pts = marker.reshape(4, 2).astype(np.float32)
        tl, tr, _, _ = pts
        x_axis = tr - tl
        norm = float(np.linalg.norm(x_axis))
        if norm == 0:
            continue
        angle = math.degrees(math.atan2(float(x_axis[1]), float(x_axis[0])))
        cfg = tag_cfg.get(str(raw_id), {})
        expected = cfg.get("expected_angle_deg")
        tolerance = float(cfg.get("angle_tolerance_deg", 35.0))
        if expected is None:
            orientation_status = "unchecked"
            angle_delta = None
        else:
            angle_delta = abs((angle - float(expected) + 180.0) % 360.0 - 180.0)
            orientation_status = "ok" if angle_delta <= tolerance else "mismatch"

        quadrants = cfg.get("quadrants") or {}
        tags.append(
            {
                "tag_id": raw_id,
                "mapped": bool(cfg),
                "center": [round(float(v), 2) for v in pts.mean(axis=0)],
                "corners": [[round(float(x), 2), round(float(y), 2)] for x, y in pts],
                "angle_deg": round(float(angle), 2),
                "angle_delta_deg": None if angle_delta is None else round(float(angle_delta), 2),
                "orientation_status": orientation_status,
                "quadrants": quadrants,
                "placement": describe_placement(quadrants),
            }
        )

    return {"tags": tags, "diagnostics": diagnostics, "image_width": width, "image_height": height}


def describe_placement(quadrants: dict[str, str]) -> str:
    ordered = ["top_left", "top_right", "bottom_left", "bottom_right"]
    shelves = [quadrants[name] for name in ordered if quadrants.get(name)]
    unique_shelves = list(dict.fromkeys(shelves))
    if not unique_shelves:
        return "このタグに対応する棚は未登録です。"
    if len(unique_shelves) == 1:
        return f"{unique_shelves[0]} の近くに貼る"
    return f"{' / '.join(unique_shelves)} の交点に貼る"
