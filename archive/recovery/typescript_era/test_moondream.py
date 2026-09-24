import argparse
import os
import sys
from typing import Iterable

import cv2
import numpy as np
from PIL import Image, ImageDraw
import moondream as md


MOONDREAM_API_KEY = os.getenv(
    "MOONDREAM_API_KEY",
    "",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="箱を検知し、台形補正した画像から本を再検知します。"
    )
    parser.add_argument("image_path", nargs="?", default="book_spines.jpg")
    parser.add_argument("--box-prompt", default="box")
    parser.add_argument("--book-prompt", default="book")
    parser.add_argument(
        "--box-index",
        type=int,
        default=0,
        help="箱検知結果の何番目を使うか。0 が最大矩形です。",
    )
    parser.add_argument(
        "--padding",
        type=float,
        default=0.03,
        help="箱領域の周囲に足す余白率。",
    )
    return parser.parse_args()


def pixel_box(obj: dict, width: int, height: int) -> tuple[int, int, int, int]:
    x_min = int(obj["x_min"] * width)
    y_min = int(obj["y_min"] * height)
    x_max = int(obj["x_max"] * width)
    y_max = int(obj["y_max"] * height)
    return x_min, y_min, x_max, y_max


def box_area(obj: dict) -> float:
    return max(0.0, obj["x_max"] - obj["x_min"]) * max(0.0, obj["y_max"] - obj["y_min"])


def draw_boxes(
    image: Image.Image,
    objects: list[dict],
    output_path: str,
    highlight_index: int | None = None,
) -> None:
    annotated = image.copy()
    draw = ImageDraw.Draw(annotated)
    width, height = annotated.size

    for index, obj in enumerate(objects, start=1):
        x_min, y_min, x_max, y_max = pixel_box(obj, width, height)
        color = "lime" if highlight_index == index - 1 else "red"
        draw.rectangle((x_min, y_min, x_max, y_max), outline=color, width=4)
        draw.text((x_min + 6, max(0, y_min - 18)), str(index), fill="yellow")

    annotated.save(output_path)


def expand_box(
    box: tuple[int, int, int, int],
    image_size: tuple[int, int],
    padding_ratio: float,
) -> tuple[int, int, int, int]:
    x_min, y_min, x_max, y_max = box
    width, height = image_size
    pad_x = int((x_max - x_min) * padding_ratio)
    pad_y = int((y_max - y_min) * padding_ratio)
    return (
        max(0, x_min - pad_x),
        max(0, y_min - pad_y),
        min(width, x_max + pad_x),
        min(height, y_max + pad_y),
    )


def pil_to_cv(image: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)


def cv_to_pil(image: np.ndarray) -> Image.Image:
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)


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


def contour_candidates(contours: Iterable[np.ndarray]) -> list[np.ndarray]:
    quads: list[np.ndarray] = []
    for contour in contours:
        perimeter = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
        if len(approx) == 4 and cv2.contourArea(approx) > 1_000:
            quads.append(approx.reshape(4, 2))
    return quads


def detect_box_quad(crop: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 50, 150)
    edges = cv2.dilate(edges, np.ones((3, 3), dtype=np.uint8), iterations=2)

    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    quads = contour_candidates(contours)
    if quads:
        best = max(quads, key=cv2.contourArea)
        return order_points(best)

    height, width = crop.shape[:2]
    fallback = np.array(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
        dtype=np.float32,
    )
    return order_points(fallback)


def square_perspective_warp(crop: np.ndarray, quad: np.ndarray) -> np.ndarray:
    top_width = np.linalg.norm(quad[1] - quad[0])
    bottom_width = np.linalg.norm(quad[2] - quad[3])
    left_height = np.linalg.norm(quad[3] - quad[0])
    right_height = np.linalg.norm(quad[2] - quad[1])

    side = max(int(max(top_width, bottom_width, left_height, right_height)), 1)
    destination = np.array(
        [[0, 0], [side - 1, 0], [side - 1, side - 1], [0, side - 1]],
        dtype=np.float32,
    )
    matrix = cv2.getPerspectiveTransform(quad.astype(np.float32), destination)
    return cv2.warpPerspective(crop, matrix, (side, side))


def save_quad_preview(crop: np.ndarray, quad: np.ndarray, output_path: str) -> None:
    preview = crop.copy()
    cv2.polylines(preview, [quad.astype(np.int32)], True, (0, 255, 0), 5)
    cv2.imwrite(output_path, preview)


def main() -> int:
    args = parse_args()
    api_key = MOONDREAM_API_KEY
    if not api_key:
        print(
            "MOONDREAM_API_KEY が未設定です。 例: export MOONDREAM_API_KEY='your-key'",
            file=sys.stderr,
        )
        return 1

    model = md.vl(api_key=api_key)
    image = Image.open(args.image_path).convert("RGB")
    width, height = image.size
    stem, _ = os.path.splitext(args.image_path)

    box_result = model.detect(image, args.box_prompt)
    box_objects = sorted(box_result["objects"], key=box_area, reverse=True)
    if not box_objects:
        print(f"箱が見つかりませんでした: prompt={args.box_prompt}", file=sys.stderr)
        return 1

    if args.box_index < 0 or args.box_index >= len(box_objects):
        print(
            f"--box-index={args.box_index} は範囲外です。0 から {len(box_objects) - 1} を指定してください。",
            file=sys.stderr,
        )
        return 1

    draw_boxes(
        image,
        box_objects,
        f"{stem}_box_annotated.jpg",
        highlight_index=args.box_index,
    )

    selected_box = pixel_box(box_objects[args.box_index], width, height)
    expanded_box = expand_box(selected_box, image.size, args.padding)
    crop = image.crop(expanded_box)
    crop.save(f"{stem}_box_crop.jpg")

    crop_cv = pil_to_cv(crop)
    quad = detect_box_quad(crop_cv)
    save_quad_preview(crop_cv, quad, f"{stem}_box_quad.jpg")

    square_cv = square_perspective_warp(crop_cv, quad)
    square_image = cv_to_pil(square_cv)
    square_path = f"{stem}_box_square.jpg"
    square_image.save(square_path)

    book_result = model.detect(square_image, args.book_prompt)
    books_output_path = f"{stem}_books_annotated.jpg"
    draw_boxes(square_image, book_result["objects"], books_output_path)

    print("box_prompt:", args.box_prompt)
    print("box_objects:", box_objects)
    print("selected_box_index:", args.box_index)
    print("selected_box_pixels:", selected_box)
    print("expanded_box_pixels:", expanded_box)
    print("quad_points:", quad.astype(int).tolist())
    print("book_prompt:", args.book_prompt)
    print("book_objects:", book_result["objects"])
    print("box_annotated:", f"{stem}_box_annotated.jpg")
    print("box_crop:", f"{stem}_box_crop.jpg")
    print("box_quad_preview:", f"{stem}_box_quad.jpg")
    print("box_square:", square_path)
    print("books_annotated:", books_output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
