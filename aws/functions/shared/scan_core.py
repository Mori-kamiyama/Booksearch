"""Recognition policy shared by local experiments and packaged AWS workers.

Keep cloud delivery, leases, and cache ownership in the adapters.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any

POLICY_VERSION = "scan-v2"
MIN_TRUSTED_SUBSTRING_LEN = 4
MIN_SHORT_EDGE_RATIO = 0.28
MIN_SHORT_EDGE_PX = 80
MIN_BLUR_SCORE = 50.0


def normalize_text(value: Any) -> str:
    """Normalize Japanese book metadata for fuzzy matching."""

    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).lower()
    return re.sub(
        r"[\s　・:：,，.．。『』「」\"'“”‘’!?！？\-‐‑‒–—―（）()【】\[\]]+", "", text
    )


def normalize_isbn(value: Any) -> str:
    """Keep only ISBN digits and X."""

    if value is None:
        return ""
    return re.sub(r"[^0-9xX]", "", str(value)).upper()


def score_text(query_norm: str, value_norm: str) -> float:
    """Score normalized text with exact, substring, then fuzzy matching.

    Substring containment only gets a high score when the shared fragment is
    at least MIN_TRUSTED_SUBSTRING_LEN characters; a 1-2 character OCR
    fragment coincidentally appearing inside an unrelated long title
    otherwise scored 0.7+, which let garbled OCR text auto-match wrong books.
    """

    if not query_norm or not value_norm:
        return 0.0
    if query_norm == value_norm:
        return 1.0
    if query_norm in value_norm and len(query_norm) >= MIN_TRUSTED_SUBSTRING_LEN:
        return min(0.98, 0.7 + len(query_norm) / len(value_norm) * 0.25)
    if value_norm in query_norm and len(value_norm) >= MIN_TRUSTED_SUBSTRING_LEN:
        if len(value_norm) >= 6:
            return min(0.96, 0.82 + len(value_norm) / len(query_norm) * 0.15)
        return min(0.94, 0.65 + len(value_norm) / len(query_norm) * 0.25)
    return SequenceMatcher(None, query_norm, value_norm).ratio()


def assess_quality(
    crop,
    box,
    image_size,
    *,
    min_blur_score=MIN_BLUR_SCORE,
    min_short_edge=None,
    min_short_edge_ratio=MIN_SHORT_EDGE_RATIO,
    reject_edge_touch=False,
    reject_edge_aspect=True,
    edge_wide_aspect=1.45,
    edge_tall_aspect=0.45,
):
    import cv2

    width, height = image_size
    x1, y1, x2, y2 = box
    h, w = crop.shape[:2]
    short_edge = min(w, h)
    aspect = w / h if h else 0.0
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    minimum = (
        min_short_edge
        if min_short_edge is not None
        else max(MIN_SHORT_EDGE_PX, int(min(width, height) * min_short_edge_ratio))
    )
    mx, my = max(2, int(width * 0.005)), max(2, int(height * 0.005))
    edges = [
        name
        for name, touches in (
            ("left", x1 <= mx),
            ("top", y1 <= my),
            ("right", x2 >= width - mx),
            ("bottom", y2 >= height - my),
        )
        if touches
    ]
    reasons = []
    if blur < min_blur_score:
        reasons.append("blurry")
    if short_edge < minimum:
        reasons.append("too_small")
    if reject_edge_touch and edges:
        reasons.append("edge_touch")
    if reject_edge_aspect and edges:
        if aspect >= edge_wide_aspect:
            reasons.append("edge_wide")
        elif aspect <= edge_tall_aspect:
            reasons.append("edge_tall")
    return {
        "blur_score": round(blur, 2),
        "short_edge": short_edge,
        "aspect_ratio": round(aspect, 3),
        "edge_touch": edges,
        "readable": not reasons,
        "reasons": reasons,
    }


def tag_quadrant(center, point, x_axis, y_axis):
    dx, dy = point[0] - center[0], point[1] - center[1]
    x = dx * x_axis[0] + dy * x_axis[1]
    y = dx * y_axis[0] + dy * y_axis[1]
    return ("top_" if y < 0 else "bottom_") + ("left" if x < 0 else "right")


def tag_distance_limit(box, scale=1.25):
    # Preserve AWS's wider search radius; nearest mapped tag still wins.
    return math.hypot(box[2] - box[0], box[3] - box[1]) * scale


def search_title_candidates(con, title, limit=5):
    query = normalize_text(title)
    if not query:
        return []
    covers = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='book_covers'"
    ).fetchone()
    select = (
        "SELECT b.*, c.thumbnail, c.info_link FROM books b LEFT JOIN book_covers c ON c.book_id=b.id"
        if covers
        else "SELECT * FROM books"
    )
    rows = con.execute(
        select + " WHERE title_norm LIKE ? LIMIT 200", [f"%{query}%"]
    ).fetchall()
    if len(rows) < limit:
        rows = con.execute(select).fetchall()
    results = []
    for row in rows:
        keys = set(row.keys())
        score = score_text(query, row["title_norm"] or "")
        if score < 0.72:
            continue
        title_norm = normalize_text(row["title"])
        coverage = min(len(query), len(title_norm)) / max(
            len(query), len(title_norm), 1
        )
        results.append(
            {
                "source": "library_db",
                "score": round(score, 4),
                "match_confidence": "auto"
                if score >= 0.85 and coverage >= 0.65
                else "review",
                "title": row["title"],
                "authors": [row["authors"]] if row["authors"] else [],
                "publisher": row["publisher"],
                "published_date": row["published_date"],
                "class_number": row["class_number"],
                "acquisition_type": row["acquisition_type"],
                "registration_number": row["registration_number"],
                "isbns": [row["isbn"]] if row["isbn"] else [],
                "library_db_id": row["id"],
                "thumbnail": row["thumbnail"] if "thumbnail" in keys else None,
                "info_link": row["info_link"] if "info_link" in keys else None,
            }
        )
    results.sort(key=lambda candidate: candidate["score"], reverse=True)
    if len(results) > 1 and results[0]["score"] - results[1]["score"] < 0.08:
        for candidate in results:
            candidate["match_confidence"] = "review"
    return results[:limit]


TITLE_OCR_PROMPT = """この画像は本棚の一区画です。背表紙に実際に見える書名の文字を、左から右の本の順に転記してください。
JSON objectだけを返してください: {"books": [{"title": "見える書名", "legibility": "clear"}]}
- 1冊につき1エントリ。隣の本の文字を混ぜない。
- 大きい英字だけでなく、小さい日本語の書名・副題も注意深く読んで、見える書名の文字を全て含める。
- 同じJavaScriptやHTMLの本でも、各本の副題や「入門」「教本」「逆引き」などの違いを省略しない。
- 著者、出版社、分類番号は書名に含めない。
- 書名が一部しか読めない場合、読める部分だけをtitleに書き、legibilityをpartialにする。
- 一般知識・よくある書名から文字や副題を補完しない。HTML5やJavaScriptだけならその文字だけを書く。
- 書名を読めない本、本の上端・小口しか見えない本は除外。全冊読めなければ{\"books\": []}。
- clearは見える書名を十分に読める本だけ。説明文やMarkdownは不要。
"""


def parse_titles(text):
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1].strip()
        if raw.startswith("json"):
            raw = raw[4:].strip()
    data = json.loads(raw)
    books = (
        data
        if isinstance(data, list)
        else data.get("books", [])
        if isinstance(data, dict)
        else []
    )
    if not isinstance(books, list):
        raise TypeError("OCR books must be a list")
    out = []
    for book in books:
        if not isinstance(book, dict):
            continue
        title = str(book.get("title") or "").strip()
        legibility = book.get("legibility")
        if not title or legibility == "unreadable":
            continue
        item = {"title": title}
        if legibility in {"clear", "partial"}:
            item["legibility"] = legibility
        out.append(item)
    return out


def apply_legibility(candidates, book):
    if book.get("legibility") == "partial":
        for candidate in candidates:
            candidate["match_confidence"] = "review"
    return candidates
