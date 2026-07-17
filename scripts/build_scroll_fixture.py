"""Build a synthetic shelf-traversal video from sequential still images.

The source photos in data-260702 are portrait frames from a shelf walk. A
single ultra-wide image would make the books and AprilTags too small for the
detector, so this script creates both a panorama for inspection and a video
viewport that pans across it.

Usage:
  uv run python scripts/build_scroll_fixture.py
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import cv2
import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=REPO_ROOT / "data-260702")
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "outputs/e2e/data260702_scroll")
    parser.add_argument("--height", type=int, default=1440)
    parser.add_argument("--viewport-width", type=int, default=1280)
    parser.add_argument("--fps", type=int, default=12)
    parser.add_argument("--step", type=int, default=40)
    return parser.parse_args()


def load_images(source: Path, height: int) -> list[np.ndarray]:
    images = []
    for path in sorted(source.glob("*.jpg")):
        image = cv2.imread(str(path))
        if image is None:
            continue
        scale = height / image.shape[0]
        width = max(1, round(image.shape[1] * scale))
        images.append(cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA))
    if not images:
        raise RuntimeError(f"no readable JPEG images in {source}")
    return images


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    images = load_images(args.source, args.height)
    panorama = np.concatenate(images, axis=1)
    panorama_path = args.output_dir / "panorama.jpg"
    cv2.imwrite(str(panorama_path), panorama, [int(cv2.IMWRITE_JPEG_QUALITY), 90])

    width = min(args.viewport_width, panorama.shape[1])
    height = panorama.shape[0]
    video_path = args.output_dir / "scroll.mp4"
    ffmpeg = subprocess.Popen(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{width}x{height}", "-r", str(args.fps), "-i", "-",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video_path),
        ],
        stdin=subprocess.PIPE,
    )
    assert ffmpeg.stdin is not None

    max_x = panorama.shape[1] - width
    positions = list(range(0, max_x + 1, max(1, args.step)))
    if not positions or positions[-1] != max_x:
        positions.append(max_x)
    for x in positions + list(reversed(positions[:-1])):
        ffmpeg.stdin.write(panorama[:, x:x + width].tobytes())
    ffmpeg.stdin.close()
    if ffmpeg.wait() != 0:
        raise RuntimeError(f"ffmpeg failed to write {video_path}")

    print(f"source images: {len(images)}")
    print(f"panorama    : {panorama_path}")
    print(f"scroll video: {video_path}")
    print(f"video size  : {width}x{height}, frames={len(positions) * 2 - 1}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
