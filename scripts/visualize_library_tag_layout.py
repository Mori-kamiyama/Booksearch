"""Visualize the library shelf layout and AprilTag placements.

Usage:
  uv run python scripts/visualize_library_tag_layout.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LAYOUT = REPO_ROOT / "data" / "library_layout.json"
DEFAULT_TAG_MAP = REPO_ROOT / "data" / "apriltag_library_map.json"
DEFAULT_OUTPUT = REPO_ROOT / "outputs" / "apriltag_library" / "library_tag_layout_visual.png"

CELL = 92
GAP = 72
MARGIN = 70
TITLE_H = 96
UNIT_LABEL_H = 46
LEGEND_H = 140
SIDE_GAP = 120

COLORS = {
    "usable": (246, 252, 249),
    "empty": (232, 234, 237),
    "grid": (48, 66, 78),
    "tag": (18, 91, 76),
    "text": (24, 34, 45),
    "muted": (99, 114, 130),
    "tag_text": (255, 255, 255),
    "border": (198, 208, 216),
    "side": (247, 248, 255),
}


def font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial Bold.ttf" if bold else "/Library/Fonts/Arial.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def slot_lookup(layout: dict[str, Any]) -> dict[tuple[str, int, int], dict[str, Any]]:
    return {
        (slot["unit"], slot["col"], slot["row"]): slot
        for slot in layout["slots"]
    }


def tag_lookup(tag_map: dict[str, Any]) -> dict[tuple[str, int, int], int]:
    out = {}
    for tag_id, cfg in tag_map["tags"].items():
        inter = cfg["physical_intersection"]
        out[(cfg["unit"], inter["between_display_cols"][0], inter["between_rows"][0])] = int(tag_id)
    return out


def draw_centered(draw: ImageDraw.ImageDraw, xy: tuple[int, int, int, int], text: str, fill, fnt) -> None:
    bbox = draw.textbbox((0, 0), text, font=fnt)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = xy[0] + (xy[2] - xy[0] - tw) / 2
    y = xy[1] + (xy[3] - xy[1] - th) / 2 - 2
    draw.text((x, y), text, fill=fill, font=fnt)


def unit_origin(unit_index: int) -> tuple[int, int]:
    return (
        MARGIN + (unit_index - 1) * (13 * CELL + GAP),
        MARGIN + TITLE_H + UNIT_LABEL_H,
    )


def side_unit_origin(base_w: int, side_index: int) -> tuple[int, int]:
    return (
        MARGIN + base_w + SIDE_GAP + (side_index - 1) * (3 * CELL + GAP),
        MARGIN + TITLE_H + UNIT_LABEL_H,
    )


def draw_unit(
    draw: ImageDraw.ImageDraw,
    slots: dict[tuple[str, int, int], dict[str, Any]],
    tags: dict[tuple[str, int, int], int],
    unit: str,
    origin: tuple[int, int],
    cols: int,
    rows: int,
    label: str,
    mirrored: bool = False,
) -> None:
    ox, oy = origin
    draw.text((ox, oy - UNIT_LABEL_H), label, fill=COLORS["text"], font=font(30, bold=True))
    draw.text((ox, oy - UNIT_LABEL_H + 34), "rows: bottom -> top / cols: entrance side -> far side", fill=COLORS["muted"], font=font(17))

    for col in range(1, cols + 1):
        for row in range(1, rows + 1):
            x0 = ox + (col - 1) * CELL
            y0 = oy + (rows - row) * CELL
            actual_col = cols + 1 - col if mirrored else col
            slot = slots.get((unit, actual_col, row))
            status = "usable" if not slot else slot["status"]
            fill = COLORS["side"] if unit.startswith("side") and status == "usable" else COLORS[status]
            draw.rectangle((x0, y0, x0 + CELL, y0 + CELL), fill=fill, outline=COLORS["border"], width=2)
            if status == "empty":
                draw.line((x0 + 12, y0 + 12, x0 + CELL - 12, y0 + CELL - 12), fill=(160, 166, 173), width=3)
                draw.line((x0 + CELL - 12, y0 + 12, x0 + 12, y0 + CELL - 12), fill=(160, 166, 173), width=3)
            else:
                draw_centered(draw, (x0, y0, x0 + CELL, y0 + CELL), f"c{actual_col:02d}\nr{row:02d}", COLORS["muted"], font(17))

    for x in range(1, cols):
        for y in range(1, rows):
            tag_id = tags.get((unit, x, y))
            if tag_id is None:
                continue
            cx = ox + x * CELL
            cy = oy + (rows - y) * CELL
            r = 21
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=COLORS["tag"], outline="white", width=3)
            draw_centered(draw, (cx - r, cy - r, cx + r, cy + r), str(tag_id), COLORS["tag_text"], font(16, bold=True))

    for col in range(1, cols + 1):
        x0 = ox + (col - 1) * CELL
        draw_centered(draw, (x0, oy + rows * CELL + 6, x0 + CELL, oy + rows * CELL + 34), str(col), COLORS["muted"], font(16))
    for row in range(1, rows + 1):
        y0 = oy + (rows - row) * CELL
        draw_centered(draw, (ox - 42, y0, ox - 8, y0 + CELL), str(row), COLORS["muted"], font(16))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--layout", default=str(DEFAULT_LAYOUT))
    parser.add_argument("--tag-map", default=str(DEFAULT_TAG_MAP))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    layout = load_json(Path(args.layout))
    tag_map = load_json(Path(args.tag_map))
    slots = slot_lookup(layout)
    tags = tag_lookup(tag_map)

    base_units = [u for u in layout["units"] if u["kind"] == "base"]
    side_units = [u for u in layout["units"] if u["kind"] == "side"]
    base_w = len(base_units) * 13 * CELL + max(0, len(base_units) - 1) * GAP
    side_w = len(side_units) * 3 * CELL + max(0, len(side_units) - 1) * GAP
    width = MARGIN * 2 + base_w + SIDE_GAP + side_w
    height = MARGIN * 2 + TITLE_H + UNIT_LABEL_H + 7 * CELL + 56 + LEGEND_H
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)

    draw.text((MARGIN, MARGIN), "Library Shelf + AprilTag Placement", fill=COLORS["text"], font=font(42, bold=True))
    draw.text(
        (MARGIN, MARGIN + 54),
        "Tags are placed at grid intersections. Each green circle maps nearby quadrants to shelf slots.",
        fill=COLORS["muted"],
        font=font(23),
    )

    for unit_index, unit_cfg in enumerate(base_units, start=1):
        draw_unit(
            draw,
            slots,
            tags,
            unit_cfg["unit"],
            unit_origin(unit_index),
            13,
            7,
            f"{unit_cfg['unit']}  entrance order {unit_index}",
            mirrored=bool(unit_cfg.get("mirrored", False)),
        )

    for side_index in range(1, len(side_units) + 1):
        unit = f"side-{side_index:02d}"
        draw_unit(draw, slots, tags, unit, side_unit_origin(base_w, side_index), 3, 7, unit)

    legend_y = MARGIN + TITLE_H + UNIT_LABEL_H + 7 * CELL + 78
    draw.rectangle((MARGIN, legend_y, MARGIN + 48, legend_y + 48), fill=COLORS["usable"], outline=COLORS["border"], width=2)
    draw.text((MARGIN + 64, legend_y + 9), "usable shelf box", fill=COLORS["text"], font=font(23))
    draw.rectangle((MARGIN + 310, legend_y, MARGIN + 358, legend_y + 48), fill=COLORS["empty"], outline=COLORS["border"], width=2)
    draw.line((MARGIN + 320, legend_y + 10, MARGIN + 348, legend_y + 38), fill=(160, 166, 173), width=3)
    draw.line((MARGIN + 348, legend_y + 10, MARGIN + 320, legend_y + 38), fill=(160, 166, 173), width=3)
    draw.text((MARGIN + 374, legend_y + 9), "empty: center 4 cols, rows 1-5 (mirrored units shown physically)", fill=COLORS["text"], font=font(23))
    draw.ellipse((MARGIN + 980, legend_y, MARGIN + 1028, legend_y + 48), fill=COLORS["tag"], outline="white", width=3)
    draw_centered(draw, (MARGIN + 980, legend_y, MARGIN + 1028, legend_y + 48), "tag", COLORS["tag_text"], font(14, bold=True))
    draw.text((MARGIN + 1046, legend_y + 9), "AprilTag ID at intersection", fill=COLORS["text"], font=font(23))

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    img.save(output)
    print(f"wrote {output}")


if __name__ == "__main__":
    main()
