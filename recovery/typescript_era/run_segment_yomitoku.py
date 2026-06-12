import argparse
import json
import subprocess
from io import BytesIO
from pathlib import Path

import cairosvg
from PIL import Image


REPO_ROOT = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="moondream segment の path をマスク化し、masked crop に yomitoku をかけます。"
    )
    parser.add_argument(
        "--segment-summary",
        default="outputs/add_tag_segment/summary.json",
    )
    parser.add_argument(
        "--output-dir",
        default="outputs/add_tag_segment_yomitoku",
    )
    parser.add_argument(
        "--white-background",
        action="store_true",
        help="透明部分を白背景で埋めて保存します。",
    )
    return parser.parse_args()


def clamp(value: int, lower: int, upper: int) -> int:
    return max(lower, min(value, upper))


def bbox_to_pixels(bbox: dict, width: int, height: int) -> tuple[int, int, int, int]:
    left = clamp(int(bbox["x_min"] * width), 0, width - 1)
    top = clamp(int(bbox["y_min"] * height), 0, height - 1)
    right = clamp(int(bbox["x_max"] * width), left + 1, width)
    bottom = clamp(int(bbox["y_max"] * height), top + 1, height)
    return left, top, right, bottom


def make_mask(path_d: str, width: int, height: int) -> Image.Image:
    svg = f"""
    <svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 1 1">
      <path d="{path_d}" fill="white" stroke="none" />
    </svg>
    """
    png_bytes = cairosvg.svg2png(bytestring=svg.encode("utf-8"), output_width=width, output_height=height)
    return Image.open(BytesIO(png_bytes)).convert("L")


def run_yomitoku(image_path: Path, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(REPO_ROOT / ".venv/bin/python"),
        "-m",
        "yomitoku.cli.main",
        str(image_path),
        "-f",
        "json",
        "-o",
        str(output_dir),
        "-d",
        "cpu",
        "-l",
    ]
    subprocess.run(cmd, check=True)
    return output_dir / f"{image_path.parent.name}_{image_path.stem}_p1.json"


def extract_strings(result_json_path: Path) -> list[str]:
    data = json.loads(result_json_path.read_text(encoding="utf-8"))
    strings: list[str] = []
    for paragraph in data.get("paragraphs", []):
        text = paragraph.get("contents", "").strip()
        if text:
            strings.append(text)
    for figure in data.get("figures", []):
        for paragraph in figure.get("paragraphs", []):
            text = paragraph.get("contents", "").strip()
            if text:
                strings.append(text)
    if strings:
        return strings
    return [w.get("content", "").strip() for w in data.get("words", []) if w.get("content", "").strip()]


def main() -> int:
    args = parse_args()
    summary_path = Path(args.segment_summary)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    image_path = Path(summary["image"])
    output_dir = Path(args.output_dir)
    masks_dir = output_dir / "masks"
    crops_dir = output_dir / "masked_crops"
    yomitoku_dir = output_dir / "yomitoku"
    output_dir.mkdir(parents=True, exist_ok=True)
    masks_dir.mkdir(parents=True, exist_ok=True)
    crops_dir.mkdir(parents=True, exist_ok=True)
    yomitoku_dir.mkdir(parents=True, exist_ok=True)

    with Image.open(image_path).convert("RGBA") as image:
        image_width, image_height = image.size
        books = []
        for item in summary["results"]:
            index = item["index"]
            raw_segment_path = Path(item["output_json"])
            segment_data = json.loads(raw_segment_path.read_text(encoding="utf-8"))
            segment_result = segment_data["segment_result"]
            bbox = segment_result["bbox"]
            path_d = segment_result["path"]

            left, top, right, bottom = bbox_to_pixels(bbox, image_width, image_height)
            crop = image.crop((left, top, right, bottom))
            mask = make_mask(path_d, crop.width, crop.height)
            mask_path = masks_dir / f"book_{index:02d}_mask.png"
            mask.save(mask_path)

            transparent = Image.new("RGBA", crop.size, (255, 255, 255, 0))
            masked = Image.composite(crop, transparent, mask)
            if args.white_background:
                bg = Image.new("RGBA", crop.size, (255, 255, 255, 255))
                masked = Image.alpha_composite(bg, masked)

            masked_crop_path = crops_dir / f"book_{index:02d}_masked.png"
            masked.save(masked_crop_path)

            book_output_dir = yomitoku_dir / f"book_{index:02d}"
            yomitoku_json = run_yomitoku(masked_crop_path, book_output_dir)
            strings = extract_strings(yomitoku_json)

            books.append(
                {
                    "index": index,
                    "segment_bbox": bbox,
                    "mask_image": str(mask_path),
                    "masked_crop": str(masked_crop_path),
                    "yomitoku_json": str(yomitoku_json),
                    "strings": strings,
                }
            )
            print(f"book_{index:02d}: {strings[:5]}")

    result_path = output_dir / "summary.json"
    result_path.write_text(
        json.dumps(
            {
                "segment_summary": str(summary_path),
                "image": str(image_path),
                "books": books,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"summary_json: {result_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
