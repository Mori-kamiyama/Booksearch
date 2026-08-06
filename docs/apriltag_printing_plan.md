# AprilTag Printing Plan

## Accepted placement

- Canonical source: `data/library_map.json`
- Dictionary: `DICT_APRILTAG_36h11`
- Tag IDs: `0..156`
- Generated mapping: `data/apriltag_library_map.json`
- Placement guide: `outputs/apriltag_library/library_tag_layout_visual.png`
- Printable PDF: `outputs/apriltag_library/apriltag_library_sheets.pdf`

Tags are placed at physical grid intersections. `x` on a print label means the
intersection between display columns `x` and `x+1`; rows are counted bottom to
top. `TL/TR/BR/BL` are physical camera quadrants and point to canonical shelf
IDs. This distinction matters on mirrored `base-01` through `base-03`.

The sparse deterministic layout uses 157 tags and covers all 356 usable shelf
boxes at least once.

## Regeneration

```bash
uv run python scripts/generate_library_layout.py
uv run python scripts/generate_apriltag_assets.py
uv run python scripts/visualize_library_tag_layout.py
uv run python -m unittest discover -s tests -p 'test_library_map.py'
```

The AprilTag generator synchronizes the mapping used by the standalone guide,
tag detector, normal frontend, and AWS YOLO Worker. `prepare_assets.sh` always
refreshes the worker copy; it no longer preserves a stale legacy map.

## Reassigning existing observations

Changing quadrant geometry cannot be migrated from `shelf_id` alone. Re-run
tag detection against the original shelf images, then rebuild candidates:

```bash
uv run python scripts/shelf_locator.py \
  --catalog outputs/book_catalog_data_260702/catalog.json \
  --mapping data/apriltag_library_map.json \
  --output outputs/book_catalog_data_260702/catalog_reassigned_apriltag_canonical_v2_20260806.json \
  --max-tag-distance 1600

uv run python scripts/import_bookshelf_catalog.py \
  --catalog outputs/book_catalog_data_260702/catalog_reassigned_apriltag_canonical_v2_20260806.json \
  --db outputs/library/library.db \
  --output outputs/book_catalog_data_260702/bookshelf_data_reassigned_apriltag_canonical_v2_20260806.json \
  --replace-candidates
```

The pre-migration SQLite backup is
`outputs/library/library.before_library_map_v2_20260806.db`.
