import hashlib
import json
import sys
import sqlite3
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import build_semantic_index as builder


def test_snapshot_includes_wal_and_does_not_change_source(tmp_path):
    source = tmp_path / 'source.db'
    writer = sqlite3.connect(source)
    try:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('CREATE TABLE books(id INTEGER)')
        writer.execute('INSERT INTO books VALUES(1)')
        writer.commit()
        snapshot = builder.snapshot_catalog(source, tmp_path / 'output' / 'library.db')
        with sqlite3.connect(snapshot) as reader:
            assert reader.execute('SELECT id FROM books').fetchall() == [(1,)]
            assert reader.execute('PRAGMA journal_mode').fetchone()[0] == 'delete'
        assert writer.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
        with pytest.raises(ValueError, match='must differ'):
            builder.snapshot_catalog(source, source)
    finally:
        writer.close()


def test_preparation_has_no_provider_and_records_catalog(monkeypatch, tmp_path):
    db = tmp_path / 'library.db'
    db.write_bytes(b'snapshot')
    book = dict(id=1, title='本', authors='著者', isbn='', topics=[], description='確認済み紹介')
    monkeypatch.setattr(builder, 'read_catalog', lambda _: [book])
    monkeypatch.setattr(builder, 'Bedrock', lambda *_: pytest.fail('preparation called AWS'))
    books, documents, report = builder.prepare(db, tmp_path / 'output')
    assert books == [book] and '確認済み紹介' in documents[0]
    assert report['catalog_sha256'] == hashlib.sha256(b'snapshot').hexdigest()
    assert report['status'] == 'prepared_no_aws_calls'


def test_generation_publishes_only_complete_matching_snapshot(monkeypatch, tmp_path):
    db = tmp_path / 'library.db'
    db.write_bytes(b'snapshot')
    out = tmp_path / 'output'
    out.mkdir()
    book = {'id': 1}
    report = {'catalog_sha256': hashlib.sha256(b'snapshot').hexdigest()}

    class Provider:
        calls, hits = 0, 0
        def __init__(self, *_): pass
        def embed(self, text, kind):
            self.calls += 1
            return [1] + [0] * 1023

    monkeypatch.setattr(builder, 'Bedrock', Provider)
    builder.generate(db, out, 'region', [book], ['紹介'], report)
    original = (out / 'index.json').read_bytes()
    assert json.loads(original)['books'][0]['id'] == 1
    db.write_bytes(b'changed')
    with pytest.raises(ValueError, match='Catalog changed'):
        builder.generate(db, out, 'region', [book], ['紹介'], report)
    assert (out / 'index.json').read_bytes() == original
