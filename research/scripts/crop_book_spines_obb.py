"""Create perspective-corrected, one-book crops with a YOLO OBB model.

The manifest preserves every source-image occurrence. Near-identical spine
crops in neighboring photos share one canonical OCR image, which avoids paying
for the same book repeatedly while retaining image-to-book provenance.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from ultralytics import YOLO


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


@dataclass
class Fingerprint:
    canonical_index: int
    source_index: int
    phash: int
    aspect: float
    hsv_hist: np.ndarray


def order_points(points: np.ndarray) -> np.ndarray:
    points = points.astype(np.float32)
    ordered = np.zeros((4, 2), dtype=np.float32)
    sums = points.sum(axis=1)
    diffs = np.diff(points, axis=1).reshape(-1)
    ordered[0] = points[np.argmin(sums)]  # top-left
    ordered[2] = points[np.argmax(sums)]  # bottom-right
    ordered[1] = points[np.argmin(diffs)]  # top-right
    ordered[3] = points[np.argmax(diffs)]  # bottom-left
    return ordered


def rectify(image: np.ndarray, polygon: np.ndarray, pad_ratio: float) -> np.ndarray | None:
    tl, tr, br, bl = order_points(polygon)
    width = int(round(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl))))
    height = int(round(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl))))
    if width < 2 or height < 2:
        return None
    target = np.array(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
        dtype=np.float32,
    )
    matrix = cv2.getPerspectiveTransform(np.array([tl, tr, br, bl]), target)
    crop = cv2.warpPerspective(
        image,
        matrix,
        (width, height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )
    if crop.shape[1] > crop.shape[0]:
        crop = cv2.rotate(crop, cv2.ROTATE_90_CLOCKWISE)
    if pad_ratio > 0:
        pad = max(2, int(round(crop.shape[1] * pad_ratio)))
        crop = cv2.copyMakeBorder(crop, pad, pad, pad, pad, cv2.BORDER_REPLICATE)
    return crop


def phash(image: np.ndarray) -> int:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    normalized = cv2.resize(gray, (32, 128), interpolation=cv2.INTER_AREA)
    compact = cv2.resize(normalized, (32, 32), interpolation=cv2.INTER_AREA)
    coeff = cv2.dct(np.float32(compact))[:8, :8].reshape(-1)[1:]
    median = float(np.median(coeff))
    value = 0
    for item in coeff:
        value = (value << 1) | int(item > median)
    return value


def hsv_hist(image: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(cv2.resize(image, (64, 128)), cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [12, 8], [0, 180, 0, 256])
    cv2.normalize(hist, hist)
    return hist


def duplicate_of(
    crop: np.ndarray,
    source_index: int,
    fingerprints: list[Fingerprint],
    window: int,
    max_hamming: int,
) -> int | None:
    crop_hash = phash(crop)
    aspect = crop.shape[0] / max(1, crop.shape[1])
    histogram = hsv_hist(crop)
    for item in reversed(fingerprints):
        if source_index - item.source_index > window:
            break
        if abs(math.log(max(aspect, 0.01) / max(item.aspect, 0.01))) > 0.25:
            continue
        if (crop_hash ^ item.phash).bit_count() > max_hamming:
            continue
        if cv2.compareHist(histogram, item.hsv_hist, cv2.HISTCMP_CORREL) < 0.88:
            continue
        return item.canonical_index
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--conf", type=float, default=0.5)
    parser.add_argument("--device", default="mps")
    parser.add_argument("--pad-ratio", type=float, default=0.04)
    parser.add_argument("--min-short-edge", type=int, default=12)
    parser.add_argument("--min-long-edge", type=int, default=80)
    parser.add_argument("--dedup-window", type=int, default=12)
    parser.add_argument("--dedup-hamming", type=int, default=4)
    parser.add_argument("--max-images", type=int)
    args = parser.parse_args()

    images = sorted(
        path for path in args.source.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    )
    if args.max_images is not None:
        images = images[: args.max_images]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    crop_dir = args.output_dir / "crops"
    crop_dir.mkdir(exist_ok=True)

    model = YOLO(str(args.model))
    results = model.predict(
        source=[str(path) for path in images],
        imgsz=args.imgsz,
        conf=args.conf,
        device=args.device,
        verbose=False,
        stream=True,
    )

    canonical: list[dict[str, Any]] = []
    fingerprints: list[Fingerprint] = []
    detections = 0
    rejected = 0
    duplicates = 0
    for source_index, (image_path, result) in enumerate(zip(images, results)):
        image = cv2.imread(str(image_path))
        if image is None or result.obb is None:
            continue
        candidates = list(zip(result.obb.xyxyxyxy.cpu().numpy(), result.obb.conf.cpu().tolist()))
        candidates.sort(key=lambda item: (float(item[0][:, 0].mean()), float(item[0][:, 1].mean())))
        for local_index, (polygon, confidence) in enumerate(candidates, 1):
            detections += 1
            crop = rectify(image, polygon, args.pad_ratio)
            if crop is None or min(crop.shape[:2]) < args.min_short_edge or max(crop.shape[:2]) < args.min_long_edge:
                rejected += 1
                continue
            appearance = {
                "source_image": str(image_path.resolve()),
                "source_index": source_index,
                "detector_confidence": round(float(confidence), 6),
                "polygon_xy": [[round(float(x), 2), round(float(y), 2)] for x, y in polygon],
            }
            duplicate_index = duplicate_of(
                crop,
                source_index,
                fingerprints,
                args.dedup_window,
                args.dedup_hamming,
            )
            if duplicate_index is not None:
                canonical[duplicate_index]["appearances"].append(appearance)
                duplicates += 1
                continue

            crop_name = f"spine_{len(canonical) + 1:06d}.jpg"
            crop_path = crop_dir / crop_name
            cv2.imwrite(str(crop_path), crop, [int(cv2.IMWRITE_JPEG_QUALITY), 94])
            entry = {
                "box_id": f"spine_{len(canonical) + 1:06d}",
                "source_image": appearance["source_image"],
                "crop_image": str(crop_path.resolve()),
                "detector_confidence": appearance["detector_confidence"],
                "polygon_xy": appearance["polygon_xy"],
                "appearances": [appearance],
            }
            canonical.append(entry)
            fingerprints.append(
                Fingerprint(
                    canonical_index=len(canonical) - 1,
                    source_index=source_index,
                    phash=phash(crop),
                    aspect=crop.shape[0] / max(1, crop.shape[1]),
                    hsv_hist=hsv_hist(crop),
                )
            )
        print(
            f"[{source_index + 1}/{len(images)}] {image_path.name} "
            f"detections={len(candidates)} canonical={len(canonical)} duplicates={duplicates}",
            flush=True,
        )

    manifest = {
        "source": str(args.source.resolve()),
        "detector_model": str(args.model.resolve()),
        "detector": {"imgsz": args.imgsz, "conf": args.conf},
        "dedup": {
            "window": args.dedup_window,
            "phash_max_hamming": args.dedup_hamming,
            "hsv_correlation_min": 0.88,
        },
        "stats": {
            "images": len(images),
            "detections": detections,
            "rejected_small": rejected,
            "duplicates": duplicates,
            "canonical_crops": len(canonical),
        },
        "entries": canonical,
    }
    manifest_path = args.output_dir / "catalog.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest["stats"], ensure_ascii=False))
    print(f"catalog={manifest_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
