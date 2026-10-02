"""Local import adapter for the shared recognition policy shipped to Lambda."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "aws" / "functions"))
from shared.scan_core import (
    MIN_BLUR_SCORE,
    MIN_SHORT_EDGE_PX,
    MIN_SHORT_EDGE_RATIO,
    POLICY_VERSION,
    TITLE_OCR_PROMPT,
    apply_legibility,
    assess_quality,
    normalize_isbn,
    normalize_text,
    parse_titles,
    score_text,
    search_title_candidates,
    tag_distance_limit,
    tag_quadrant,
)

__all__ = [
    "MIN_BLUR_SCORE",
    "MIN_SHORT_EDGE_PX",
    "MIN_SHORT_EDGE_RATIO",
    "POLICY_VERSION",
    "TITLE_OCR_PROMPT",
    "apply_legibility",
    "assess_quality",
    "normalize_isbn",
    "normalize_text",
    "parse_titles",
    "score_text",
    "search_title_candidates",
    "tag_distance_limit",
    "tag_quadrant",
]
