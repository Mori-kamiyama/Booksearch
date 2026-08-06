"""Generate the compatibility shelf-layout JSON from the canonical map.

Usage:
  uv run python scripts/generate_library_layout.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = REPO_ROOT / "data" / "library_layout.json"
DEFAULT_SOURCE = REPO_ROOT / "data" / "library_map.json"
SYNC_LAYOUT_OUTPUTS = (
    REPO_ROOT / "frontend" / "src" / "data" / "library_layout.json",
)


def load_source(path: Path = DEFAULT_SOURCE) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def compatibility_regions(unit: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"cols": region["canonical_cols"], "rows": region["rows"]}
        for region in unit.get("empty_regions", [])
    ]


def is_empty(unit: dict[str, Any], col: int, row: int) -> bool:
    return any(
        col in region["canonical_cols"] and row in region["rows"]
        for region in unit.get("empty_regions", [])
    )


def slot_for(unit: dict[str, Any], col: int, row: int) -> dict[str, Any]:
    empty = is_empty(unit, col, row)
    unit_id = unit["unit"]
    shelf_id = f"{unit_id}-c{col:02d}-r{row:02d}"
    if unit["kind"] == "base":
        label = f"入口側から{unit['unit_index_from_entrance']}台目 {col}列目 下から{row}段目"
    else:
        label = f"サイド本棚{unit['unit_index']}台目 {col}列目 下から{row}段目"
    return {
        "slot_id": shelf_id,
        "shelf_id": shelf_id if not empty else None,
        "recognition_code": shelf_id if not empty else None,
        "kind": unit["kind"],
        "unit": unit_id,
        **({"unit_index_from_entrance": unit["unit_index_from_entrance"]} if unit["kind"] == "base" else {"unit_index": unit["unit_index"]}),
        "col": col,
        "row": row,
        "status": "empty" if empty else "usable",
        "label_ja": label,
    }


def build_layout(source: dict[str, Any] | None = None) -> dict[str, Any]:
    source = source or load_source()
    slots: list[dict[str, Any]] = []
    for unit in source["units"]:
        for col in range(1, int(unit["cols"]) + 1):
            for row in range(1, int(unit["rows"]) + 1):
                slots.append(slot_for(unit, col, row))

    usable_slots = [slot for slot in slots if slot["status"] == "usable"]
    empty_slots = [slot for slot in slots if slot["status"] == "empty"]

    return {
        "schema_version": 2,
        "map_id": source["map_id"],
        "coordinate_convention": {
            "canonical_cols": "Shelf IDs use stable canonical columns",
            "display_cols": "Physical left-to-right columns; mirrored units reverse canonical columns",
            "rows": "1..7 counted bottom to top",
        },
        "units": [
            {
                **{key: value for key, value in unit.items() if key not in {"display_mirrored", "empty_regions"}},
                "mirrored": bool(unit.get("display_mirrored", False)),
                "empty_rule": {"regions": compatibility_regions(unit)} if unit.get("empty_regions") else None,
            }
            for unit in source["units"]
        ],
        "counts": {
            "total_slots_including_empty": len(slots),
            "usable_slots": len(usable_slots),
            "empty_slots": len(empty_slots),
            "base_slots_including_empty": sum(1 for slot in slots if slot["kind"] == "base"),
            "base_usable_slots": sum(1 for slot in usable_slots if slot["kind"] == "base"),
            "base_empty_slots": sum(1 for slot in empty_slots if slot["kind"] == "base"),
            "side_usable_slots": sum(1 for slot in usable_slots if slot["kind"] == "side"),
        },
        "slots": slots,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--no-sync-consumers", action="store_true")
    args = parser.parse_args()

    output = Path(args.output)
    layout = build_layout(load_source(Path(args.source)))
    serialized_layout = json.dumps(layout, ensure_ascii=False, indent=2) + "\n"
    outputs = [output]
    if not args.no_sync_consumers:
        outputs.extend(SYNC_LAYOUT_OUTPUTS)
    for destination in outputs:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(serialized_layout, encoding="utf-8")
        print(f"wrote {destination}")
    print(json.dumps(layout["counts"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
