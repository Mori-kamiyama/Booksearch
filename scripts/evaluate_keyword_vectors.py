"""Bounded 30-book keyword experiment; reuses the earlier embedding cache.
Run from repository root with uv run --no-project --with boto3 python ...
Relevance IDs and synonym mappings are fixed before reading vector results.
"""
import json
from pathlib import Path
import time
from evaluate_semantic_search import Bedrock, read_catalog, document, lexical
from discovery_genres import normalize_search_text

CASES = [
    ('ユーザビリティ', [1462,1499,2492], ['UIデザイン']),
    ('データクレンジング', [153,195], ['データ分析']),
    ('スタートアップ', [1,9], ['起業']),
    ('リクルーティング', [7], ['採用']),
    ('英文読解', [603], ['英文解釈']),
    ('音声操作', [2630], ['ボイスユーザーインターフェース']),
    ('発音', [573,694], ['音声学']),
    ('Python', [153,179,183,187,195,196,293,298], []),
    ('離乳食', [], []),
    ('zxqv987qqq', [], []),
]


def main():
    out=Path('outputs/semantic-small')
    prior=json.loads((out/'comparison.json').read_text())
    all_books={b['id']:b for b in read_catalog(Path('outputs/discovery-language/library.db'))}
    books=[all_books[i] for i in prior['book_ids']]
    provider=Bedrock('ap-northeast-1',out/'embedding-cache')
    # Require previously cached documents. This experiment sends only ten new queries.
    import hashlib
    vectors={}
    for b in books:
        key=hashlib.sha256(json.dumps(['cohere.embed-multilingual-v3','search_document',document(b)],ensure_ascii=False).encode()).hexdigest()
        if not (provider.cache/f'{key}.json').exists():
            raise RuntimeError('Missing document cache; stop instead of uploading additional books')
        vectors[b['id']]=provider.embed(document(b),'search_document')
    results=[]
    for query,expected,aliases in CASES:
        started=time.perf_counter()
        baseline=lexical(books,query)
        syn={b['id']:b for term in [query,*aliases] for b in lexical(books,term)}
        lexical_ms=(time.perf_counter()-started)*1000
        qnorm=normalize_search_text(query)
        literal={b['id'] for b in books if qnorm in normalize_search_text((b['title'] or '')+' '+(b['description'] or ''))}
        hits_before=provider.hits
        started=time.perf_counter()
        q=provider.embed(query,'search_query')
        embedding_ms=(time.perf_counter()-started)*1000
        started=time.perf_counter()
        ranking=sorted([(sum(x*y for x,y in zip(q,vectors[b['id']])),b) for b in books],key=lambda v:-v[0])
        local_ms=(time.perf_counter()-started)*1000
        row={'query':query,'expected_ids':expected,'synonyms':aliases,'lexical_ids':[b['id'] for b in baseline],
             'synonym_ids':list(syn),'literal_title_description_ids':sorted(literal),
             'embedding_cached':provider.hits>hits_before,
             'timings_ms':{'lexical_plus_synonyms':round(lexical_ms,2),'embedding':round(embedding_ms,2),'cosine':round(local_ms,2)},
             'vector_top3':[{'id':b['id'],'title':b['title'],'cosine':round(score,4),'relevant':b['id'] in expected,
                            'query_absent_from_title_description':b['id'] not in literal} for score,b in ranking[:3]]}
        results.append(row)
        print(json.dumps(row,ensure_ascii=False),flush=True)
    (out/'keyword-comparison.json').write_text(json.dumps({'evaluation':'Assistant labels based on catalog descriptions; not independent human judgments. Same deliberately selected 30 books for all methods. Synonyms manually supplied for this experiment, not shipped. No calibrated threshold.','results':results,'embedding_calls':provider.calls},ensure_ascii=False,indent=2)+'\n')

if __name__=='__main__':main()
