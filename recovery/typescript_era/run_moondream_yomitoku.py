import argparse
import json
import subprocess
from pathlib import Path

from PIL import Image


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="moondream の本ボックスで切り出し、各 crop に yomitoku を個別実行します。"
    )
    parser.add_argument(
        "--response-json",
        default="outputs/add_tag/moondream_response.json",
        help="main.py が出力した moondream_response.json",
    )
    parser.add_argument(
        "--output-dir",
        default="outputs/add_tag_moondream_yomitoku",
        help="crop と OCR 結果の出力先",
    )
    parser.add_argument(
        "--padding",
        type=float,
        default=0.02,
        help="各 box の上下左右に足す比率 padding",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="先頭から何冊試すか。0 なら全件",
    )
    return parser.parse_args()


def load_response(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def clamp(value: int, lower: int, upper: int) -> int:
    return max(lower, min(value, upper))


def crop_box(box: dict, width: int, height: int, padding: float) -> tuple[int, int, int, int]:
    x_min = int(box["x_min"] * width)
    y_min = int(box["y_min"] * height)
    x_max = int(box["x_max"] * width)
    y_max = int(box["y_max"] * height)

    pad_x = int((x_max - x_min) * padding)
    pad_y = int((y_max - y_min) * padding)

    left = clamp(x_min - pad_x, 0, width - 1)
    top = clamp(y_min - pad_y, 0, height - 1)
    right = clamp(x_max + pad_x, left + 1, width)
    bottom = clamp(y_max + pad_y, top + 1, height)
    return left, top, right, bottom


def extract_strings(result_json_path: Path) -> list[str]:
    if not result_json_path.exists():
        return []

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

    words = [word.get("content", "").strip() for word in data.get("words", [])]
    return [word for word in words if word]


def run_yomitoku(image_path: Path, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(Path(".venv/bin/python").resolve()),
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
    return output_dir / f"{image_path.stem}_p1.json"


def main() -> int:
    args = parse_args()
    response_path = Path(args.response_json)
    response = load_response(response_path)

    warped_image_path = Path(response["warped_image"])
    if not warped_image_path.is_absolute():
        warped_image_path = Path.cwd() / warped_image_path

    output_dir = Path(args.output_dir)
    crops_dir = output_dir / "crops"
    yomitoku_dir = output_dir / "yomitoku"
    output_dir.mkdir(parents=True, exist_ok=True)
    crops_dir.mkdir(parents=True, exist_ok=True)
    yomitoku_dir.mkdir(parents=True, exist_ok=True)

    summaries = []
    with Image.open(warped_image_path).convert("RGB") as image:
        width, height = image.size
        book_objects = response.get("book_objects", [])
        if args.limit > 0:
            book_objects = book_objects[: args.limit]

        for index, box in enumerate(book_objects, start=1):
            left, top, right, bottom = crop_box(box, width, height, args.padding)
            crop = image.crop((left, top, right, bottom))
            crop_path = crops_dir / f"book_{index:02d}.jpg"
            crop.save(crop_path)

            book_output_dir = yomitoku_dir / f"book_{index:02d}"
            result_json_path = run_yomitoku(crop_path, book_output_dir)
            strings = extract_strings(result_json_path)

            summaries.append(
                {
                    "index": index,
                    "crop_image": str(crop_path),
                    "yomitoku_json": str(result_json_path),
                    "box": {
                        "left": left,
                        "top": top,
                        "right": right,
                        "bottom": bottom,
                    },
                    "strings": strings,
                }
            )
            print(f"book_{index:02d}: {strings[:3]}")

    summary_path = output_dir / "summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "response_json": str(response_path),
                "warped_image": str(warped_image_path),
                "padding": args.padding,
                "books": summaries,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"summary_json: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
