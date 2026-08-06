# Library map unification (2026-08-06)

## Outcome

The accepted placement PNG is now backed by one source,
`data/library_map.json`, with map ID `booksearch-library-physical-v2`.
Generated frontend, API, standalone guide, and AWS worker maps are byte-identical.

The original split had three independent failure modes:

1. the normal app fetched a stale `/api/shelves` map after initially loading the
   correct embedded map;
2. AWS packaging preserved an existing legacy map instead of refreshing it;
3. mirrored tag intersections moved horizontally without swapping physical
   `top_left/top_right` shelf assignments.

The third issue meant the PNG could show a tag at the intended intersection
while camera assignment still chose the shelf on the opposite side.

## Decisions

- Shelf IDs use canonical columns and never change with presentation.
- UI x positions use display columns.
- `physical_intersection` is explicitly a display coordinate.
- tag quadrants are physical camera quadrants containing canonical shelf IDs.
- `ShelfMapHighlight`, `ShelfUnitGrid`, and `ShelfMiniMap` resolve positions
  through the same functions in `frontend/src/lib/shelf.ts`.
- the normal frontend uses its build-time canonical map for tag labels; detector
  responses expose `map_id` so a stale server map is rejected.
- new AWS shelf assignments carry `map_id` and `coordinate_schema_version`.

## Existing data

The July 2026 candidate DB was derived from the incorrect mirrored quadrant
mapping, so a shelf-ID-only transform was not reliable. Original images were
reprocessed with v2. Before replacement the DB had 370 unique pairs and 455
observations; after replacement it has 338 unique pairs and 479 observations.
The previous DB remains recoverable at
`outputs/library/library.before_library_map_v2_20260806.db`.

## Verification

- placement PNG matches the accepted image exactly:
  `2a16ace765413112802597619886a2c089f9c97b038519b7f953ad71aafcc24a`
- Python map invariants: 5 passed
- frontend Vitest: 19 passed
- normal frontend production build: passed
- standalone tag-placement build: passed
- backend Go tests: passed
- AWS Go API tests: passed
- local browser `/map`: all structural empty cells render in the same mirrored positions
- local browser `/map/base-01-c01-r02`: highlight rendered at display column 13 (`x=384`)

## Production deployment

Deployed and verified on 2026-08-06:

- AWS CloudFormation stack `booksearch`: `UPDATE_COMPLETE`
- canonical map uploaded to `assets/apriltag_library_map.json`
- `/api/shelves`: `booksearch-library-physical-v2`, 157 tags
- CloudFront frontend cache invalidation: completed
- standalone placement guide: `https://booksearch-tag-placement.vercel.app`
- API and lookup worker catalog snapshots match the rebuilt local SQLite DB
