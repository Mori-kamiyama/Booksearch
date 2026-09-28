import json
from pathlib import Path
import sqlite3
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from import_cached_metadata import canonical_isbn, enrich, extract, pages, plain
from discovery_classification import classify_level, theme_evidence
from build_discovery_index import build as build_discovery
from build_related_recommendations import RelatedIndex, build as build_related


def test_isbn_requires_checksum_and_handles_equivalent_editions():
    assert canonical_isbn('0-306-40615-2') == canonical_isbn('9780306406157')
    assert canonical_isbn('9780306406158') is None
    assert canonical_isbn('0306406153') is None
    assert canonical_isbn('1234567890128') is None
    assert canonical_isbn('9780134610993') == '9780134610993'


def test_structured_metadata_does_not_guess_pages_or_publish_html():
    assert plain('<p>本文<br>紹介</p><script>ignore()</script>') == '本文 紹介'
    assert pages(True) is None
    assert pages(-10) is None
    assert pages(1.5) is None
    assert pages('280') == 280
    assert extract('rakuten_books', {'size':'文庫', 'itemPrice':300})['page_count'] is None
    raw = {'onix':{'DescriptiveDetail':{'Extent':[{'ExtentType':'11','ExtentUnit':'03','ExtentValue':'680'}, {'ExtentType':'00','ExtentUnit':'03','ExtentValue':'12'}]}}}
    assert extract('openbd', raw)['page_count'] == 680
    raw['onix']['DescriptiveDetail']['Extent'][0]['ExtentUnit'] = '01'
    assert extract('openbd', raw)['page_count'] is None


