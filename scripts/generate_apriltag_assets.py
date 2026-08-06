"""Generate AprilTag mapping and printable sheets from the canonical map.

Usage:
  uv run python scripts/generate_apriltag_assets.py
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import cv2
from PIL import Image, ImageDraw, ImageFont

from generate_library_layout import build_layout, load_source


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = REPO_ROOT / "data" / "library_map.json"
DEFAULT_MAP = REPO_ROOT / "data" / "apriltag_library_map.json"
DEFAULT_PRINT_DIR = REPO_ROOT / "outputs" / "apriltag_library"
SYNC_MAP_OUTPUTS = (
    REPO_ROOT / "api" / "tags" / "apriltag_library_map.json",
    REPO_ROOT / "frontend" / "tag-placement" / "public" / "apriltag_library_map.json",
    REPO_ROOT / "aws" / "functions" / "yolo_worker" / "assets" / "apriltag_library_map.json",
)

PRINT_DPI = 300
A4_PX = (2480, 3508)  # 300dpi
# 切り取りタイル一片の実寸（白のクワイエットゾーン込み）。
TAG_TILE_CM = 2.6
TAG_TILE_PX = round(TAG_TILE_CM / 2.54 * PRINT_DPI)
TAG_QUIET_PX = round(TAG_TILE_PX * 0.10)  # 検出用の白余白（各辺）
TAG_SIZE_PX = TAG_TILE_PX - 2 * TAG_QUIET_PX  # 黒パターン本体のサイズ
SHEET_MARGIN = 100
SHEET_COLS = 4
SHEET_ROWS = 5


def load_slots(layout: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {slot["slot_id"]: slot for slot in layout["slots"]}


def usable_shelf_ids(layout: dict[str, Any]) -> set[str]:
    return {
        slot["shelf_id"]
        for slot in layout["slots"]
        if slot["status"] == "usable" and slot["shelf_id"]
    }


def units(layout: dict[str, Any]) -> list[dict[str, Any]]:
    return layout["units"]


def shelf_id(slots: dict[str, dict[str, Any]], unit: str, col: int, row: int) -> str | None:
    slot_id = f"{unit}-c{col:02d}-r{row:02d}"
    slot = slots.get(slot_id)
    if not slot or slot["status"] != "usable":
        return None
    return slot["shelf_id"]


def quadrant_map_for_intersection(
    slots: dict[str, dict[str, Any]],
    unit_cfg: dict[str, Any],
    x: int,
    y: int,
) -> dict[str, str]:
    unit = unit_cfg["unit"]
    quadrants = {
        "top_left": shelf_id(slots, unit, x, y + 1),
        "top_right": shelf_id(slots, unit, x + 1, y + 1),
        "bottom_right": shelf_id(slots, unit, x + 1, y),
        "bottom_left": shelf_id(slots, unit, x, y),
    }
    return {name: sid for name, sid in quadrants.items() if sid}


def candidate_intersections(
    layout: dict[str, Any],
    slots: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for unit_cfg in units(layout):
        unit = unit_cfg["unit"]
        for x in range(1, int(unit_cfg["cols"])):
            for y in range(1, int(unit_cfg["rows"])):
                quadrants = quadrant_map_for_intersection(slots, unit_cfg, x, y)
                if not quadrants:
                    continue
                candidates.append({"unit": unit, "x": x, "y": y, "quadrants": quadrants})
    return candidates


def select_sparse_intersections(
    layout: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    usable = usable_shelf_ids(layout)
    covered = {shelf_id: 0 for shelf_id in usable}
    selected: list[dict[str, Any]] = []

    def add(candidate: dict[str, Any]) -> None:
        selected.append(candidate)
        for shelf in candidate["quadrants"].values():
            if shelf in covered:
                covered[shelf] += 1

    for candidate in candidates:
        if (int(candidate["x"]) + int(candidate["y"])) % 2 == 0:
            add(candidate)

    selected_ids = {(c["unit"], c["x"], c["y"]) for c in selected}
    while any(count == 0 for count in covered.values()):
        missing = {shelf for shelf, count in covered.items() if count == 0}
        remaining = [
            c for c in candidates
            if (c["unit"], c["x"], c["y"]) not in selected_ids
        ]
        best = max(
            remaining,
            key=lambda c: (
                sum(1 for shelf in c["quadrants"].values() if shelf in missing),
                len(c["quadrants"]),
            ),
        )
        selected_ids.add((best["unit"], best["x"], best["y"]))
        add(best)

    return selected


def build_mapping(source: dict[str, Any], layout: dict[str, Any], slots: dict[str, dict[str, Any]]) -> dict[str, Any]:
    tags: dict[str, Any] = {}
    print_list: list[dict[str, Any]] = []
    candidates = candidate_intersections(layout, slots)
    selected = select_sparse_intersections(layout, candidates)
    units_by_id = {unit["unit"]: unit for unit in units(layout)}

    for tag_id, candidate in enumerate(selected):
        unit = candidate["unit"]
        unit_cfg = units_by_id[unit]
        # Quadrants always use canonical shelf IDs. Only the physical/display
        # intersection is mirrored, matching the placement PNG.
        x = int(unit_cfg["cols"]) - int(candidate["x"]) if unit_cfg.get("mirrored", False) else candidate["x"]
        y = candidate["y"]
        canonical_quadrants = candidate["quadrants"]
        if unit_cfg.get("mirrored", False):
            # The tag stays upright in the physical guide, so camera-left must
            # point to the shelf physically left of the tag after mirroring.
            mirrored_names = {
                "top_left": "top_right",
                "top_right": "top_left",
                "bottom_right": "bottom_left",
                "bottom_left": "bottom_right",
            }
            quadrants = {
                physical_name: canonical_quadrants[canonical_name]
                for physical_name, canonical_name in mirrored_names.items()
                if canonical_name in canonical_quadrants
            }
        else:
            quadrants = canonical_quadrants
        tags[str(tag_id)] = {
            "unit": unit,
            "physical_intersection": {"between_display_cols": [x, x + 1], "between_rows": [y, y + 1]},
            "expected_angle_deg": source["tag_expected_angle_deg"],
            "angle_tolerance_deg": source["tag_angle_tolerance_deg"],
            "quadrants": quadrants,
        }
        print_list.append({"tag_id": tag_id, "unit": unit, "x": x, "y": y, "quadrants": quadrants})

    return {
        "schema_version": 2,
        "map_id": source["map_id"],
        "coordinate_schema_version": 2,
        "dictionary": source["tag_dictionary"],
        "auto_distance_scale": source["tag_auto_distance_scale"],
        "placement": "physical_intersection uses display coordinates; quadrants use canonical shelf IDs.",
        "selection": {
            "strategy": source["tag_selection"]["strategy"],
            "coverage": source["tag_selection"]["coverage"],
        },
        "tags": tags,
        "_print_list": print_list,
    }


def font(size: int) -> ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def marker_image(dictionary: cv2.aruco.Dictionary, tag_id: int) -> Image.Image:
    marker = cv2.aruco.generateImageMarker(dictionary, tag_id, TAG_SIZE_PX)
    return Image.fromarray(marker).convert("RGB")


def draw_tag_cell(
    page: Image.Image,
    dictionary: cv2.aruco.Dictionary,
    item: dict[str, Any],
    x0: int,
    y0: int,
    cell_w: int,
    cell_h: int,
) -> None:
    draw = ImageDraw.Draw(page)
    tag = marker_image(dictionary, item["tag_id"])
    tag_x = x0 + (cell_w - TAG_SIZE_PX) // 2
    tag_y = y0 + 46 + TAG_QUIET_PX
    # 白のクワイエットゾーン込みのタイル枠（切り取り線）。
    tile_x0 = tag_x - TAG_QUIET_PX
    tile_y0 = tag_y - TAG_QUIET_PX
    draw.rectangle(
        (tile_x0, tile_y0, tile_x0 + TAG_TILE_PX - 1, tile_y0 + TAG_TILE_PX - 1),
        outline=(180, 180, 180),
        width=1,
    )
    page.paste(tag, (tag_x, tag_y))

    title = f"tag {item['tag_id']:03d}  {item['unit']}  x{item['x']:02d}/y{item['y']:02d}"
    draw.text((x0 + 24, y0 + 14), title, fill=(0, 0, 0), font=font(30))

    q = item["quadrants"]
    lines = [
        f"TL {q.get('top_left', '-')}",
        f"TR {q.get('top_right', '-')}",
        f"BR {q.get('bottom_right', '-')}",
        f"BL {q.get('bottom_left', '-')}",
    ]
    text_y = tag_y + TAG_SIZE_PX + TAG_QUIET_PX + 16
    for line in lines:
        draw.text((x0 + 44, text_y), line, fill=(0, 0, 0), font=font(24))
        text_y += 30

    draw.rectangle((x0, y0, x0 + cell_w - 1, y0 + cell_h - 1), outline=(210, 210, 210), width=2)


def write_print_sheets(print_list: list[dict[str, Any]], output_dir: Path, dictionary_name: str) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    for old_path in output_dir.glob("apriltag_library_sheet_*.png"):
        old_path.unlink()
    old_pdf = output_dir / "apriltag_library_sheets.pdf"
    if old_pdf.exists():
        old_pdf.unlink()

    dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, dictionary_name))
    pages: list[Image.Image] = []
    cell_w = (A4_PX[0] - SHEET_MARGIN * 2) // SHEET_COLS
    cell_h = (A4_PX[1] - SHEET_MARGIN * 2) // SHEET_ROWS
    tags_per_page = SHEET_COLS * SHEET_ROWS
    page_paths: list[Path] = []

    for page_index in range(math.ceil(len(print_list) / tags_per_page)):
        page = Image.new("RGB", A4_PX, "white")
        page_items = print_list[page_index * tags_per_page : (page_index + 1) * tags_per_page]
        for i, item in enumerate(page_items):
            col = i % SHEET_COLS
            row = i // SHEET_COLS
            x0 = SHEET_MARGIN + col * cell_w
            y0 = SHEET_MARGIN + row * cell_h
            draw_tag_cell(page, dictionary, item, x0, y0, cell_w, cell_h)
        page_path = output_dir / f"apriltag_library_sheet_{page_index + 1:02d}.png"
        page.save(page_path)
        page_paths.append(page_path)
        pages.append(page)

    pdf_path = output_dir / "apriltag_library_sheets.pdf"
    if pages:
        pages[0].save(pdf_path, save_all=True, append_images=pages[1:], resolution=300.0)
        page_paths.append(pdf_path)
    return page_paths


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--map-output", default=str(DEFAULT_MAP))
    parser.add_argument("--print-dir", default=str(DEFAULT_PRINT_DIR))
    parser.add_argument("--no-sync-consumers", action="store_true")
    args = parser.parse_args()

    source = load_source(Path(args.source))
    layout = build_layout(source)
    slots = load_slots(layout)
    mapping = build_mapping(source, layout, slots)
    print_list = mapping.pop("_print_list")

    map_output = Path(args.map_output)
    serialized_mapping = json.dumps(mapping, ensure_ascii=False, indent=2) + "\n"
    outputs = [map_output]
    if not args.no_sync_consumers:
        outputs.extend(SYNC_MAP_OUTPUTS)
    for output in outputs:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(serialized_mapping, encoding="utf-8")

    print_list_path = Path(args.print_dir) / "apriltag_library_print_list.json"
    print_list_path.parent.mkdir(parents=True, exist_ok=True)
    print_list_path.write_text(json.dumps(print_list, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    page_paths = write_print_sheets(print_list, Path(args.print_dir), source["tag_dictionary"])
    for output in outputs:
        print(f"wrote {output}")
    print(f"wrote {print_list_path}")
    print(f"tags: {len(mapping['tags'])}")
    print(f"print files: {len(page_paths)}")
    for path in page_paths[-3:]:
        print(path)


if __name__ == "__main__":
    main()
