"""Recover ISBN-verified bibliography from cached cover responses, into a copy.

No network calls. Unknown or conflicting identifiers never enrich a book.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sqlite3

from enrich_book_metadata import create_schema


def canonical_isbn(value):
    value = re.sub(r'[^0-9X]', '', str(value or '').upper())
    if len(value) == 10:
        if not value[:9].isdigit() or (not value[-1].isdigit() and value[-1] != 'X'):
            return None
        digits = [10 if c == 'X' else int(c) for c in value]
        if sum((10-i)*n for i, n in enumerate(digits)) % 11:
            return None
        body = '978' + value[:9]
        return body + str((-sum(int(n)*(1 if i%2 == 0 else 3) for i,n in enumerate(body)))%10)
    if len(value) == 13 and value.isdigit() and value.startswith(('978','979')):
        if sum(int(n)*(1 if i%2 == 0 else 3) for i,n in enumerate(value)) % 10 == 0:
            return value
    return None


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'): self.hidden += 1
        if tag in ('p', 'br', 'div', 'li'): self.parts.append('\n')
    def handle_endtag(self, tag):
        if tag in ('script', 'style'): self.hidden = max(0, self.hidden-1)
        if tag in ('p', 'div', 'li'): self.parts.append('\n')
    def handle_data(self, data):
        if not self.hidden: self.parts.append(data)


def plain(value):
    if not isinstance(value, str): return ''
    parser = PlainText()
    parser.feed(value)
    return re.sub(r'\s+', ' ', ''.join(parser.parts)).strip()


def pages(value):
    # Numeric strings occur in ONIX; bool, decimals and implausible values do not qualify.
    if isinstance(value, bool) or not re.fullmatch(r'\d{1,5}', str(value or '')): return None
    count = int(value)
    return count if 0 < count <= 10000 else None


def as_list(value):
    return value if isinstance(value, list) else [value] if isinstance(value, dict) else []


def extract(source, raw):
    result = {'isbns': [], 'description': '', 'categories': [], 'page_count': None,
              'language': None, 'title_reading': '', 'authors_reading': '', 'source_id': '', 'url': '', 'fields': {}}
    if source == 'google_books':
        info = raw.get('volumeInfo', {})
        result.update(isbns=[i.get('identifier') for i in info.get('industryIdentifiers', []) if isinstance(i, dict)],
                      description=plain(info.get('description')), categories=info.get('categories', []),
                      page_count=pages(info.get('pageCount')), language=info.get('language'),
                      source_id=raw.get('id', ''), url=info.get('infoLink', ''),
                      fields={'description':'volumeInfo.description','page_count':'volumeInfo.pageCount'})
    elif source == 'rakuten_books':
        result.update(isbns=[raw.get('isbn')], description=plain(raw.get('itemCaption')),
                      title_reading=plain(raw.get('titleKana')), authors_reading=plain(raw.get('authorKana')),
                      source_id=str(raw.get('isbn') or ''), url=raw.get('itemUrl', ''),
                      fields={'description':'itemCaption','title_reading':'titleKana','authors_reading':'authorKana'})
    elif source == 'openlibrary':
        identifiers = raw.get('identifiers', {})
        result.update(isbns=identifiers.get('isbn_13', []) + identifiers.get('isbn_10', []),
                      page_count=pages(raw.get('number_of_pages')), source_id=raw.get('key', ''), url=raw.get('url', ''),
                      categories=[s.get('name') for s in raw.get('subjects', []) if isinstance(s, dict)],
                      fields={'page_count':'number_of_pages', 'categories':'subjects'})
    elif source == 'openbd':
        onix = raw.get('onix') or {}
        detail = onix.get('DescriptiveDetail') or {}
        result['isbns'] = [i.get('IDValue') for i in as_list(onix.get('ProductIdentifier')) if i.get('ProductIDType') in ('02','15')]
        texts = [t for t in as_list((onix.get('CollateralDetail') or {}).get('TextContent')) if t.get('TextType') in ('02','03') and t.get('ContentAudience') == '00']
        result['description'] = max((plain(t.get('Text')) for t in texts), key=len, default='')
        extents = [pages(e.get('ExtentValue')) for e in as_list(detail.get('Extent')) if e.get('ExtentType') == '11' and e.get('ExtentUnit') == '03']
        result['page_count'] = next((e for e in extents if e), None)
        title = ((detail.get('TitleDetail') or {}).get('TitleElement') or {}).get('TitleText') or {}
        result['title_reading'] = plain(title.get('collationkey')) if isinstance(title, dict) else ''
        result['source_id'] = str(onix.get('RecordReference') or '')
        result['fields'] = {'description':'onix.CollateralDetail.TextContent[02/03,00]', 'page_count':'onix.DescriptiveDetail.Extent[11,03]', 'title_reading':'onix.DescriptiveDetail.TitleDetail.TitleElement.TitleText.collationkey'}
    result['categories'] = [plain(v) for v in result['categories'] if isinstance(v, str) and plain(v)] if isinstance(result['categories'], list) else []
    return result


def enrich(source, output):
    source, output = Path(source), Path(output)
    if source.resolve() == output.resolve(): raise ValueError('Output must differ from input')
    output.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(f'{source.resolve().as_uri()}?mode=ro', uri=True) as original, sqlite3.connect(output) as db:
        original.backup(db)
        create_schema(db)
        db.execute('''CREATE TABLE IF NOT EXISTS book_metadata_provenance(
          book_id INTEGER PRIMARY KEY, title_reading TEXT, authors_reading TEXT, evidence_json TEXT NOT NULL)''')
        db.row_factory = sqlite3.Row
        counts = Counter()
        cache_columns = {row[1] for row in db.execute('PRAGMA table_info(book_covers)')}
        if not {'book_id', 'source', 'raw_json', 'fetched_at'}.issubset(cache_columns):
            return {'cache_unavailable': 1}
        for book in db.execute('SELECT b.id,b.isbn,c.source,c.raw_json,c.fetched_at FROM books b JOIN book_covers c ON c.book_id=b.id').fetchall():
            try:
                raw = json.loads(book['raw_json'] or '{}')
                if not isinstance(raw, dict): raise ValueError('not an object')
                data = extract(book['source'], raw)
            except (ValueError, TypeError, AttributeError):
                counts['malformed'] += 1
                continue
            isbn = canonical_isbn(book['isbn'])
            if not isbn or isbn not in {canonical_isbn(i) for i in data['isbns']}:
                counts['unverified_isbn'] += 1
                continue
            if not any([data['description'], data['page_count'], data['categories'], data['title_reading'], data['authors_reading']]):
                counts['empty'] += 1
                continue
            # Keep previously reviewed/fetched metadata. This import is additive, never downgrading.
            if db.execute("SELECT 1 FROM book_metadata WHERE book_id=? AND fetch_status='matched'", (book['id'],)).fetchone():
                counts['existing_kept'] += 1
                continue
            evidence = {'source':book['source'], 'source_id':data['source_id'], 'url':data['url'],
                        'isbn':isbn, 'match':'isbn_checksum_exact_or_equivalent', 'fields':data['fields'],
                        'cached_at':book['fetched_at']}
            db.execute('''INSERT OR REPLACE INTO book_metadata
              (book_id,description,categories_json,page_count,language,source,source_id,match_method,match_score,fetched_at,fetch_status,error)
              VALUES(?,?,?,?,?,?,?,'isbn_exact',1,?,'matched',NULL)''',
              (book['id'],data['description'],json.dumps(data['categories'], ensure_ascii=False),data['page_count'],data['language'],book['source'],data['source_id'],book['fetched_at'] or datetime.now(timezone.utc).isoformat()))
            db.execute('INSERT OR REPLACE INTO book_metadata_provenance VALUES(?,?,?,?)',
                       (book['id'],data['title_reading'],data['authors_reading'],json.dumps(evidence, ensure_ascii=False)))
            counts['imported'] += 1
            counts['description'] += bool(data['description'])
            counts['page_count'] += bool(data['page_count'])
            counts['title_reading'] += bool(data['title_reading'])
            counts['authors_reading'] += bool(data['authors_reading'])
        return dict(counts)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--output-db', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(enrich(args.db, args.output_db), ensure_ascii=False))
