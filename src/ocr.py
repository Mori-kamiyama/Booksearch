"""OCR helpers for extracting book title data from crop images."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


from scan_core import TITLE_OCR_PROMPT, parse_titles


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
    return parse_titles(text)


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
        config=types.GenerateContentConfig(temperature=0),
        contents=[
            types.Part.from_bytes(data=image_path.read_bytes(), mime_type=mime),
            prompt,
        ],
    )
    return parse_title_ocr_response(response.text or "")
