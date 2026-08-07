from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from generate_apriltag_assets import build_mapping, load_slots  # noqa: E402
from generate_library_layout import build_layout, load_source  # noqa: E402


class LibraryMapTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = load_source(ROOT / "data" / "library_map.json")
        cls.layout = build_layout(cls.source)
        generated_map = build_mapping(cls.source, cls.layout, load_slots(cls.layout))
        generated_map.pop("_print_list")
        cls.tag_map = generated_map

    def test_generated_files_match_canonical_source(self) -> None:
        self.assertEqual(
            self.layout,
            json.loads((ROOT / "data" / "library_layout.json").read_text(encoding="utf-8")),
        )
        self.assertEqual(
            self.tag_map,
            json.loads((ROOT / "data" / "apriltag_library_map.json").read_text(encoding="utf-8")),
        )

    def test_all_consumers_use_identical_tag_map(self) -> None:
        canonical = (ROOT / "data" / "apriltag_library_map.json").read_bytes()
        consumers = (
            ROOT / "api" / "tags" / "apriltag_library_map.json",
            ROOT / "frontend" / "tag-placement" / "public" / "apriltag_library_map.json",
            ROOT / "aws" / "functions" / "yolo_worker" / "assets" / "apriltag_library_map.json",
        )
        for consumer in consumers:
            with self.subTest(consumer=consumer):
                self.assertEqual(canonical, consumer.read_bytes())

    def test_physical_quadrants_match_neighboring_display_cells(self) -> None:
        units = {unit["unit"]: unit for unit in self.layout["units"]}
        slots = {(slot["unit"], slot["col"], slot["row"]): slot for slot in self.layout["slots"]}

        def canonical_col(unit: dict, display_col: int) -> int:
            return unit["cols"] + 1 - display_col if unit.get("mirrored") else display_col

        for tag_id, tag in self.tag_map["tags"].items():
            unit = units[tag["unit"]]
            intersection = tag["physical_intersection"]
            left, right = intersection["between_display_cols"]
            bottom, top = intersection["between_rows"]
            physical_neighbors = {
                "top_left": (left, top),
                "top_right": (right, top),
                "bottom_right": (right, bottom),
                "bottom_left": (left, bottom),
            }
            expected = {}
            for quadrant, (display_col, row) in physical_neighbors.items():
                slot = slots[(tag["unit"], canonical_col(unit, display_col), row)]
                if slot["status"] == "usable":
                    expected[quadrant] = slot["shelf_id"]
            with self.subTest(tag_id=tag_id):
                self.assertEqual(expected, tag["quadrants"])

    def test_every_usable_slot_is_covered(self) -> None:
        usable = {slot["shelf_id"] for slot in self.layout["slots"] if slot["status"] == "usable"}
        covered = {
            shelf_id
            for tag in self.tag_map["tags"].values()
            for shelf_id in tag["quadrants"].values()
        }
        self.assertEqual(usable, covered)

    def test_physical_layout_matches_golden_fingerprint(self) -> None:
        payload = {
            "units": self.layout["units"],
            "slots": [
                {"slot_id": slot["slot_id"], "status": slot["status"]}
                for slot in self.layout["slots"]
            ],
            "tags": self.tag_map["tags"],
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        self.assertEqual(
            self.source["golden"]["physical_layout_sha256"],
            hashlib.sha256(raw).hexdigest(),
        )
        self.assertEqual(self.source["golden"]["tag_count"], len(self.tag_map["tags"]))


if __name__ == "__main__":
    unittest.main()
