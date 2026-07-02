"""Generate the library shelf layout JSON.

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
BASE_UNITS = 4
SIDE_UNITS = 4

# base ユニットの空白区画。最大の 4列×5段。
BASE_EMPTY_LARGE = {"cols": [4, 5, 6, 7], "rows": [1, 2, 3, 4, 5]}
# 2つ目の区画は 4列×3段で、上端(row5)を1つ目の空白と揃える。
BASE_EMPTY_SMALL = {"cols": [10, 11, 12, 13], "rows": [3, 4, 5]}


def empty_regions_for_unit(unit: int) -> list[dict[str, Any]]:
    # base-01〜03 は最大の 4×5 区画のみ。base-04 のみ小区画も空白。
    if unit == 4:
        return [BASE_EMPTY_LARGE, BASE_EMPTY_SMALL]
    return [BASE_EMPTY_LARGE]


def is_base_empty(unit: int, col: int, row: int) -> bool:
    return any(
        col in region["cols"] and row in region["rows"]
        for region in empty_regions_for_unit(unit)
    )


def base_slot(unit: int, col: int, row: int) -> dict[str, Any]:
    is_empty = is_base_empty(unit, col, row)
    shelf_id = f"base-{unit:02d}-c{col:02d}-r{row:02d}"
    return {
        "slot_id": shelf_id,
        "shelf_id": shelf_id if not is_empty else None,
        "recognition_code": shelf_id if not is_empty else None,
        "kind": "base",
        "unit": f"base-{unit:02d}",
        "unit_index_from_entrance": unit,
        "col": col,
        "row": row,
        "status": "empty" if is_empty else "usable",
        "label_ja": f"入口側から{unit}台目 {col}列目 下から{row}段目",
    }


def side_slot(unit: int, col: int, row: int) -> dict[str, Any]:
    shelf_id = f"side-{unit:02d}-c{col:02d}-r{row:02d}"
    return {
        "slot_id": shelf_id,
        "shelf_id": shelf_id,
        "recognition_code": shelf_id,
        "kind": "side",
        "unit": f"side-{unit:02d}",
        "unit_index": unit,
        "col": col,
        "row": row,
        "status": "usable",
        "label_ja": f"サイド本棚{unit}台目 {col}列目 下から{row}段目",
    }


def build_layout() -> dict[str, Any]:
    slots: list[dict[str, Any]] = []
    for unit in range(1, BASE_UNITS + 1):
        for col in range(1, 14):
            for row in range(1, 8):
                slots.append(base_slot(unit, col, row))

    for unit in range(1, SIDE_UNITS + 1):
        for col in range(1, 4):
            for row in range(1, 8):
                slots.append(side_slot(unit, col, row))

    usable_slots = [slot for slot in slots if slot["status"] == "usable"]
    empty_slots = [slot for slot in slots if slot["status"] == "empty"]

    return {
        "schema_version": 1,
        "coordinate_convention": {
            "base_units": "4 parallel bookshelf units counted from the entrance side",
            "base_cols": "1..13 counted from the entrance side",
            "rows": "1..7 counted bottom to top",
            "side_bookshelves": "4 side bookshelf units, each a 3 x 7 grid",
        },
        "units": [
            {
                "unit": f"base-{unit:02d}",
                "kind": "base",
                "unit_index_from_entrance": unit,
                "cols": 13,
                "rows": 7,
                "empty_rule": {"regions": empty_regions_for_unit(unit)},
            }
            for unit in range(1, BASE_UNITS + 1)
        ]
        + [
            {
                "unit": f"side-{unit:02d}",
                "kind": "side",
                "unit_index": unit,
                "cols": 3,
                "rows": 7,
                "empty_rule": None,
            }
            for unit in range(1, SIDE_UNITS + 1)
        ],
        "counts": {
            "total_slots_including_empty": len(slots),
            "usable_slots": len(usable_slots),
            "empty_slots": len(empty_slots),
            "base_slots_including_empty": BASE_UNITS * 13 * 7,
            "base_usable_slots": sum(1 for slot in usable_slots if slot["kind"] == "base"),
            "base_empty_slots": sum(1 for slot in empty_slots if slot["kind"] == "base"),
            "side_usable_slots": sum(1 for slot in usable_slots if slot["kind"] == "side"),
        },
        "slots": slots,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    output = Path(args.output)
    layout = build_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(layout, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {output}")
    print(json.dumps(layout["counts"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
