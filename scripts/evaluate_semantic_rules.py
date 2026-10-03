# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy>=2,<3"]
# ///
"""Evaluate the actual Go relevance rules with cached embeddings; no network/model calls."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

import numpy as np

from evaluate_semantic_search import MODEL, read_catalog

# Regression checks, not human relevance labels or an accuracy benchmark.
CASES = [
    {'query': 'ぷろぐらみんぐ入門', 'embedding_query': 'プログラミング入門'},
    {'query': 'LLM', 'embedding_query': '大規模言語モデル LLM'},
    {'query': 'ユーザビリティ', 'expected_first': 'ユーザビリティテスト実践ガイドブック'},
    {'query': 'データクレンジング', 'expected_first': '現場で使える!pandas (パンダス) データ前処理入門'},
    {'query': 'リクルーティング'},
    {'query': '英文読解'},
    {'query': 'Pythonでデータを分析したい', 'expected_first': 'いまさら聞けないPythonでデータ分析'},
    {'query': '使いやすいアプリの画面を設計したい', 'expected_first': 'UIデザインの教科書'},
    {'query': 'UI デザイン', 'expected_first': 'UIデザインの教科書'},
    {'query': 'C++'},
    {'query': '離乳食', 'empty': True},
    {'query': 'zxqv987qqq', 'empty': True},
    {'query': 'ワープエンジンの修理手順', 'empty': True},
    {'query': 'ドラゴンの飼育方法', 'empty': True},
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', type=Path, default=Path('outputs/semantic-serving/index.json'))
    parser.add_argument('--db', type=Path, default=Path('outputs/semantic-serving/library.db'))
    parser.add_argument('--cache', type=Path, default=Path('outputs/semantic-serving/embedding-cache'))
    parser.add_argument('--output', type=Path, default=Path('outputs/semantic-rules'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    index = json.loads(args.index.read_text())
    if index['model'] != MODEL or index['catalog_sha256'] != hashlib.sha256(args.db.read_bytes()).hexdigest():
        raise ValueError('Index/catalog/model mismatch')
    books = {b['id']: b for b in read_catalog(args.db)}
    vectors = np.asarray([b['vector'] for b in index['books']], dtype=np.float64)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    fixtures = []
    for case in CASES:
        key = hashlib.sha256(json.dumps([MODEL, 'search_query', case.get('embedding_query', case['query'])], ensure_ascii=False).encode()).hexdigest()
        # Missing embeddings fail explicitly; this script never creates new ones.
        query = np.asarray(json.loads((args.cache / f'{key}.json').read_text()))
        query /= np.linalg.norm(query)
        scores = vectors @ query
        positions = sorted(range(len(scores)), key=lambda i: (-scores[i], index['books'][i]['id']))[:50]
        candidates = [{'book': books[index['books'][i]['id']], 'cosine': float(scores[i])} for i in positions]
        fixtures.append({**case, 'candidates': candidates})
    fixture = (args.output / 'fixture.json').resolve()
    fixture.write_text(json.dumps(fixtures, ensure_ascii=False) + '\n')
    report = (args.output / 'report.json').resolve()
    env = {**os.environ, 'SEMANTIC_RULE_FIXTURE': str(fixture), 'SEMANTIC_RULE_REPORT': str(report)}
    result = subprocess.run(['go', 'test', './internal/db', '-run', '^TestSemanticRuleCatalogEvaluation$', '-count=1'], cwd='backend', env=env)
    for row in json.loads(report.read_text()):
        print(row['query'], [(b['book']['title'], round(b['cosine'], 3), round(b['coverage'], 2)) for b in row['books']], f"{row['microseconds']}us")
    raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
