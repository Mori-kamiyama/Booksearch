# /// script
# requires-python = ">=3.12"
# dependencies = ["boto3>=1.40,<2"]
# ///
"""Offline catalog experiment. Does not deploy or change the serving catalog.

uv run scripts/evaluate_semantic_search.py --db ... --output outputs/semantic
Use --prepare-only to inspect documents/baselines without AWS calls.
"""
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time

from discovery_genres import normalize_search_text, GENRE_RULES
from build_discovery_index import THEMES

TOPIC_LABELS = {row[0]: row[1] for row in [*GENRE_RULES, *THEMES]}

MODEL = 'cohere.embed-multilingual-v3'
RERANK = 'cohere.rerank-v3-5:0'


def unit(vector):
    if not vector or any(not math.isfinite(x) for x in vector):
        raise ValueError('invalid embedding')
    length = math.sqrt(sum(x*x for x in vector))
    if not length:
        raise ValueError('zero embedding')
    return [x/length for x in vector]


def read_catalog(path):
    # Input must be a closed, generated snapshot (not a live WAL database).
    if Path(str(path) + '-wal').exists():
        raise ValueError('Use a closed catalog snapshot without a WAL file')
    db = sqlite3.connect(path.resolve().as_uri()+'?mode=ro&immutable=1', uri=True)
    db.row_factory = sqlite3.Row
    try:
        topics, terms = defaultdict(list), defaultdict(list)
        for ident, value in db.execute('SELECT book_id,topic_id FROM book_topics ORDER BY topic_id'):
            topics[ident].append(value)
        for ident, value in db.execute('SELECT book_id,value_norm FROM book_search_terms ORDER BY value_norm'):
            terms[ident].append(value)
        books = [dict(row) for row in db.execute('''SELECT b.id,b.title,b.authors,b.isbn,
            m.description,d.page_count,d.level FROM books b
            LEFT JOIN book_metadata m ON m.book_id=b.id AND m.fetch_status='matched'
            LEFT JOIN book_discovery d ON d.book_id=b.id ORDER BY b.id''')]
        for b in books:
            b['topics'], b['terms'] = topics[b['id']], terms[b['id']]
        return books
    finally:
        db.close()


def document(book):
    # Stable text cap fits the model character limit. No inferred level or summary.
    return (f"書名: {book['title'] or ''}\n著者: {book['authors'] or ''}\n"
            f"テーマ: {', '.join(TOPIC_LABELS[t] for t in book['topics'] if t in TOPIC_LABELS)}\n内容紹介: {book['description'] or ''}")[:2000]


def eligible(book, filters):
    unknown = set(filters) - {'genre', 'topic', 'min_pages', 'max_pages', 'level'}
    if unknown:
        raise ValueError(f'Unknown filters: {unknown}')
    for key in ('genre', 'topic'):
        if filters.get(key) and filters[key] not in book['topics']:
            return False
    for key, compare in [('min_pages', lambda a,b: a >= b), ('max_pages', lambda a,b: a <= b)]:
        if filters.get(key) is not None:
            if book['page_count'] is None or not compare(book['page_count'], filters[key]):
                return False
    return not filters.get('level') or book['level'] == filters['level']


def exact(book, query):
    q = normalize_search_text(query)
    return bool(q) and q in (normalize_search_text(book['title']), normalize_search_text(book['isbn']))


def lexical(books, query):
    """Comparable local AND/alias baseline; not the UI's typo-correction pipeline."""
    tokens = [normalize_search_text(t) for t in query.split()]
    tokens = [t for t in tokens if t]
    if not tokens:
        return []
    result = [b for b in books if all(any(t in value for value in b['terms'] +
        [normalize_search_text(b['isbn'])]) for t in tokens)]
    return sorted(result, key=lambda b: (not exact(b, query), b['id']))


def fuse(lex, semantic, query, limit=30):
    scores, by_id = defaultdict(float), {}
    for ranking in (lex, semantic):
        for rank, book in enumerate(ranking[:100], 1):
            scores[book['id']] += 1/(60+rank)
            by_id[book['id']] = book
    return sorted(by_id.values(), key=lambda b: (not exact(b, query), -scores[b['id']], b['id']))[:limit]


