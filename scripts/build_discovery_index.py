"""Build shared search facets and a browser suggestion index from a catalog copy.

Never edits the input DB. Only matched metadata is eligible for page counts.
Theme assignments record their source; missing levels/readings stay unknown.
"""
import argparse
import json
import re
import sqlite3
from pathlib import Path
from discovery_classification import classify_level, theme_evidence

GENRES = ['総記・情報', '哲学・心理', '歴史・地理', '社会科学', '自然科学', '技術・工学', '産業', '芸術', '言語', '文学']
THEMES = [
    ('c-language', 'C言語', ['C言語', 'C programming'], r'c\s*言語|(?<![a-z0-9])c programming(?![a-z0-9])'),
    ('brutalism', 'ブルータリズム', ['brutalism', 'ブルータリズム', 'ブルータリズム建築'], r'ブルータリズム|brutalism'),
    ('english-learning', '英語学習', ['英語', '英会話', 'TOEIC', 'TOEFL'], r'英語|英会話|toeic|toefl'),
    ('python', 'Python', ['Python', 'パイソン'], r'(?<![a-z0-9])python(?![a-z0-9])|パイソン'),
    ('typography', 'タイポグラフィ', ['タイポグラフィ', 'typography'], r'タイポグラフィ|(?<![a-z0-9])typography(?![a-z0-9])'),
    ('ui-design', 'UIデザイン', ['UIデザイン', 'UI design', 'ユーザーインターフェース'], r'ui.?デザイン|(?<![a-z0-9])ui design(?![a-z0-9])|ユーザーインターフェース'),
    ('graphic-design', 'グラフィックデザイン', ['グラフィックデザイン', 'graphic design'], r'グラフィック.?デザイン|(?<![a-z0-9])graphic design(?![a-z0-9])'),
    ('machine-learning', '機械学習', ['機械学習', 'machine learning'], r'機械学習|machine learning'),
    ('deep-learning', '深層学習', ['深層学習', 'ディープラーニング'], r'深層学習|ディープラーニング|deep learning'),
    ('javascript', 'JavaScript', ['JavaScript', 'ジャバスクリプト'], r'(?<![a-z0-9])javascript(?![a-z0-9])|ジャバスクリプト'),
    ('sql', 'SQL', ['SQL', 'エスキューエル'], r'(?<![a-z0-9])sql(?![a-z0-9])'),
    ('statistics', '統計学', ['統計学', '統計解析'], r'統計学|統計解析|(?<![a-z0-9])statistics(?![a-z0-9])'),
    ('photography', '写真撮影', ['写真撮影', '撮影技法'], r'写真撮影|撮影技法|フォトグラフィー|(?<![a-z0-9])photography(?![a-z0-9])'),
    ('color-theory', '色彩・配色', ['色彩', '配色', 'color theory'], r'色彩|配色|color theory'),
    ('animation', 'アニメーション制作', ['アニメーション制作', 'animation'], r'アニメーション(?:制作|の技法|の作り方)|animation techniques'),
    ('web-design', 'Webデザイン', ['Webデザイン', 'ウェブデザイン'], r'web.?デザイン|ウェブデザイン|web design'),
    ('marketing', 'マーケティング', ['マーケティング', 'marketing'], r'マーケティング|(?<![a-z0-9])marketing(?![a-z0-9])'),
    ('accounting', '簿記・会計', ['簿記', '会計', 'accounting'], r'簿記|会計|(?<![a-z0-9])accounting(?![a-z0-9])'),
    ('urban-planning', '都市計画', ['都市計画', 'urban planning'], r'都市計画|urban planning'),
]

