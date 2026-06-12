import argparse
import json
import os
from pathlib import Path

import moondream as md
from PIL import Image


MOONDREAM_API_KEY = os.getenv(
    "MOONDREAM_API_KEY",
    "",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="moondream detect の box を spatial_refs にして segment を試します。"
    )
    parser.add_argument(
        "--response-json",
        default="outputs/add_tag/moondream_response.json",
    )
    parser.add_argument(
        "--output-dir",
        default="outputs/add_tag_segment",
    )
    parser.add_argument(
        "--indices",
        default="9,11,13,15,16",
        help="1-based indices of books to segment",
    )
    parser.add_argument(
        "--object",
        default="book",
    )
    return parser.parse_args()


def load_model() -> md.CloudVL:
    if not MOONDREAM_API_KEY:
        raise RuntimeError("MOONDREAM_API_KEY が未設定です。")
    return md.vl(api_key=MOONDREAM_API_KEY)


def parse_indices(indices_arg: str) -> list[int]:
    return [int(part.strip()) for part in indices_arg.split(",") if part.strip()]


def main() -> int:
    args = parse_args()
    response_path = Path(args.response_json)
    response = json.loads(response_path.read_text(encoding="utf-8"))
    image_path = Path(response["warped_image"])
    if not image_path.is_absolute():
        image_path = response_path.resolve().parent.parent / image_path.relative_to("outputs")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    indices = parse_indices(args.indices)
    model = load_model()

    with Image.open(image_path).convert("RGB") as image:
        book_objects = response.get("book_objects", [])
        summaries = []
        for index in indices:
            if index < 1 or index > len(book_objects):
                continue
            box = book_objects[index - 1]
            spatial_ref = [
                box["x_min"],
                box["y_min"],
                box["x_max"],
                box["y_max"],
            ]
            result = model.segment(
                image,
                args.object,
                spatial_refs=[spatial_ref],
            )
            payload = {
                "index": index,
                "detect_box": box,
                "segment_result": result,
            }
            out_path = output_dir / f"book_{index:02d}_segment.json"
            out_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            summaries.append(
                {
                    "index": index,
                    "detect_box": box,
                    "segment_bbox": result.get("bbox"),
                    "path_prefix": result.get("path", "")[:160],
                    "output_json": str(out_path),
                }
            )
            print(
                f"book_{index:02d}: detect={box} segment_bbox={result.get('bbox')}"
            )

    summary_path = output_dir / "summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "image": str(image_path),
                "object": args.object,
                "indices": indices,
                "results": summaries,
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