@pytest.fixture
def cached_catalog(tmp_path):
    path = tmp_path/'source.db'
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE books(id INTEGER PRIMARY KEY,title TEXT,authors TEXT,isbn TEXT,class_number TEXT);
          CREATE TABLE book_covers(book_id INTEGER,source TEXT,raw_json TEXT,fetched_at TEXT);
          INSERT INTO books VALUES(1,'Python基礎','著者1','9780306406157','007'),
            (2,'Python演習','著者2','9780134610993','007'),(3,'写真撮影','著者3','9780134610993','740');''')
        for ident, raw in [(1, {'isbn':'0306406152','titleKana':'パイソンキソ','authorKana':'チョシャイチ','itemCaption':'初心者向けのPython教材。Pythonの基本を学ぶ。'}),
                           (2, {'isbn':'9780306406157','itemCaption':'間違った書籍の紹介'}), (3, None)]:
            db.execute('INSERT INTO book_covers VALUES(?,?,?,?)',(ident,'rakuten_books',json.dumps(raw),'2026-01-01'))
    return path


def test_import_is_additive_verified_and_reproducible(cached_catalog, tmp_path):
    before = cached_catalog.read_bytes()
    output = tmp_path/'metadata.db'
    stats = enrich(cached_catalog, output)
    assert stats['imported'] == 1 and stats['unverified_isbn'] == 1 and stats['malformed'] == 1
    assert cached_catalog.read_bytes() == before
    with sqlite3.connect(output) as db:
        assert db.execute('SELECT book_id,source,match_method FROM book_metadata').fetchall() == [(1,'rakuten_books','isbn_exact')]
        assert json.loads(db.execute('SELECT evidence_json FROM book_metadata_provenance').fetchone()[0])['isbn'] == '9780306406157'
        db.execute("UPDATE book_metadata SET description='Reviewed description'")
    repeat = tmp_path/'repeat.db'
    assert enrich(output, repeat)['existing_kept'] == 1
    with sqlite3.connect(repeat) as db:
        assert db.execute('SELECT description FROM book_metadata').fetchone()[0] == 'Reviewed description'
    with pytest.raises(ValueError): enrich(output, output)


def test_verified_readings_and_audience_reach_index(cached_catalog, tmp_path):
    metadata, output = tmp_path/'metadata.db', tmp_path/'discovery.db'
    enrich(cached_catalog, metadata)
    coverage = build_discovery(metadata, output, tmp_path/'index.json')
    assert coverage['level'] == 1
    index = json.loads((tmp_path/'index.json').read_text())
    assert index['books'][0]['title_reading'] == 'パイソンキソ'
    assert 'title_reading' not in index['books'][1]
    with sqlite3.connect(output) as db:
        level, evidence = db.execute('SELECT level,evidence FROM book_discovery WHERE book_id=1').fetchone()
        assert level == 'beginner'
        assert '初心者向け' in json.loads(evidence)['level']['quote']
    stats = build_related(output, tmp_path/'report.json')
    assert stats['books'] == 3
    with sqlite3.connect(output) as db:
        assert db.execute('SELECT count(*) FROM book_recommendation_runs').fetchone()[0] == 3


@pytest.mark.parametrize('description', ['初心者向けではない。', '初心者から上級者まで。', '入門という書名の小説。', '初心者向けの入門書。上級者向けのガイド。', '後者は初心者を対象として本質に近づく視点が欠如している。', '初心者向けの書籍では絶対に説明しない知恵を凝縮。', '中級者から上級者向けの辞典。'])
def test_uncertain_levels_remain_unknown(description):
    assert classify_level(description) == (None, None)


def test_theme_requires_more_than_incidental_mention():
    assert theme_evidence('歴史', [], '最後にPythonにも触れる。', r'python') is None
    assert theme_evidence('Python実践', [], '', r'python').startswith('書名:')
    assert theme_evidence('プログラミング', ['Python'], '', r'python').startswith('書誌カテゴリ:')


def book(ident, title, author='A', description='', isbn='', classification='007'):
    return dict(id=ident,title=title,authors=author,description=description,isbn=isbn,class_number=classification,categories_json='[]')


def test_related_content_excludes_copies_and_limits_author_concentration():
    books = [book(1,'Python programming basics',isbn='0306406152'),
             book(2,'Different cover same book',isbn='9780306406157'),
             book(3,'Python programming basics',author='different spelling'),
             *[book(i,f'Python programming exercises volume {i}',author='Prolific') for i in range(4,9)],
             book(9,'Python programming algorithms',author='Other'),
             book(10,'Urban architecture construction',author='Architect',classification='520')]
    index = RelatedIndex(books, {b['id']:{'python'} for b in books if b['id'] != 10})
    ranked = index.rank(0)
    ids = [books[j]['id'] for j, *_ in ranked]
    assert 2 not in ids and 3 not in ids and 10 not in ids
    assert 9 in ids
    assert sum(books[j]['authors'] == 'Prolific' for j,*_ in ranked) <= 2
    assert ranked == index.rank(0)
    assert all('同じテーマの本' in reasons for j,score,reasons,cosine in ranked)


def test_partial_japanese_word_and_generic_business_do_not_create_relation():
    books = [book(1,'シベリアのバイオリン',author='Author1',description='シベリア抑留の記録。平和への希望。',classification='210'),
             book(2,'バイオプラの教科書',author='Author2',description='植物を使うプラスチックの技術。',classification='578'),
             book(3,'ホテルビジネス',author='Author3',description='ホテル業界と宿泊産業。発行：クロスメディア・パブリッシング（インプレス）',classification='689'),
             book(4,'魚ビジネス',author='Author4',description='漁業と水産産業。発行：クロスメディア・パブリッシング（インプレス）',classification='660')]
    index = RelatedIndex(books,{})
    assert index.rank(0) == []
    assert index.rank(2) == []
    assert classify_level('初めて学ぶ人も対象。中上級者向けの解説も収録。') == (None,None)


@pytest.mark.parametrize('title, topic', [('Pythonで学ぶ統計学','python'), ('JavaScriptの教科書','javascript'), ('SQLを学ぶ','sql')])
def test_theme_english_terms_can_touch_japanese_particles(title, topic):
    from build_discovery_index import THEMES
    pattern = next(t[3] for t in THEMES if t[0] == topic)
    assert theme_evidence(title, [], '', pattern).startswith('書名:')
    assert theme_evidence('cpython internals' if topic == 'python' else 'not' + title, [], '', pattern) is None


def test_catalog_without_cover_cache_still_produces_copy(tmp_path):
    source, target = tmp_path/'source.db', tmp_path/'target.db'
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE books(id INTEGER PRIMARY KEY,title TEXT,isbn TEXT)')
        db.execute("INSERT INTO books VALUES(1,'No cache','9780306406157')")
    assert enrich(source, target) == {'cache_unavailable':1}
    with sqlite3.connect(target) as db:
        assert db.execute('SELECT count(*) FROM books').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM book_metadata').fetchone()[0] == 0


def test_reader_genres_are_multiple_and_preserve_legacy_filters(cached_catalog, tmp_path):
    metadata, output = tmp_path/'metadata.db', tmp_path/'discovery.db'
    enrich(cached_catalog, metadata)
    build_discovery(metadata, output, tmp_path/'index.json')
    data = json.loads((tmp_path/'index.json').read_text())
    assert next(t for t in data['topics'] if t['id'] == 'genre-it')['label'] == 'IT・プログラミング'
    assert next(t for t in data['topics'] if t['id'] == 'ndc-0')['kind'] == 'legacy'
    with sqlite3.connect(output) as db:
        assert db.execute("SELECT 1 FROM book_topics WHERE book_id=1 AND topic_id='ndc-0'").fetchone()
        assert db.execute("SELECT 1 FROM book_search_terms WHERE book_id=1 AND value_norm='ぱいそん'").fetchone()
    from discovery_genres import assign_genres
    genres = dict(assign_genres('UIデザイン', '007', ['ui-design']))
    assert set(genres) >= {'genre-it','genre-design'}
    assert dict(assign_genres('建築の歴史', '520', []))['genre-architecture']


def test_every_theme_has_explicit_reader_genre_parents():
    from build_discovery_index import THEMES
    from discovery_genres import THEME_GENRES, GENRE_RULES
    ids = {g[0] for g in GENRE_RULES}
    assert {t[0] for t in THEMES} == set(THEME_GENRES)
    assert all(set(parents) <= ids and parents for parents in THEME_GENRES.values())
    assert 'genre-it' not in THEME_GENRES['english-learning']
