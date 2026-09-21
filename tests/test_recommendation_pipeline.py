"""Offline recommendation packaging contract, without any external service."""
import sqlite3
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/build_bm25_recommendations.py'


def test_single_source_uses_full_catalogue_and_dry_run_preserves_schema(tmp_path):
    path = tmp_path / 'catalog.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE books(id INTEGER PRIMARY KEY,title TEXT,authors TEXT,publisher TEXT,class_number TEXT)')
        db.executemany('INSERT INTO books VALUES(?,?,?,?,?)', [
            (1, 'Python入門', '著者', '出版社', '007'),
            (2, 'Python実践', '著者', '出版社', '007'),
            (3, '日本史', '別著者', '歴史社', '210'),
        ])
    args = [sys.executable, str(SCRIPT), '--db', str(path), '--book-id', '1']
    result = subprocess.run(args + ['--dry-run'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [('books',)]
    result = subprocess.run(args, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(path) as db:
        rows = db.execute('SELECT source_book_id,recommended_book_id FROM book_recommendations ORDER BY score DESC').fetchall()
    assert rows and rows[0] == (1, 2)
    assert all(source == 1 and candidate != 1 for source, candidate in rows)


def test_single_book_dry_run_does_not_create_tables(tmp_path):
    path = tmp_path / 'single.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE books(id INTEGER PRIMARY KEY,title TEXT,authors TEXT,publisher TEXT,class_number TEXT)')
        db.execute("INSERT INTO books VALUES(1,'唯一の本','','','')")
    result = subprocess.run([sys.executable, str(SCRIPT), '--db', str(path), '--dry-run'], capture_output=True, text=True)
    assert result.returncode == 1
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [('books',)]
