import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest
from shared import scan_core
from test_aws_live_yolo_gate import load_worker as load_yolo
from test_aws_lookup_reliability import load_worker as load_lookup

import detection
import lookup
import ocr


def test_quality_is_identical_across_local_and_aws_and_relative(monkeypatch):
    worker = load_yolo(monkeypatch)
    for scale in (0.5, 1.0, 2.0):
        for crop_height in (226, 714):
            w, h = int(861 * scale), int(crop_height * scale)
            crop = np.random.default_rng(7).integers(0, 256, (h, w, 3), dtype=np.uint8)
            size = (int(1080 * scale), int(1920 * scale))
            box = (
                int(89 * scale),
                int(500 * scale),
                int(89 * scale) + w,
                int(500 * scale) + h,
            )
            a = detection.assess_crop_quality(crop, box, size).to_json()
            assert worker.assess_quality(crop, box, size) == a
            assert a["readable"] == (crop_height == 714)


def test_rotated_tag_uses_tag_axes_in_both_adapters(monkeypatch):
    import shelf_locator

    worker = load_yolo(monkeypatch)
    center = np.array([45.0, 55.0])
    local = shelf_locator.DetectedTag(
        1,
        np.zeros((4, 2)),
        center,
        np.array([0.0, 1.0]),
        np.array([-1.0, 0.0]),
        90.0,
        "unchecked",
    )
    remote = worker.DetectedTag(1, center, 90.0, "unchecked", (0.0, 1.0), (-1.0, 0.0))
    point = np.array([80.0, 90.0])
    assert (
        shelf_locator._quadrant(local, point)
        == remote.quadrant_for_point(point)
        == "top_right"
    )


def test_partial_titles_cannot_be_auto_matches():
    books = scan_core.parse_titles(
        json.dumps(
            {
                "books": [
                    {"title": "JavaScript", "legibility": "partial"},
                    {"title": "Unreadable guess", "legibility": "unreadable"},
                ]
            }
        )
    )
    assert books == [{"title": "JavaScript", "legibility": "partial"}]
    candidates = [{"score": 1.0, "match_confidence": "auto"}]
    assert (
        scan_core.apply_legibility(candidates, books[0])[0]["match_confidence"]
        == "review"
    )


def test_invalid_ocr_is_an_error_not_successful_empty_result():
    with pytest.raises(json.JSONDecodeError):
        scan_core.parse_titles("not json")
    assert scan_core.parse_titles('[{"title":"本"}]') == [{"title": "本"}]


def test_title_typo_search_is_identical_across_local_and_aws(tmp_path, monkeypatch):
    worker = load_lookup(monkeypatch)
    db = tmp_path / "library.db"
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    con.executescript("""CREATE TABLE books(id INTEGER, title TEXT, title_norm TEXT,
        authors TEXT, authors_norm TEXT, publisher TEXT, publisher_norm TEXT,
        published_date TEXT, class_number TEXT, acquisition_type TEXT,
        registration_number TEXT, isbn TEXT);
        INSERT INTO books(id,title,title_norm) VALUES(1,'プログラミングTypeScript','プログラミングtypescript');""")
    con.commit()
    local = lookup.library_db_lookup("プログラミング TypeScripx", db)["candidates"]
    assert local == worker.search_library(con, "プログラミング TypeScripx")
    assert local[0]["library_db_id"] == 1
    assert local[0]["match_confidence"] == "auto"
    con.close()


def test_local_ocr_uses_shared_prompt_and_parser():
    assert ocr.TITLE_OCR_PROMPT == scan_core.TITLE_OCR_PROMPT
    raw = '{"books":[{"title":"JavaScript", "legibility":"partial"}]}'
    assert ocr.parse_title_ocr_response(raw) == scan_core.parse_titles(raw)


@pytest.mark.parametrize(
    ("title", "book_id"),
    [
        ("図解！JavaScriptのツボとコツが絶対にわかる本", 748),
        ("図解！ JavaScript のツボとコツがゼッタイにわかる本", 748),
        ("確かな力が身につく JavaScript 「超」入門", 2131),
    ],
)
def test_user_confirmed_missed_titles_match_in_both_adapters(
    title, book_id, monkeypatch
):
    worker = load_lookup(monkeypatch)
    db = (
        Path(__file__).resolve().parents[1]
        / "aws/functions/lookup_worker/assets/library.db"
    )
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        remote = worker.search_library(con, title)
    assert lookup.library_db_lookup(title, db)["candidates"] == remote
    assert remote[0]["library_db_id"] == book_id
    assert remote[0]["match_confidence"] == "auto"


def test_missed_subtitle_does_not_confirm_a_different_javascript_book():
    db = (
        Path(__file__).resolve().parents[1]
        / "aws/functions/lookup_worker/assets/library.db"
    )
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        candidates = scan_core.search_title_candidates(con, "図解！ JavaScript 超入門")
    assert candidates
    assert all(candidate["match_confidence"] == "review" for candidate in candidates)