def build(source, output_db, output_index):
    if source.resolve() == output_db.resolve():
        raise ValueError('Output DB must differ from source')
    source_db = sqlite3.connect(f'{source.resolve().as_uri()}?mode=ro', uri=True)
    output_db.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(output_db)
    source_db.backup(db)
    source_db.close()
    db.row_factory = sqlite3.Row
    db.executescript('''DROP TABLE IF EXISTS book_discovery;
      DROP TABLE IF EXISTS book_topics;
      CREATE TABLE book_discovery(book_id INTEGER PRIMARY KEY, page_count INTEGER, level TEXT, evidence TEXT);
      CREATE TABLE book_topics(book_id INTEGER NOT NULL, topic_id TEXT NOT NULL, evidence TEXT NOT NULL, PRIMARY KEY(book_id,topic_id));
      CREATE INDEX book_topics_topic ON book_topics(topic_id,book_id);''')
    has_metadata = db.execute("SELECT 1 FROM sqlite_master WHERE name='book_metadata'").fetchone()
    metadata = {r['book_id']: dict(r) for r in db.execute("SELECT * FROM book_metadata WHERE fetch_status='matched'")} if has_metadata else {}
    has_provenance = db.execute("SELECT 1 FROM sqlite_master WHERE name='book_metadata_provenance'").fetchone()
    provenance = {r['book_id']: dict(r) for r in db.execute('SELECT * FROM book_metadata_provenance')} if has_provenance else {}
    entries, counts = [], {}
    for row in db.execute('SELECT id,title,authors,class_number FROM books ORDER BY id').fetchall():
        book = dict(row)
        meta = metadata.get(book['id'], {})
        pages = meta.get('page_count')
        pages = pages if isinstance(pages, int) and pages > 0 else None
        level, level_evidence = classify_level(meta.get('description'))
        evidence = {}
        if pages: evidence['page_count'] = {'source': meta.get('source'), 'source_id': meta.get('source_id')}
        if level: evidence['level'] = dict(level_evidence, source=meta.get('source'), source_id=meta.get('source_id'))
        db.execute('INSERT INTO book_discovery VALUES(?,?,?,?)', (book['id'], pages, level, json.dumps(evidence, ensure_ascii=False) if evidence else None))
        try: categories = json.loads(meta.get('categories_json') or '[]')
        except (ValueError, TypeError): categories = []
        categories = [v for v in categories if isinstance(v, str)] if isinstance(categories, list) else []
        topics = []
        classification = str(book.get('class_number') or '').strip()
        if re.match(r'^\d{3}(?:\D|$)', classification):
            topics.append((f'ndc-{classification[0]}', f'分類番号: {classification}'))
        for topic_id, label, aliases, pattern in THEMES:
            evidence = theme_evidence(book['title'], categories, meta.get('description') or '', pattern)
            if evidence: topics.append((topic_id, evidence))
        for topic_id, evidence in topics:
            db.execute('INSERT INTO book_topics VALUES(?,?,?)', (book['id'], topic_id, evidence))
            counts[topic_id] = counts.get(topic_id, 0) + 1
        entry = {'id': book['id'], 'title': book['title'] or '', 'authors': book['authors'] or ''}
        for field in ('title_reading', 'authors_reading'):
            value = provenance.get(book['id'], {}).get(field) if meta else None
            if value: entry[field] = value
        entries.append(entry)
    topics = [{'id': f'ndc-{i}', 'label': label, 'aliases': [label], 'count': counts.get(f'ndc-{i}', 0)} for i, label in enumerate(GENRES)]
    topics += [{'id': ident, 'label': label, 'aliases': aliases, 'count': counts.get(ident, 0)} for ident, label, aliases, _ in THEMES]
    topics = [topic for topic in topics if topic['count']]
    for topic in topics:
        if not topic['id'].startswith('ndc-'):
            topic['genres'] = [row[0] for row in db.execute("SELECT DISTINCT g.topic_id FROM book_topics t JOIN book_topics g ON g.book_id=t.book_id WHERE t.topic_id=? AND g.topic_id LIKE 'ndc-%' ORDER BY g.topic_id", (topic['id'],))]
    payload = {'version': 1, 'books': entries, 'topics': topics, 'coverage': {'books': len(entries), 'page_count': db.execute('SELECT count(*) FROM book_discovery WHERE page_count IS NOT NULL').fetchone()[0], 'level': db.execute('SELECT count(*) FROM book_discovery WHERE level IS NOT NULL').fetchone()[0]}}
    output_index.parent.mkdir(parents=True, exist_ok=True)
    output_index.write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')))
    db.commit()
    db.close()
    return payload['coverage']

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--output-db', type=Path, required=True)
    parser.add_argument('--output-index', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.db, args.output_db, args.output_index)))
