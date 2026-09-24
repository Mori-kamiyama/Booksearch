# Tag Camera Lab

Minimal camera recognition test for Booksearch AprilTags.

Run:

```bash
uv run python tag-camera-lab/server.py
```

Open:

```text
http://127.0.0.1:5199/
```

This lab intentionally avoids the main React app and Go backend. It serves one
HTML file and one Python API:

- `GET /` for the camera test UI
- `GET /test-tag.png` for a generated `tag 0` marker
- `POST /api/detect` for OpenCV AprilTag detection

Use the upload control first with `/test-tag.png` or a photo. If upload works
but live camera does not, the problem is likely camera focus, size, lighting,
permission, or capture timing rather than the detector.
