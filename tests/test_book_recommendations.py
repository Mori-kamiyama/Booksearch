"""Offline checks for Google Books matching and BM25 recommendation helpers."""

from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from enrich_book_metadata import select_volume  # noqa: E402
from build_bm25_recommendations import ranked_recommendations  # noqa: E402
from fetch_missing_covers import rakuten_cover, rakuten_title_match  # noqa: E402
from recommendation_utils import metadata_match_score, tokenize  # noqa: E402


class MetadataMatchingTests(unittest.TestCase):
    def test_exact_isbn_wins_over_similar_title(self) -> None:
        score, method = metadata_match_score(
            local_title="Pythonデータ分析入門",
            local_authors="山田 太郎",
            local_isbn="978-4-1234-5678-9",
            candidate_title="別の本",
            candidate_authors=[],
            candidate_isbns=["9784123456789"],
        )
        self.assertEqual((score, method), (1.0, "isbn_exact"))

    def test_weak_title_only_match_is_rejected(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute("CREATE TABLE books (id INTEGER, title TEXT, authors TEXT, isbn TEXT)")
        connection.execute("INSERT INTO books VALUES (1, '図書館の未来', '', '')")
        book = connection.execute("SELECT * FROM books").fetchone()
        volume, score, method = select_volume(book, [{"id": "x", "volumeInfo": {"title": "図書館戦争"}}])
        self.assertIsNone(volume)
        self.assertLess(score, 0.92)
        self.assertEqual(method, "title_only")

    def test_rakuten_title_match_prefers_matching_edition(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute(
            "CREATE TABLE books (title TEXT, authors TEXT, publisher TEXT, published_date TEXT)"
        )
        connection.execute("INSERT INTO books VALUES ('広辞苑', '新村, 出', '岩波書店', '2018/01')")
        book = connection.execute("SELECT * FROM books").fetchone()
        payload = {"items": [
            {"title": "広辞苑", "author": "新村 出", "publisherName": "岩波書店", "salesDate": "2007年12月", "largeImageUrl": "https://example.com/old.jpg"},
            {"title": "広辞苑 第7版（普通版）", "author": "新村 出", "publisherName": "岩波書店", "salesDate": "2018年01月", "largeImageUrl": "https://example.com/new.jpg"},
        ]}
        matched = rakuten_title_match(payload, book)
        self.assertIsNotNone(matched)
        self.assertEqual(matched["largeImageUrl"], "https://example.com/new.jpg")

    def test_rakuten_noimage_is_not_a_cover(self) -> None:
        self.assertEqual(rakuten_cover({"largeImageUrl": "https://example.com/noimage_01.gif"}), "")


class KeywordBm25Tests(unittest.TestCase):
    def test_japanese_tokenizer_emits_ngrams_and_ascii_words(self) -> None:
        tokens = tokenize("Pythonで学ぶ機械学習")
        self.assertIn("python", tokens)
        self.assertIn("機械", tokens)
        self.assertIn("機械学", tokens)

    def test_related_document_ranks_first(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute(
            """
            CREATE TABLE books (
                id INTEGER, title TEXT, authors TEXT, publisher TEXT,
                class_number TEXT, description TEXT, categories_json TEXT, categories_text TEXT
            )
            """
        )
        connection.executemany(
            "INSERT INTO books VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (1, "Pythonデータ分析入門", "山田太郎", "技術社", "007.6", "Pythonでデータ分析と機械学習を始める入門書", '["プログラミング", "データ分析"]', "プログラミング データ分析"),
                (2, "実践Python機械学習", "佐藤花子", "技術社", "007.6", "Pythonを使ったデータ分析と機械学習の実践", '["プログラミング", "データ分析"]', "プログラミング データ分析"),
                (3, "日本の近代史", "鈴木一郎", "歴史社", "210", "明治時代から現代までの日本史", '["歴史"]', "歴史"),
            ],
        )
        books = connection.execute("SELECT * FROM books ORDER BY id").fetchall()
        ranked = ranked_recommendations(books, source_index=0, top_k=2)
        self.assertEqual(ranked[0][0]["id"], 2)
        self.assertIn("分類が近い本", ranked[0][2])


if __name__ == "__main__":
    unittest.main()
