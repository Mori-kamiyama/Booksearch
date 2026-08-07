"""Assign YOLO-detected book boxes to physical shelf IDs using AprilTags.

The mapping table describes which shelf sits in each quadrant around a tag.
For example, if tag 12 is placed at an intersection of four shelf openings,
``quadrants.top_left`` is the shelf ID of the opening above-left of that tag.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np


REPO_ROOT = Path(__file__).resolve().parent.parent

CORNER_NAMES = ("top_left", "top_right", "bottom_right", "bottom_left")
QUADRANT_NAMES = ("top_left", "top_right", "bottom_right", "bottom_left")

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


@dataclass(frozen=True)
class ShelfVote:
    tag_id: int
    shelf_id: str
    quadrant: str
    distance_px: float
    angle_deg: float


@dataclass(frozen=True)
class ShelfAssignment:
    box_id: str
    shelf_id: str | None
    status: str
    reason: str
    votes: list[ShelfVote]

    def to_json(self) -> dict[str, Any]:
        return {
            "box_id": self.box_id,
            "shelf_id": self.shelf_id,
            "status": self.status,
            "reason": self.reason,
            "votes": [
                {
                    "tag_id": vote.tag_id,
                    "shelf_id": vote.shelf_id,
                    "quadrant": vote.quadrant,
                    "distance_px": round(vote.distance_px, 2),
                    "angle_deg": round(vote.angle_deg, 2),
                }
                for vote in self.votes
            ],
        }


@dataclass(frozen=True)
class DetectedTag:
    tag_id: int
    corners: np.ndarray
    center: np.ndarray
    x_axis: np.ndarray
    y_axis: np.ndarray
    angle_deg: float
    orientation_status: str


def load_mapping(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _unit_vector(vec: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vec))
    if norm == 0:
        return vec
    return vec / norm


def _angle_delta(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def _orientation_status(tag_id: int, angle_deg: float, mapping: dict[str, Any]) -> str:
    cfg = mapping.get("tags", {}).get(str(tag_id))
    if not cfg:
        return "unmapped"
    expected = cfg.get("expected_angle_deg")
    tolerance = cfg.get("angle_tolerance_deg", 35)
    if expected is None:
        return "unknown"
    return "ok" if _angle_delta(angle_deg, float(expected)) <= float(tolerance) else "mismatch"


def detect_tags(image: np.ndarray, mapping: dict[str, Any]) -> list[DetectedTag]:
    dictionary_name = mapping.get("dictionary", "DICT_APRILTAG_36h11")
    dictionary_id = ARUCO_DICTIONARIES.get(dictionary_name)
    if dictionary_id is None:
        raise ValueError(f"unsupported aruco dictionary: {dictionary_name}")

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    dictionary = cv2.aruco.getPredefinedDictionary(dictionary_id)
    detector = cv2.aruco.ArucoDetector(dictionary, cv2.aruco.DetectorParameters())
    corners_list, ids, _ = detector.detectMarkers(gray)
    if ids is None:
        return []

    tags: list[DetectedTag] = []
    for corners, tag_id_arr in zip(corners_list, ids):
        tag_id = int(tag_id_arr[0])
        pts = corners.reshape(4, 2).astype(float)
        center = pts.mean(axis=0)
        x_axis = _unit_vector(pts[1] - pts[0])
        y_axis = _unit_vector(pts[3] - pts[0])
        angle_deg = math.degrees(math.atan2(float(x_axis[1]), float(x_axis[0])))
        tags.append(
            DetectedTag(
                tag_id=tag_id,
                corners=pts,
                center=center,
                x_axis=x_axis,
                y_axis=y_axis,
                angle_deg=angle_deg,
                orientation_status=_orientation_status(tag_id, angle_deg, mapping),
            )
        )
    return tags


def _entry_box(entry: dict[str, Any]) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = entry["bbox_xyxy"]
    return int(x1), int(y1), int(x2), int(y2)


def _box_entry(box: Any) -> dict[str, Any]:
    return {
        "box_id": box.box_id,
        "bbox_xyxy": list(box.xyxy),
    }


def _quadrant(tag: DetectedTag, point: np.ndarray) -> str:
    rel = point - tag.center
    local_x = float(np.dot(rel, tag.x_axis))
    local_y = float(np.dot(rel, tag.y_axis))
    if local_x < 0 and local_y < 0:
        return "top_left"
    if local_x >= 0 and local_y < 0:
        return "top_right"
    if local_x >= 0 and local_y >= 0:
        return "bottom_right"
    return "bottom_left"


def _auto_max_distance(entry: dict[str, Any], mapping: dict[str, Any]) -> float:
    x1, y1, x2, y2 = _entry_box(entry)
    scale = float(mapping.get("auto_distance_scale", 1.25))
    return max(x2 - x1, y2 - y1) * scale


def assign_boxes_to_shelves(
    entries: list[dict[str, Any]],
    tags: list[DetectedTag],
    mapping: dict[str, Any],
    max_tag_distance: float | None = None,
) -> dict[str, ShelfAssignment]:
    assignments: dict[str, ShelfAssignment] = {}
    tag_configs = mapping.get("tags", {})

    for entry in entries:
        box_id = str(entry.get("box_id"))
        x1, y1, x2, y2 = _entry_box(entry)
        center = np.array([(x1 + x2) / 2.0, (y1 + y2) / 2.0])
        distance_limit = (
            float("inf")
            if max_tag_distance == 0
            else float(max_tag_distance)
            if max_tag_distance is not None
            else _auto_max_distance(entry, mapping)
        )

        votes: list[ShelfVote] = []
        skipped_mismatch = 0
        for tag in tags:
            cfg = tag_configs.get(str(tag.tag_id))
            if not cfg:
                continue
            if tag.orientation_status == "mismatch":
                skipped_mismatch += 1
                continue
            distance = float(np.linalg.norm(center - tag.center))
            if distance > distance_limit:
                continue
            quadrant = _quadrant(tag, center)
            shelf_id = (cfg.get("quadrants") or {}).get(quadrant)
            if not shelf_id:
                continue
            votes.append(
                ShelfVote(
                    tag_id=tag.tag_id,
                    shelf_id=shelf_id,
                    quadrant=quadrant,
                    distance_px=distance,
                    angle_deg=tag.angle_deg,
                )
            )

        votes.sort(key=lambda vote: vote.distance_px)
        if votes:
            assignments[box_id] = ShelfAssignment(
                box_id=box_id,
                shelf_id=votes[0].shelf_id,
                status="assigned",
                reason="nearest_tag_quadrant",
                votes=votes[:5],
            )
        else:
            reason = "orientation_mismatch" if skipped_mismatch and tags else "no_mapped_tag"
            assignments[box_id] = ShelfAssignment(
                box_id=box_id,
                shelf_id=None,
                status="skipped",
                reason=reason,
                votes=[],
            )
    return assignments


def locate_shelves_for_boxes(
    image_path: Path,
    boxes: list[Any],
    mapping_path: Path,
    max_tag_distance: float | None = None,
) -> tuple[dict[str, ShelfAssignment], list[DetectedTag]]:
    image = cv2.imread(str(image_path))
    if image is None:
        return {}, []
    mapping = load_mapping(mapping_path)
    tags = detect_tags(image, mapping)
    entries = [_box_entry(box) for box in boxes]
    return assign_boxes_to_shelves(entries, tags, mapping, max_tag_distance), tags


def annotate_catalog(
    catalog: dict[str, Any],
    mapping_path: Path,
    max_tag_distance: float | None = None,
) -> dict[str, Any]:
    mapping = load_mapping(mapping_path)
    entries_by_image: dict[str, list[dict[str, Any]]] = {}
    for entry in catalog.get("entries", []):
        source_image = entry.get("source_image")
        if source_image and entry.get("bbox_xyxy"):
            entries_by_image.setdefault(source_image, []).append(entry)

    tag_summaries: dict[str, list[dict[str, Any]]] = {}
    for source_image, entries in entries_by_image.items():
        image = cv2.imread(source_image)
        if image is None:
            continue
        tags = detect_tags(image, mapping)
        assignments = assign_boxes_to_shelves(entries, tags, mapping, max_tag_distance)
        tag_summaries[source_image] = [
            {
                "tag_id": tag.tag_id,
                "center": [round(float(tag.center[0]), 2), round(float(tag.center[1]), 2)],
                "angle_deg": round(tag.angle_deg, 2),
                "orientation_status": tag.orientation_status,
            }
            for tag in tags
        ]
        for entry in entries:
            assignment = assignments.get(str(entry.get("box_id")))
            if assignment is None:
                continue
            entry["shelf_id"] = assignment.shelf_id
            entry["shelf_assignment"] = assignment.to_json()

    catalog["shelf_mapping"] = {
        "mapping_file": str(mapping_path),
        "map_id": mapping.get("map_id"),
        "coordinate_schema_version": mapping.get("coordinate_schema_version"),
        "tag_detections": tag_summaries,
    }
    return catalog


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AprilTag mappingでcatalogのYOLO boxへ棚IDを付与します。")
    parser.add_argument("--catalog", required=True, help="build_book_catalog.py の catalog.json")
    parser.add_argument("--mapping", required=True, help="AprilTagと棚IDのマッピングJSON")
    parser.add_argument("--output", default=None, help="出力JSON。省略時は入力catalogを上書き")
    parser.add_argument(
        "--max-tag-distance",
        type=float,
        default=None,
        help="box重心からtag中心までの最大距離px。0なら距離制限なし。省略時はboxサイズから自動",
    )
    return parser.parse_args()


def resolve_path(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else REPO_ROOT / p


def main() -> int:
    args = parse_args()
    catalog_path = resolve_path(args.catalog)
    mapping_path = resolve_path(args.mapping)
    output_path = resolve_path(args.output) if args.output else catalog_path

    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    annotated = annotate_catalog(catalog, mapping_path, args.max_tag_distance)
    output_path.write_text(json.dumps(annotated, ensure_ascii=False, indent=2), encoding="utf-8")

    assigned = sum(1 for entry in annotated.get("entries", []) if entry.get("shelf_id"))
    skipped = sum(
        1
        for entry in annotated.get("entries", [])
        if (entry.get("shelf_assignment") or {}).get("status") == "skipped"
    )
    print(f"catalog : {output_path}")
    print(f"assigned: {assigned}")
    print(f"skipped : {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
