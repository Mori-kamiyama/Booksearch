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
