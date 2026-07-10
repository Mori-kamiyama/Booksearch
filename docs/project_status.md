# Project Status

## 2026-06-05

- DB confidence update was tested with the full/hidden book image pair.
- Observed behavior matched the expected shape: 43 total books, 10 hidden, 32 books seen twice and 10 seen once in the confidence test run.
- AWS Lambda/DynamoDB integration and a small frontend for shelf candidates were implemented before cleanup.
- Generated artifacts and raw local outputs were removed from the working tree.
- Git history rewrite attempt accidentally removed tracked files; recovery was performed from local Claude/Codex logs and file-history backups.

## 2026-06-12

- Additional source, AWS, frontend, E2E, model, and historical files were salvaged without overwriting the recovery directory.
- Verification passed for the backend Go tests, AWS Go API build/tests, Python syntax checks, and frontend production build.
- The recovered YOLO model loaded successfully and detected four `box` instances in `A4 - 9.png`.
- Embedded credentials in recovered historical scripts were removed before the recovery checkpoint was pushed to GitHub.
- Production Playwright E2E passed 22/22 against CloudFront and API Gateway:
  desktop Chromium and mobile Safari UI, search, shelf candidates, SPA routing,
  API health/search, legacy scan upload, and presigned S3 upload/start flow.

## 2026-06-19

- Fixed a production job stuck in `ocr_pending`: YOLO counted all crops in
  `crop_total`, while OCR only received readable crops. OCR now compares
  `ocr_done` with `ocr_total` or the job diagnostics `readable_count`.
- Updated Lookup Lambda to use the restored bundled `library.db` and open it as
  an immutable read-only SQLite database.
- Hardened the API Lambda request parser so job/status routes survive API
  Gateway payload-shape differences.
- Improved the job page so polling failures are visible instead of leaving the
  last status on screen forever.
- Reprocessed job `465791b6-2ece-48af-accd-06974db2e6a5`; it now finishes as
  `done` with 3 detected boxes, 17 OCR titles, and 16 DB matches.
- Redeployed API, OCR, Lookup, frontend S3/CloudFront, then reran production
  Playwright E2E: 22/22 passed.
- Deployed the current YOLO worker image to AWS so edge-touching wide/tall crop
  rejection (`edge_wide` / `edge_tall`) is active in production. The image was
  pushed as `edge-aspect-filter-20260619` and Lambda now resolves to digest
  `sha256:3ac833e0525afc06b20908897c87e7d321b7ef7398a38f54fb5fe60269a338ad`.
- Pinned YOLO worker `scipy==1.11.4` so the arm64 Lambda image uses a wheel
  instead of trying to compile the latest SciPy with the Lambda base GCC.
- Fixed the remaining production display issue where unreadable crops were
  correctly skipped by YOLO/OCR but still included in the Lookup catalog. Lookup
  now omits `skipped_low_quality` / `quality.readable=false` crops from
  `catalog.entries`; job `ccdd375b-76c5-4e72-afff-502b812eaed9` was reprocessed
  and now shows 1 readable box instead of 3 total detected boxes.
- Clarified the frontend location workflow: search results now always show a
  location row, using the highest-confidence shelf candidate when available and
  showing `場所未登録` when no shelf observation has been learned yet. The old
  `棚候補` navigation label was renamed to `本の場所`.
- Added a growing Google Books cover cache to the AWS API. Search/book detail
  responses now backfill missing `thumbnail` / `info_link` values by querying
  Google Books for up to 5 missing covers per request and storing results in
  `s3://booksearch-277707097118-ap-northeast-1/cache/google_book_covers.json`.
  Missing-cover results are cached, while transient Google Books 429/5xx errors
  are retried after 1 hour instead of being treated as long-term misses.

## 2026-06-29

- Improved the search-result location UI from a mock-ish row into a reviewable
  panel: top shelf candidate, confidence, observation count, alternate shelves,
  and local confirm/reject/correct actions.
- Added local backend support for `book_shelf_candidates` so search and book
  detail responses can attach `shelf_candidates` in the same shape as production.
- Production scan job `2aa5cf53-228a-4483-9016-6fc8252bbce3` previously read
  books but added `0` shelf observations because AprilTag votes from nearby tags
  conflicted. The hypothesis was that conflict should not discard the crop when
  the nearest tag still provides a useful shelf.
