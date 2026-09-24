"""Small, dependency-free helpers for metadata matching and BM25 recommendations."""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from typing import Iterable


ASCII_TOKEN_RE = re.compile(r"[a-z0-9]+")
JAPANESE_CHAR_RE = re.compile(r"[一-龯々〆〤ぁ-ゖァ-ヺー]")
STOP_WORDS = frozenset({"の", "に", "を", "と", "は", "が", "で", "な", "へ", "や", "the", "and", "for", "with"})


def normalize_text(value: str | None) -> str:
    """Normalise a bibliographic string for tolerant comparisons."""
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKC", value).lower()
    return "".join(char for char in normalized if char.isalnum())


def normalize_isbn(value: str | None) -> str:
    if not value:
        return ""
    normalized = "".join(char.upper() for char in value if char.isdigit() or char in "xX")
    return normalized if len(normalized) in {10, 13} else ""


def tokenize(value: str | None) -> list[str]:
    """Return ASCII words plus 2/3-character Japanese n-grams.

    This intentionally uses no morphology dependency. It is a baseline for a
    small catalogue and works even when an external tokenizer is unavailable.
    """
    text = unicodedata.normalize("NFKC", value or "").lower()
    tokens = [match.group(0) for match in ASCII_TOKEN_RE.finditer(text) if match.group(0) not in STOP_WORDS]
    japanese_runs = re.findall(r"[一-龯々〆〤ぁ-ゖァ-ヺー]+", text)
    for run in japanese_runs:
        for size in (2, 3):
            tokens.extend(run[index : index + size] for index in range(len(run) - size + 1))
    return tokens


def repeated_tokens(value: str | None, weight: int) -> list[str]:
    return tokenize(value) * max(weight, 0)


def text_similarity(left: str | None, right: str | None) -> float:
    """Character based score suitable for Japanese title matching."""
    a, b = normalize_text(left), normalize_text(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def author_similarity(left: str | None, right: str | None) -> float:
    a, b = normalize_text(left), normalize_text(right)
    if not a or not b:
        return 0.0
    if a == b or a in b or b in a:
        return 1.0
    return text_similarity(a, b)


def metadata_match_score(
    *,
    local_title: str,
    local_authors: str,
    local_isbn: str,
    candidate_title: str,
    candidate_authors: Iterable[str],
    candidate_isbns: Iterable[str],
) -> tuple[float, str]:
    """Score a Google Books candidate without accepting weak title-only hits."""
    isbn = normalize_isbn(local_isbn)
    candidate_isbn_set = {normalize_isbn(item) for item in candidate_isbns}
    candidate_isbn_set.discard("")
    if isbn and isbn in candidate_isbn_set:
        return 1.0, "isbn_exact"

    title_score = text_similarity(local_title, candidate_title)
    candidate_author_text = " ".join(candidate_authors)
    author_score = author_similarity(local_authors, candidate_author_text)
    if not local_authors:
        return title_score, "title_only"
    return 0.78 * title_score + 0.22 * author_score, "title_author"


def class_similarity(left: str | None, right: str | None) -> float:
    a = normalize_text(left)
    b = normalize_text(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    for prefix_length, score in ((3, 0.55), (2, 0.30), (1, 0.12)):
        if len(a) >= prefix_length and len(b) >= prefix_length and a[:prefix_length] == b[:prefix_length]:
            return score
    return 0.0


def bm25_scores(documents: list[list[str]], query: list[str], k1: float = 1.5, b: float = 0.75) -> list[float]:
    """Calculate Okapi BM25 scores for one tokenised query against documents."""
    if not documents or not query:
        return [0.0] * len(documents)
    document_frequency: Counter[str] = Counter()
    for document in documents:
        document_frequency.update(set(document))
    average_length = sum(len(document) for document in documents) / len(documents)
    if average_length == 0:
        return [0.0] * len(documents)

    query_terms = Counter(query)
    scores: list[float] = []
    for document in documents:
        frequencies = Counter(document)
        length_factor = k1 * (1 - b + b * len(document) / average_length)
        score = 0.0
        for term, query_frequency in query_terms.items():
            frequency = frequencies.get(term, 0)
            if not frequency:
                continue
            idf = math.log(1 + (len(documents) - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5))
            score += query_frequency * idf * (frequency * (k1 + 1) / (frequency + length_factor))
        scores.append(score)
    return scores
