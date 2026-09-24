"""Detect AprilTag/ArUco markers in a still image and return JSON.

This is intentionally small and API-oriented: the frontend captures one camera
frame, the Go backend saves it to a temporary file, and this script performs the
same OpenCV dictionary detection used by the shelf pipeline.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import cv2
import numpy as np


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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--map", required=True, type=Path)
    args = parser.parse_args()

    mapping = json.loads(args.map.read_text(encoding="utf-8"))
    image = cv2.imread(str(args.image))
    if image is None:
        raise SystemExit(json.dumps({"error": "image could not be read"}))

    result = detect_tags(image, mapping)
    print(json.dumps(result, ensure_ascii=False))


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
    h, w = image.shape[:2]
    if best_ids is None or len(best_ids) == 0:
        return {"tags": [], "diagnostics": diagnostics, "image_width": w, "image_height": h}

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

    return {"tags": tags, "diagnostics": diagnostics, "image_width": w, "image_height": h}


def describe_placement(quadrants: dict[str, str]) -> str:
    ordered = ["top_left", "top_right", "bottom_left", "bottom_right"]
    shelves = [quadrants[name] for name in ordered if quadrants.get(name)]
    unique_shelves = list(dict.fromkeys(shelves))
    if not unique_shelves:
        return "このタグに対応する棚は未登録です。"
    if len(unique_shelves) == 1:
        return f"{unique_shelves[0]} の近くに貼る"
    return f"{' / '.join(unique_shelves)} の交点に貼る"


if __name__ == "__main__":
    main()
