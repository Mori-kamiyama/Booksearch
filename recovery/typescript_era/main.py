import argparse
import json
import os
from pathlib import Path

import cv2
import moondream as md
import numpy as np
from PIL import Image


MOONDREAM_API_KEY = os.getenv(
    "MOONDREAM_API_KEY",
    "",
)
DEFAULT_PROMPT = "Describe the books visible in this bookshelf section. Include each spine title you can read."
TAG_IDS = (1, 2, 4, 5)
TAG_DICTIONARY = cv2.aruco.DICT_4X4_50


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Aruco 1/2/4/5 で囲まれた本棚区画を補正し、moondream に渡します。"
    )
    parser.add_argument("--image", default="data/add_tag.jpg")
    parser.add_argument("--output-dir", default="outputs/add_tag")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    return parser.parse_args()


def order_points(points: np.ndarray) -> np.ndarray:
    pts = points.astype(np.float32)
    sums = pts.sum(axis=1)
    diffs = np.diff(pts, axis=1)
    ordered = np.zeros((4, 2), dtype=np.float32)
    ordered[0] = pts[np.argmin(sums)]
    ordered[2] = pts[np.argmax(sums)]
    ordered[1] = pts[np.argmin(diffs)]
    ordered[3] = pts[np.argmax(diffs)]
    return ordered


def detect_target_tags(image: np.ndarray) -> dict[int, np.ndarray]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    dictionary = cv2.aruco.getPredefinedDictionary(TAG_DICTIONARY)
    detector = cv2.aruco.ArucoDetector(dictionary)
    corners, ids, _ = detector.detectMarkers(gray)

    if ids is None:
        raise RuntimeError("Aruco タグを検出できませんでした。")

    detected = {
        int(tag_id): tag_corners.reshape(4, 2)
        for tag_corners, tag_id in zip(corners, ids.flatten())
    }
    missing = [tag_id for tag_id in TAG_IDS if tag_id not in detected]
    if missing:
        raise RuntimeError(f"必要なタグが不足しています: {missing}")

    return {tag_id: detected[tag_id] for tag_id in TAG_IDS}


def compute_inner_quad(tags: dict[int, np.ndarray]) -> np.ndarray:
    centers = np.array([corners.mean(axis=0) for corners in tags.values()], dtype=np.float32)
    region_center = centers.mean(axis=0)

    inner_points = []
    for tag_id in TAG_IDS:
        corners = tags[tag_id]
        distances = np.linalg.norm(corners - region_center, axis=1)
        inner_points.append(corners[np.argmin(distances)])

    return order_points(np.array(inner_points, dtype=np.float32))


def warp_from_quad(image: np.ndarray, quad: np.ndarray) -> np.ndarray:
    top_width = np.linalg.norm(quad[1] - quad[0])
    bottom_width = np.linalg.norm(quad[2] - quad[3])
    left_height = np.linalg.norm(quad[3] - quad[0])
    right_height = np.linalg.norm(quad[2] - quad[1])

    width = max(int(round(max(top_width, bottom_width))), 1)
    height = max(int(round(max(left_height, right_height))), 1)
    destination = np.array(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
        dtype=np.float32,
    )
    matrix = cv2.getPerspectiveTransform(quad.astype(np.float32), destination)
    return cv2.warpPerspective(image, matrix, (width, height))


def save_detection_preview(
    image: np.ndarray,
    tags: dict[int, np.ndarray],
    quad: np.ndarray,
    output_path: Path,
) -> None:
    preview = image.copy()
    for tag_id, corners in tags.items():
        pts = corners.astype(np.int32)
        cv2.polylines(preview, [pts], True, (255, 0, 0), 3)
        center = pts.mean(axis=0).astype(int)
        cv2.putText(
            preview,
            str(tag_id),
            tuple(center),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (0, 255, 255),
            2,
            cv2.LINE_AA,
        )

    cv2.polylines(preview, [quad.astype(np.int32)], True, (0, 255, 0), 4)
    cv2.imwrite(str(output_path), preview)


def run_moondream(image_path: Path, prompt: str) -> dict:
    if not MOONDREAM_API_KEY:
        raise RuntimeError("MOONDREAM_API_KEY が未設定です。")

    model = md.vl(api_key=MOONDREAM_API_KEY)
    with Image.open(image_path).convert("RGB") as image:
        return model.query(image, prompt)


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    source_path = Path(args.image)
    image = cv2.imread(str(source_path))
    if image is None:
        raise FileNotFoundError(f"画像を開けませんでした: {source_path}")

    tags = detect_target_tags(image)
    quad = compute_inner_quad(tags)
    warped = warp_from_quad(image, quad)

    preview_path = output_dir / "add_tag_detected.jpg"
    warped_path = output_dir / "add_tag_warped.jpg"
    response_path = output_dir / "moondream_response.json"

    save_detection_preview(image, tags, quad, preview_path)
    cv2.imwrite(str(warped_path), warped)

    response = run_moondream(warped_path, args.prompt)
    response_path.write_text(
        json.dumps(
            {
                "source_image": str(source_path),
                "prompt": args.prompt,
                "tag_ids": list(TAG_IDS),
                "quad_points": quad.round(2).tolist(),
                "warped_image": str(warped_path),
                "response": response,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"preview: {preview_path}")
    print(f"warped: {warped_path}")
    print(f"response_json: {response_path}")
    print("answer:", response.get("answer", ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
