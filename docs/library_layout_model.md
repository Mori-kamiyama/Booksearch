# Library Layout Model

## Purpose

This note captures the physical shelf structure as a working model for shelf IDs,
AprilTag placement, and recognition-code generation. It is not yet a complete
inventory of books or genres.

The machine-readable source file is `data/library_layout.json`. Regenerate it
with:

```bash
uv run python scripts/generate_library_layout.py
```

## Known Structure

- The base library has 4 parallel bookshelf units, counted from the entrance
  side.
- Each base bookshelf unit is a 13 x 7 grid of boxes.
- In every base bookshelf unit, columns 4 through 7 have an empty area covering
  the bottom 5 rows.
- There are also 4 side bookshelves. Each side bookshelf is a 3 x 7 grid.

## Coordinate Convention

Use a stable grid coordinate before assigning human-facing labels:

- `unit`: one of the 4 parallel base bookshelf units, plus the side bookshelf
  units.
- `unit=1` is the entrance-side base bookshelf unit.
- `col`: 1 to 13 for base units, counted from the entrance side.
- `row`: 1 to 7, counted bottom to top.
- `slot`: one physical box at `(unit, col, row)`.

Physical orientation note:

- `base-01` faces the opposite left/right direction from the other base units
  in the AprilTag placement guide. The stable shelf IDs still use the same
  `c01..c13` coordinate shape, but tag quadrant mapping mirrors `base-01`
  columns when generating `data/apriltag_library_map.json`.

For the side bookshelves:

- `unit`: `side-01` through `side-04`
- `col`: 1 to 3
- `row`: 1 to 7

## Empty Slots

For every base bookshelf unit, these slots are empty:

- `col` in `4..7`
- `row` in `1..5`

That is `4 columns * 5 rows = 20` empty slots per base unit.

## Derived Counts

- Base capacity before blanks: `4 * 13 * 7 = 364`
- Empty base slots: `4 * 4 * 5 = 80`
- Usable base slots: `284`
- Side bookshelf slots: `4 * 3 * 7 = 84`
- Total usable slots: `368`

## Proposed Shelf ID Shape

Use IDs that encode the physical coordinate directly:

- Base: `base-{unit}-c{col:02d}-r{row:02d}`
- Side: `side-{unit}-c{col:02d}-r{row:02d}`

Examples:

- `base-01-c01-r01`
- `base-01-c13-r07`
- `base-03-c08-r02`
- `side-01-c03-r07`
- `side-04-c03-r07`

This is more scalable than the current test IDs like `shelf-A-01`, because it
can represent all 4 parallel bookshelf units and the side bookshelf without
inventing many letter lanes.

## Recognition-Code Hypothesis

The recognition system should map camera-visible tags to these slot IDs, not
directly to book titles. A later recognition-code set can be generated from the
layout:

1. Generate all valid slot IDs.
2. Exclude empty slots.
3. Place AprilTags at grid intersections or known shelf boundaries.
4. Map tag quadrants/regions to nearby slot IDs.
5. Accumulate book observations into `book_shelf_candidates`.

The current implementation already supports the final step with AprilTag-based
`shelf_id` assignment. The missing piece is a full layout-derived map instead of
the small `shelf-A-*` / `shelf-B-*` test map.

## Open Questions

- Is `row=1` bottom-to-top acceptable for users, or should the UI display
  Japanese labels like `下から1段目`?
- Are boxes the right shelf-location unit, or should multiple boxes sometimes
  be grouped into a larger shelf region?
