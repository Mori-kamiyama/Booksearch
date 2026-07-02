"""Video frame extraction utilities."""

from __future__ import annotations

from pathlib import Path
from typing import Any

DEFAULT_HASH_SIZE = 8
DEFAULT_MAX_HAMMING_DISTANCE = 4


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


def crop_phash(image: Any, hash_size: int = DEFAULT_HASH_SIZE) -> int:
    """Compute a simple average-hash (aHash) for a crop image.

    `image` is an OpenCV BGR ndarray (as returned by cv2.imread). The hash is
    a `hash_size * hash_size`-bit integer where each bit marks whether the
    corresponding downsampled pixel is brighter than the image's mean.
    """
    import cv2

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    resized = cv2.resize(gray, (hash_size, hash_size), interpolation=cv2.INTER_AREA)
    avg = resized.mean()

    hash_value = 0
    for pixel in resized.flatten():
        hash_value = (hash_value << 1) | int(pixel > avg)
    return hash_value


def is_duplicate_crop(
    phash: int,
    seen_hashes: list[int],
    max_hamming_distance: int = DEFAULT_MAX_HAMMING_DISTANCE,
) -> bool:
    """Check whether `phash` is a near-duplicate of any hash already seen.

    Used in video mode to skip re-OCRing a book box that already appeared in
    an earlier frame. The Hamming-distance threshold is kept small so that
    only near-identical crops (same book, same framing) count as duplicates.
    """
    for seen in seen_hashes:
        distance = bin(phash ^ seen).count("1")
        if distance <= max_hamming_distance:
            return True
    return False
