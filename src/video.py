"""Video frame extraction utilities."""

from __future__ import annotations

from pathlib import Path


def extract_frames(
    video_path: Path,
    output_dir: Path,
    interval_sec: float = 2.0,
    max_frames: int | None = None,
) -> list[Path]:
    """Extract frames from a video at fixed time intervals.

    Returns a list of saved JPEG paths, one per sampled frame.
    """
    import cv2

    output_dir.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError(f"Cannot open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_interval = max(1, int(fps * interval_sec))

    saved: list[Path] = []
    frame_idx = 0

    while True:
        ok = cap.grab()
        if not ok:
            break

        if frame_idx % frame_interval == 0:
            ok, frame = cap.retrieve()
            if not ok or frame is None:
                frame_idx += 1
                continue

            out_path = output_dir / f"frame_{frame_idx:06d}.jpg"
            cv2.imwrite(str(out_path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
            saved.append(out_path)

            if max_frames is not None and len(saved) >= max_frames:
                break

        frame_idx += 1

    cap.release()
    return saved
