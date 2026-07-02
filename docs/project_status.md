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
