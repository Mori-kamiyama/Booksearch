# AprilTag Printing Plan

## Choice

Use AprilTag, not the older small test map.

- Dictionary: `DICT_APRILTAG_36h11`
- Tag IDs: `0..195`
- Mapping file: `data/apriltag_library_map.json`
- Printable PDF: `outputs/apriltag_library/apriltag_library_sheets.pdf`

`DICT_APRILTAG_25h9` only has 35 IDs, so it is too small for the full library
layout. `DICT_APRILTAG_36h11` has enough IDs and is already supported by the
current OpenCV-based detector.

## Placement Rule

Each tag is placed at a grid intersection, not inside a single box. The printed
set intentionally uses a sparse layout, not every possible intersection.

For example, a printed label like:

```text
tag 000  base-01  x01/y01
```

means:

- base shelf unit: `base-01`
- place the tag at the intersection between columns 1 and 2
- place it at the intersection between rows 1 and 2
- rows are counted from bottom to top
- base units are counted from the entrance side

The small `TL/TR/BR/BL` text on each printed tag shows which shelf slot each
quadrant maps to.

## Generated Counts

- Printable tags: 196
- Covered usable shelf slots: 368
- Missing usable slots: 0
- Average tag references per usable shelf slot: `2.0`

The tag count is smaller than the full-intersection plan because the generator
keeps a checkerboard-like subset of intersections and then adds only the tags
needed to cover under-represented shelf slots.

## Regeneration

```bash
uv run python scripts/generate_apriltag_assets.py
```

This regenerates:

- `data/apriltag_library_map.json`
- `outputs/apriltag_library/apriltag_library_print_list.json`
- `outputs/apriltag_library/apriltag_library_sheets.pdf`
- `outputs/apriltag_library/apriltag_library_sheet_*.png`

## Recognition Use

Use this map instead of the small test map:

```bash
uv run python scripts/build_book_catalog.py path/to/shelf.jpg \
  --apriltag-map data/apriltag_library_map.json
```
