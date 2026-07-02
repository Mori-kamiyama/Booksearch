# Tag Camera Recognition

## Current Direction

The tag placement page now recognizes tags from a browser camera by sending
captured frames to the local Go API.

Flow:

1. `frontend/src/pages/TagPlacementPage.tsx` captures a camera frame as JPEG.
2. `POST /api/tags/detect` receives the frame as multipart `image`.
3. The Go API runs `uv run python scripts/detect_apriltags.py`.
4. OpenCV ArUco/AprilTag detection returns tag IDs and placement metadata.
5. The frontend selects the detected tag and shows the paste location.

## Rationale

AprilTag decoding in plain browser JavaScript would be fragile and slow to
implement from scratch. The project already depends on OpenCV for reliable tag
detection, so the first working version keeps recognition on the backend and
uses the website as the camera UI.

This also keeps dictionary support aligned with the generated print plan:
`data/apriltag_library_map.json` uses `DICT_APRILTAG_36h11`.

## Notes

- Start the local API with an AprilTag map, for example:

```bash
go run . --port 8081 --apriltag-map ../data/apriltag_library_map.json
```

- The frontend can point at that API with:

```bash
VITE_API_BASE_URL=http://127.0.0.1:8081 npm run dev -- --host 127.0.0.1 --port 5174
```

- Relative API flag paths are normalized to absolute paths at startup, because
  the detector subprocess runs from the repository root.
