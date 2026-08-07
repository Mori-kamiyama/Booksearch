# Library Layout Model

## Source of truth

`data/library_map.json` is the only hand-edited physical shelf map. It defines
the coordinate convention, unit orientation, empty regions, AprilTag dictionary,
and deterministic tag-selection rule.

The following files are generated compatibility artifacts and must not be
edited independently:

- `data/library_layout.json`
- `frontend/src/data/library_layout.json`
- `data/apriltag_library_map.json`
- `api/tags/apriltag_library_map.json`
- `frontend/tag-placement/public/apriltag_library_map.json`
- `aws/functions/yolo_worker/assets/apriltag_library_map.json`

Regenerate them with:

```bash
uv run python scripts/generate_library_layout.py
uv run python scripts/generate_apriltag_assets.py
uv run python scripts/visualize_library_tag_layout.py
```

## Coordinates

- `canonical_col` is the stable column encoded in `shelf_id`.
- `display_col` is the physical left-to-right position in the placement PNG.
- `row` is always bottom-to-top.
- `physical_intersection` uses display coordinates.
- tag `quadrants` contain canonical shelf IDs for the physically adjacent boxes.

`base-01` through `base-03` are mirrored, so:

```text
display_col = 14 - canonical_col
canonical_col = 14 - display_col
```

Other units are not mirrored. All UI renderers use the shared conversion in
`frontend/src/lib/shelf.ts`.

## Physical structure

- `base-01` through `base-04`: 13 columns x 7 rows.
- `side-01` through `side-04`: 3 columns x 7 rows.
- Every base unit has canonical columns 4..7 empty in rows 1..5.
- `base-04` additionally has canonical columns 10..13 empty in rows 3..5.

Derived totals:

- 448 slots including structural empty boxes.
- 92 structural empty boxes.
- 356 usable shelf boxes.
- 157 AprilTags covering every usable shelf box.

Shelf IDs have the stable form
`{unit}-c{canonical_col:02d}-r{row:02d}`.

## Invariants

`tests/test_library_map.py` verifies that all generated consumers are identical,
all 157 physical quadrants point to the adjacent displayed shelf boxes, every
usable box is covered, and the physical layout matches its golden fingerprint.
The placement PNG SHA-256 stored in the source is the attached accepted layout.
