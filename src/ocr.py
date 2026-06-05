"""OCR helpers for extracting book title data from crop images."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


TITLE_OCR_PROMPT = """\
この画像は本が入った箱、または本棚の一区画を切り出したものです。
背表紙から読み取れる本のタイトルだけをすべて抽出し、JSON object だけを返してください。

形式:
{
  "books": [
    {
      "title": "書名"
    }
  ]
}

ルール:
- 1冊につき1エントリ
- タイトルが読めない本は除外
- 著者名、出版社、ISBNは抽出しない
- タイトル以外の文字を無理に混ぜない
- 説明文や Markdown は不要。JSON object だけ返す
"""


def strip_json_markdown(text: str) -> str:
    """Remove a surrounding Markdown code fence from a JSON response."""

    text = text.strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1].strip()
            if text.startswith("json"):
                text = text[4:].strip()
    return text


def clean_text(value: Any) -> str | None:
    """Normalize empty OCR values to None."""

    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_book(item: dict[str, Any]) -> dict[str, Any]:
    """Keep the book OCR schema small and stable."""

    return {"title": clean_text(item.get("title"))}


def parse_title_ocr_response(text: str) -> list[dict[str, Any]]:
    """Parse Gemini JSON into a list of title records."""

    raw = strip_json_markdown(text or "")
    data = json.loads(raw)
    books = data.get("books", data if isinstance(data, list) else [])
    normalized = [normalize_book(item) for item in books if isinstance(item, dict)]
    return [book for book in normalized if book.get("title")]


def gemini_title_ocr(
    image_path: Path,
    model: str = "gemini-3.1-flash-lite-preview",
    prompt: str = TITLE_OCR_PROMPT,
) -> list[dict[str, Any]]:
    """Extract visible book titles from one crop image with Gemini."""

    from google import genai
    from google.genai import types

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY 環境変数が未設定です。")

    suffix = image_path.suffix.lower()
    mime = "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/png"
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model,
        contents=[
            types.Part.from_bytes(data=image_path.read_bytes(), mime_type=mime),
            prompt,
        ],
    )
    return parse_title_ocr_response(response.text or "")
