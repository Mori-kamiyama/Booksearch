"""
Gemini で warped 画像から本のタイトルを直接抽出する。

使い方:
  python run_gemini.py
  python run_gemini.py --image outputs/add_tag/add_tag_warped.jpg
  python run_gemini.py --model gemini-2.5-flash-preview-04-17
"""

import argparse
import json
import os
from pathlib import Path

from google import genai
from google.genai import types


REPO_ROOT = Path(__file__).resolve().parent.parent

PROMPT = """\
この画像は本棚の一区画です。
写っているすべての本の背表紙タイトルを読み取り、JSON 配列として返してください。

ルール:
- 見えているタイトルをすべて列挙する（部分的にしか見えないものも含む）
- 1冊につき1つのエントリ
- タイトルのみ（著者名・出版社は除く）
- 読み取れない本は除外
- 余計な説明は不要。JSON 配列だけ返すこと

出力例:
["タイトルA", "タイトルB", "タイトルC"]
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gemini で本棚画像から書名を抽出します。")
    parser.add_argument(
        "--image",
        default="outputs/add_tag/add_tag_warped.jpg",
        help="入力画像パス（デフォルト: outputs/add_tag/add_tag_warped.jpg）",
    )
    parser.add_argument(
        "--model",
        default="gemini-3.1-flash-lite-preview",
        help="使用する Gemini モデル",
    )
    parser.add_argument(
        "--output-dir",
        default="outputs/add_tag_gemini",
        help="結果の出力先ディレクトリ",
    )
    return parser.parse_args()


def extract_titles(model: str, image_path: Path, prompt: str) -> list[str]:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY 環境変数が未設定です。")

    client = genai.Client(api_key=api_key)

    image_bytes = image_path.read_bytes()
    suffix = image_path.suffix.lower()
    mime = "image/jpeg" if suffix in (".jpg", ".jpeg") else "image/png"

    response = client.models.generate_content(
        model=model,
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type=mime),
            prompt,
        ],
    )

    text = response.text.strip()
    # コードブロックを除去
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    return json.loads(text)


def main() -> int:
    args = parse_args()
    image_path = REPO_ROOT / args.image
    output_dir = REPO_ROOT / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"画像: {image_path}")
    print(f"モデル: {args.model}")

    titles = extract_titles(args.model, image_path, PROMPT)

    print(f"\n検出された書名: {len(titles)}件")
    for i, t in enumerate(titles, 1):
        print(f"  {i:2d}. {t}")

    result = {
        "image": str(image_path),
        "model": args.model,
        "titles": titles,
    }
    out_path = output_dir / "summary.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nsummary_json: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
