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


def extract_keyframes(
    video_path: Path,
    output_dir: Path,
    min_frame_blur: float = 80.0,
    scene_change_threshold: float = 25.0,
    min_interval_sec: float = 0.5,
    max_frames: int | None = None,
) -> list[Path]:
    """Extract keyframes from a video with adaptive filtering.

    Skips:
    - Blurry frames (Laplacian variance < min_frame_blur) → 手ぶれ対策
    - Frames too similar to the last accepted frame (mean pixel diff < scene_change_threshold)
    - Frames within min_interval_sec of the last accepted frame

    Returns a list of saved JPEG paths.
    """
    import cv2
    import numpy as np

    output_dir.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError(f"Cannot open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    min_interval_frames = max(1, int(fps * min_interval_sec))

    saved: list[Path] = []
    prev_gray: "np.ndarray | None" = None
    last_saved_idx = -min_interval_frames  # 最初のフレームを候補にする
    frame_idx = 0
    stats = {"blur_skip": 0, "similar_skip": 0, "interval_skip": 0, "saved": 0}

    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            break

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # 1. ブレチェック（手ぶれ）
        blur_score = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        if blur_score < min_frame_blur:
            stats["blur_skip"] += 1
            frame_idx += 1
            continue

        # 2. 最小間隔チェック
        if frame_idx - last_saved_idx < min_interval_frames:
            stats["interval_skip"] += 1
            frame_idx += 1
            continue

        # 3. シーン変化チェック（前の採用フレームと比較）
        if prev_gray is not None:
            diff = float(np.mean(np.abs(gray.astype(np.float32) - prev_gray.astype(np.float32))))
            if diff < scene_change_threshold:
                stats["similar_skip"] += 1
                frame_idx += 1
                continue

        out_path = output_dir / f"frame_{frame_idx:06d}.jpg"
        cv2.imwrite(str(out_path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
        saved.append(out_path)
        prev_gray = gray
        last_saved_idx = frame_idx
        stats["saved"] += 1

        if max_frames is not None and len(saved) >= max_frames:
            break

        frame_idx += 1

    cap.release()
    print(
        f"  keyframes: saved={stats['saved']} "
        f"blur_skip={stats['blur_skip']} "
        f"similar_skip={stats['similar_skip']} "
        f"interval_skip={stats['interval_skip']}"
    )
    return saved


def crop_phash(image_bgr: "object") -> "object":
    """8x8 grayscale thumbnail as float32 array for crop similarity comparison."""
    import cv2
    import numpy as np

    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (8, 8), interpolation=cv2.INTER_AREA)
    return small.astype(np.float32).flatten()


def is_duplicate_crop(phash: "object", seen: list, threshold: float = 12.0) -> bool:
    """Return True if phash is close to any hash in seen (same crop content)."""
    import numpy as np

    for h in seen:
        if float(np.mean(np.abs(phash - h))) < threshold:
            return True
    return False
