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