class Bedrock:
    def __init__(self, region, cache):
        import boto3
        from botocore.config import Config
        config = Config(connect_timeout=3, read_timeout=15, retries={'total_max_attempts': 1})
        self.embed_client = boto3.client('bedrock-runtime', region_name=region, config=config)
        self.rank_client = boto3.client('bedrock-agent-runtime', region_name=region, config=config)
        self.region, self.cache = region, cache
        self.calls, self.hits = 0, 0
        cache.mkdir(parents=True, exist_ok=True)

    def embed(self, text, kind):
        key = hashlib.sha256(json.dumps([MODEL, kind, text], ensure_ascii=False).encode()).hexdigest()
        path = self.cache/f'{key}.json'
        if path.exists():
            self.hits += 1
            return unit(json.loads(path.read_text()))
        self.calls += 1
        response = self.embed_client.invoke_model(modelId=MODEL, contentType='application/json',
            accept='application/json', body=json.dumps({'texts':[text], 'input_type':kind, 'truncate':'END'}))
        body = response['body']
        try:
            vector = unit(json.loads(body.read())['embeddings'][0])
        finally:
            body.close()
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(vector))
        tmp.replace(path)
        return vector

    def rerank(self, query, candidates):
        if not candidates:
            return []
        response = self.rank_client.rerank(queries=[{'type':'TEXT', 'textQuery':{'text':query}}],
            sources=[{'type':'INLINE', 'inlineDocumentSource':{'type':'TEXT',
                'textDocument':{'text':document(b)}}} for b in candidates],
            rerankingConfiguration={'type':'BEDROCK_RERANKING_MODEL',
                'bedrockRerankingConfiguration':{'modelConfiguration':{
                    'modelArn':f'arn:aws:bedrock:{self.region}::foundation-model/{RERANK}'},
                    'numberOfResults':len(candidates)}})
        indices = [r['index'] for r in response['results']]
        if sorted(indices) != list(range(len(candidates))):
            raise ValueError('Incomplete or invalid rerank result')
        ranked = [candidates[i] for i in indices]
        return sorted(ranked, key=lambda b: not exact(b, query))


def search(books, vectors, case, provider):
    started = time.perf_counter()
    candidates = [b for b in books if eligible(b, case.get('filters', {}))]
    query = case['query']
    baseline = lexical(candidates, query)
    result = {'query':query, 'filters':case.get('filters', {}), 'lexical':baseline[:10],
              'status':'ok', 'timings_ms':{}}
    if not candidates:
        result.update(hybrid=[], reranked=[])
        return result
    try:
        q = unit(provider.embed(query, 'search_query'))
        if any(len(vectors[b['id']]) != len(q) for b in candidates):
            raise ValueError('Embedding dimensions do not match')
        ranked = sorted(candidates, key=lambda b: -sum(x*y for x,y in zip(q, vectors[b['id']])) )
        hybrid = fuse(baseline, ranked, query)
        result['hybrid'] = hybrid[:10]
        result['timings_ms']['retrieval'] = round((time.perf_counter()-started)*1000)
    except Exception as exc:
        result.update(status='embedding_failed_keyword_fallback', error=str(exc), hybrid=baseline[:10], reranked=baseline[:10])
        return result
    started = time.perf_counter()
    try:
        result['reranked'] = provider.rerank(query, hybrid)[:10]
    except Exception as exc:
        result.update(status='rerank_failed_hybrid_fallback', error=str(exc), reranked=hybrid[:10])
    result['timings_ms']['rerank'] = round((time.perf_counter()-started)*1000)
    return result


def compact(result):
    for key in ('lexical', 'hybrid', 'reranked'):
        if key in result:
            result[key] = [{k:b[k] for k in ('id','title','authors','page_count','level')} for b in result[key]]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cases', type=Path, default=Path(__file__).parents[1]/'tests/fixtures/semantic_queries.json')
    parser.add_argument('--region', default='ap-northeast-1')
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--limit', type=int, default=0, help='Optional smoke-test catalog size; 0 = full catalog')
    args = parser.parse_args()
    books = read_catalog(args.db)
    if args.limit < 0:
        parser.error('--limit must be nonnegative')
    if args.limit:
        books = books[:args.limit]
    cases = json.loads(args.cases.read_text())
    args.output.mkdir(parents=True, exist_ok=True)
    report = {'models':{'embedding':MODEL,'rerank':RERANK},'region':args.region,
        'books':len(books),'descriptions':sum(bool(b['description']) for b in books),
        'quality_verdict':'not_evaluated_requires_human_judgments',
        'notes':['Local lexical baseline excludes UI typo correction.',
                 'Semantic nearest neighbors always exist; no relevance threshold is calibrated yet.',
                 'Cold/cached timings must not be interpreted as production latency.'],
        'results':[]}
    if args.prepare_only:
        report['status'] = 'prepared_no_aws_calls'
        for case in cases:
            subset = [b for b in books if eligible(b, case.get('filters', {}))]
            report['results'].append(compact({**case,'lexical':lexical(subset,case['query'])[:10]}))
    else:
        provider = Bedrock(args.region, args.output/'embedding-cache')
        try:
            # Small query probe first: fail without processing all 4202 books if access is blocked.
            provider.embed(cases[0]['query'], 'search_query')
            vectors = {}
            for i,b in enumerate(books):
                vectors[b['id']] = provider.embed(document(b), 'search_document')
                if (i+1) % 100 == 0:
                    print(f'Embedded {i+1}/{len(books)}', flush=True)
            for case in cases:
                report['results'].append(compact(search(books, vectors, case, provider)))
            report['status'] = 'completed' if all(r['status']=='ok' for r in report['results']) else 'completed_with_failures'
        except Exception as exc:
            report.update(status='blocked', error=str(exc))
        report.update(embedding_calls=provider.calls, embedding_cache_hits=provider.hits)
    path = args.output/('prepared.json' if args.prepare_only else 'comparison.json')
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'status':report['status'],'books':len(books),'report':str(path)},ensure_ascii=False))
    if report['status'] in ('blocked', 'completed_with_failures'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
