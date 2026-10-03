# /// script
# requires-python = ">=3.12"
# dependencies = ["boto3>=1.40,<2"]
# ///
"""Prepare a catalog-bound index; AWS calls require explicit --generate.

uv run scripts/build_semantic_index.py --db aws/functions/go_api/library.db
"""
import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from evaluate_semantic_search import Bedrock, MODEL, document, read_catalog, unit


def snapshot_catalog(source, destination):
    if source.resolve() == destination.resolve():
        raise ValueError('Source catalog must differ from generated snapshot')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.db.tmp')
    temporary.unlink(missing_ok=True)
    original = sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)
    snapshot = sqlite3.connect(temporary)
    try:
        original.backup(snapshot)
        snapshot.execute('PRAGMA journal_mode=DELETE')
    finally:
        snapshot.close()
        original.close()
    temporary.replace(destination)
    return destination


def prepare(db, output):
    books = read_catalog(db)
    documents = [document(book) for book in books]
    output.mkdir(parents=True, exist_ok=True)
    report = {
        'status': 'prepared_no_aws_calls', 'model': MODEL,
        'catalog_sha256': hashlib.sha256(db.read_bytes()).hexdigest(),
        'books': len(books), 'descriptions': sum(bool(b['description']) for b in books),
        'characters': sum(map(len, documents)),
        'max_document_characters': max(map(len, documents), default=0),
        'sent_fields': ['title', 'authors', 'topic_labels', 'matched_description'],
        'sample_documents': documents[:3],
    }
    (output / 'prepared.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return books, documents, report


def generate(db, output, region, books, documents, report):
    provider = Bedrock(region, output / 'embedding-cache')
    # Fail early on an inaccessible model without sending the catalog.
    provider.embed('書籍検索', 'search_query')
    rows = []
    for number, (book, text) in enumerate(zip(books, documents), 1):
        vector = unit(provider.embed(text, 'search_document'))
        if len(vector) != 1024:
            raise ValueError('Expected Cohere multilingual v3 dimensions: 1024')
        rows.append({'id': book['id'], 'vector': vector})
        if number % 100 == 0:
            print(f'Embedded {number}/{len(books)}', flush=True)
    if hashlib.sha256(db.read_bytes()).hexdigest() != report['catalog_sha256']:
        raise ValueError('Catalog changed during generation; index not published')
    artifact = {'version': 1, 'model': MODEL, 'catalog_sha256': report['catalog_sha256'], 'books': rows}
    temporary = output / 'index.json.tmp'
    temporary.write_text(json.dumps(artifact, ensure_ascii=False, separators=(',', ':')) + '\n')
    temporary.replace(output / 'index.json')
    return {'status': 'generated', 'books': len(rows), 'embedding_calls': provider.calls,
            'cache_hits': provider.hits, 'index': str(output / 'index.json')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('outputs/semantic-serving'))
    parser.add_argument('--region', default='ap-northeast-1')
    parser.add_argument('--generate', action='store_true', help='Send catalog text to Bedrock; incurs usage charges')
    args = parser.parse_args()
    snapshot = snapshot_catalog(args.db, args.output / 'library.db')
    books, documents, report = prepare(snapshot, args.output)
    if args.generate:
        result = generate(snapshot, args.output, args.region, books, documents, report)
    else:
        result = {k: v for k, v in report.items() if k != 'sample_documents'}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
