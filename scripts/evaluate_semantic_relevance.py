# /// script
# requires-python = ">=3.12"
# dependencies = ["boto3>=1.40,<2", "numpy>=2,<3"]
# ///
"""Measure rerank relevance on the generated catalog; sends query/candidate text to Bedrock."""
import argparse
import hashlib
import json
from pathlib import Path
import time

import numpy as np
import boto3
from botocore.config import Config

from evaluate_semantic_search import Bedrock, RERANK, read_catalog

CASES = [
    ('ユーザビリティ', 'UI/UXの使いやすさ'),
    ('データクレンジング', 'データの前処理'),
    ('リクルーティング', '採用する側の実務。就活や企業史とは区別'),
    ('英文読解', '英文の読み方'),
    ('Pythonでデータを分析したい', 'Pythonによる分析'),
    ('使いやすいアプリの画面を設計したい', '画面のUI設計。単なるアプリ実装とは区別'),
    ('UI デザイン', '画面のUI設計'),
    ('C++', 'C++言語'),
    ('離乳食', '離乳食そのもの。一般栄養とは区別'),
    ('zxqv987qqq', '無意味な語なので候補なし'),
    ('ワープエンジンの修理手順', '実在する修理教材なし'),
    ('ドラゴンの飼育方法', '実在する飼育教材なし'),
]


def rerank_document(book):
    return (f"書名: {book['title'] or ''}\n著者: {book['authors'] or ''}\n内容紹介: {book['description'] or ''}")[:2000]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', type=Path, default=Path('outputs/semantic-serving/index.json'))
    parser.add_argument('--db', type=Path, default=Path('outputs/semantic-serving/library.db'))
    parser.add_argument('--output', type=Path, default=Path('outputs/semantic-relevance'))
    parser.add_argument('--interval', type=float, default=25, help='Seconds between rerank calls; check the applied inference quota')
    parser.add_argument('--rerank-region', default='ap-northeast-1')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    index = json.loads(args.index.read_text())
    assert index['catalog_sha256'] == hashlib.sha256(args.db.read_bytes()).hexdigest()
    books = {b['id']: b for b in read_catalog(args.db)}
    vectors = np.asarray([b['vector'] for b in index['books']], dtype=np.float64)
    provider = Bedrock('ap-northeast-1', Path('outputs/semantic-serving/embedding-cache'))
    provider.rank_client = boto3.client('bedrock-agent-runtime', region_name=args.rerank_region, config=Config(connect_timeout=3, read_timeout=15, retries={'total_max_attempts': 1}))
    report_path = args.output / 'comparison.json'
    manifest_path = args.output / 'manifest.json'
    manifest = {'catalog_sha256': index['catalog_sha256'], 'rerank_model': RERANK, 'rerank_region': args.rerank_region, 'document_format': 'title-authors-description-2000-characters', 'candidate_limit': 20}
    if manifest_path.exists() and json.loads(manifest_path.read_text()) != manifest:
        raise ValueError('Catalog/model/config changed; use a new output directory')
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
    report = json.loads(report_path.read_text()) if report_path.exists() else []
    for query, purpose in CASES:
        if any(row['query'] == query for row in report):
            continue
        time.sleep(args.interval)
        started = time.monotonic()
        vector = np.asarray(provider.embed(query, 'search_query'))
        scores = vectors @ vector
        positions = sorted(range(len(scores)), key=lambda i: (-scores[i], index['books'][i]['id']))[:20]
        candidates = [books[index['books'][i]['id']] for i in positions]
        response = provider.rank_client.rerank(
            queries=[{'type': 'TEXT', 'textQuery': {'text': query}}],
            sources=[{'type': 'INLINE', 'inlineDocumentSource': {'type': 'TEXT', 'textDocument': {'text': rerank_document(b)}}} for b in candidates],
            rerankingConfiguration={'type': 'BEDROCK_RERANKING_MODEL', 'bedrockRerankingConfiguration': {'modelConfiguration': {'modelArn': f'arn:aws:bedrock:{args.rerank_region}::foundation-model/{RERANK}'}, 'numberOfResults': len(candidates)}},
        )
        assert sorted(r['index'] for r in response['results']) == list(range(len(candidates)))
        rows = [{
            'id': candidates[r['index']]['id'], 'title': candidates[r['index']]['title'],
            'description': candidates[r['index']]['description'],
            'cosine': float(scores[positions[r['index']]]), 'relevance': r['relevanceScore'],
        } for r in response['results']]
        result = {'query': query, 'purpose_fixed_before_rerank': purpose, 'rerank_region': args.rerank_region, 'milliseconds': round((time.monotonic() - started) * 1000), 'books': rows}
        report.append(result)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        print(query, [(b['title'], round(b['relevance'], 5), round(b['cosine'], 4)) for b in rows[:7]], flush=True)


if __name__ == '__main__':
    main()