- Updated the YOLO worker so conflicting shelf votes assign the nearest voted
  shelf with diagnostic reason `nearest_of_conflicting_votes` instead of dropping
  the shelf assignment.
- Deployed the frontend to `https://d2uel8nex1m4w7.cloudfront.net/` and deployed
  the AWS stack update, including shelf observation/candidate DynamoDB tables and
  the new YOLO image.
- Verified with production scan job `d75d6990-7e55-47bf-92a9-3c9859c27b63`:
  `status=done`, `crop_total=4`, `ocr_done=2`, and
  `shelf_observations_added=15`.
- Verified production API visibility after the scan:
  `/api/shelf-candidates` returns the new candidates, and searches such as
  `詳説デザインマネジメント` include `shelf_candidates` with `shelf-B-02`.
- Production Playwright E2E now includes explicit checks that the scanned book
  appears with `この本はここにありそう` in search results and on the `本の場所`
  page; desktop Chromium and mobile Safari passed 26/26.

## 2026-07-03

- Implemented the Phase 1 UI slice from `docs/ui_phase1_playbook.md`: typed
  frontend API helpers, layout-backed shelf label utilities, common loading /
  empty / error states, bottom tabs, `/books/:id`, `/map`, and `/map/:shelfId`.
- General search now sends users to a detail page where the top shelf candidate
  is shown as a Japanese label plus a highlighted shelf grid, not as a raw
  shelf ID.
- Verified locally with `識別・予測・異常検知`, which resolves to
  `base-01-c02-r04` in the API and displays as
  `入口側から1台目 2列目 下から4段目` in the UI.
- Hypothesis: the map-first browse flow is good enough for Phase 1, but the
  density view will need better grouping or filtering once every shelf has many
  candidates; otherwise a single crowded cell can dominate the experience.

## 2026-07-10

- Started the local Phase 1 acceptance pass with the Go backend and Vite
  frontend running together.
- Verified the real user path: search for `識別` -> book detail -> Japanese
  shelf label and highlighted shelf map. Also verified map -> shelf cell ->
  `/map/base-01-c02-r04` -> shelf detail.
- Verified the book detail at 375px width: document width stayed at 375px with
  no horizontal overflow, and the human-readable shelf label remained visible.
- Local Playwright E2E passed 24 tests across desktop Chromium and mobile
  Safari. Two production-only scan API tests are skipped for each browser when
  `API_BASE` points at localhost; the local Phase 1 backend does not expose the
  presigned S3 endpoints. Before this adjustment those four checks failed with
  404/400, while all UI and read-only API checks passed.
- Added an E2E check for map-to-shelf-detail navigation. The remaining Phase 1
  work is visual review and deciding whether to commit/PR the current UI slice;
  no new backend work was needed.
- Adjusted the physical left/right convention for `base-01` through `base-03`.
  The layout source now marks those units as mirrored, and the frontend, tag
  generator, and tag-placement visualization all read the same flag. This
  keeps displayed shelf IDs adjacent to the tags that identify them.
- The physical tags on those units were pasted from a horizontally mirrored
  initial diagram. Their IDs and shelf quadrants stay anchored to that initial
  diagram, while only their physical intersections are mirrored; e.g. tag 29
  is the upper-left tag on the reflected first unit.
- The Go application now loads `data/apriltag_library_map.json` by default,
  so both scan jobs and `POST /api/tags/detect` use the generated physical tag
  map without requiring an `--apriltag-map` startup flag.
- Re-ran AprilTag detection and shelf assignment only (without repeating OCR)
  for `outputs/book_catalog_data_260702/catalog.json`. Replaced the SQLite
  shelf candidates with 455 accepted observations / 370 unique book-shelf
  pairs from the reassigned catalog, after saving
  `outputs/library/library.before_apriltag_reassign_20260710.db` as a backup.
- Added live AprilTag scanning to `/scan`: while the camera is active, a
  downscaled frame is sent to `POST /api/tags/detect` every 1.6 seconds with
  overlap protection, detected IDs and status shown below the preview, and
  the existing stop-and-upload video flow remains available.
