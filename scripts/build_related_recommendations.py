"""Precompute content-based related books; never modifies weekly featured books.

Uses normalized TF-IDF content similarity, verified themes and author diversity.
No network or LLM. Completed empty rankings deliberately suppress old fallbacks.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sqlite3
import unicodedata
from functools import lru_cache
from janome.tokenizer import Tokenizer

from build_bm25_recommendations import create_schema
from import_cached_metadata import canonical_isbn
from recommendation_utils import normalize_text, class_similarity

STRATEGY = 'content-diverse-v2'


STOP_WORDS = frozenset('本書 入門 基礎 基本 教科書 講義 ビジネス 解説 紹介 方法 内容 ため こと もの これ それ 自分 私たち 一つ 一冊 日本 世界 人生 時代 実践 活用 理解 the and for with from this that into'.split())


@lru_cache(maxsize=1)
def tokenizer():
    return Tokenizer()


@lru_cache(maxsize=15000)
def informative_tokens(text):
    text = re.split(r'(?:発行|発行元|発売元)\s*[:：]|〜参加著者〜', text or '')[0]
    text = unicodedata.normalize('NFKC', text).lower()
    words = []
    for token in tokenizer().tokenize(text):
        pos = token.part_of_speech.split(',')
        word = token.base_form if token.base_form != '*' else token.surface
        if pos[0] != '名詞' or pos[1] in ('非自立','代名詞','数','接尾'): continue
        if len(word) < 2 or word in STOP_WORDS or re.fullmatch(r'[ぁ-ゖー]+', word): continue
        words.append(word)
    return words


class RelatedIndex:
    def __init__(self, books, topics):
        self.books = books
        self.topics = topics
        self.authors = [normalize_text(b['authors']) for b in books]
        self.titles = [normalize_text(b['title']) for b in books]
        self.isbns = [canonical_isbn(b.get('isbn')) for b in books]
        frequencies = []
        for book in books:
            tokens = informative_tokens(book['title'])*5
            tokens += informative_tokens(book.get('description') or '')
            tokens += informative_tokens(book.get('categories_json') or '')*2
            frequencies.append(Counter(tokens))
        df = Counter(t for f in frequencies for t in f)
        self.vectors = []
        self.postings = defaultdict(list)
        for i, frequency in enumerate(frequencies):
            vector = {t:(1+math.log(n))*math.log(1+len(books)/df[t]) for t,n in frequency.items()
                      if df[t] <= max(10, len(books)*0.15)}
            length = math.sqrt(sum(v*v for v in vector.values())) or 1
            vector = {t:v/length for t,v in vector.items()}
            self.vectors.append(vector)
            for t,v in vector.items(): self.postings[t].append((i,v))

    def similar(self, i, j):
        left, right = self.vectors[i], self.vectors[j]
        if len(left) > len(right): left, right = right, left
        return sum(v*right.get(t,0) for t,v in left.items())

    def rank(self, source_index, top_k=6):
        source = self.books[source_index]
        scores, overlaps = defaultdict(float), Counter()
        for token, value in self.vectors[source_index].items():
            for j, weight in self.postings[token]:
                scores[j] += value*weight
                overlaps[j] += 1
        candidates = []
        for j, cosine in scores.items():
            candidate = self.books[j]
            if j == source_index or self.titles[j] == self.titles[source_index]: continue
            if self.isbns[j] and self.isbns[j] == self.isbns[source_index]: continue
            shared = self.topics.get(source['id'], set()) & self.topics.get(candidate['id'], set())
            same_author = bool(self.authors[j] and self.authors[j] == self.authors[source_index])
            classification = class_similarity(source.get('class_number'), candidate.get('class_number'))
            context = bool(shared or same_author or classification >= .55
                           or (classification >= .30 and cosine >= .20 and overlaps[j] >= 2))
            # Cross-field recommendations need much stronger content evidence.
            if not context and not (source.get('description') and candidate.get('description') and cosine >= .22 and overlaps[j] >= 4): continue
            if not ((cosine >= .10 and overlaps[j] >= 1) or (shared and cosine >= .025)
                    or (same_author and classification >= .55 and cosine >= .04)): continue
            reasons = []
            if shared: reasons.append('同じテーマの本')
            if cosine >= .10: reasons.append('内容のキーワードが近い本')
            if same_author: reasons.append('同じ著者の作品')
            if classification >= .55: reasons.append('分類が近い本')
            score = cosine + min(len(shared),2)*.12 + same_author*.06 + classification*.035
            candidates.append((j, score, reasons[:3], cosine))
        candidates.sort(key=lambda item:(-item[1], self.books[item[0]]['id']))
        candidates = candidates[:100]
        selected, seen_titles, seen_isbns, author_counts = [], set(), set(), Counter()
        while candidates and len(selected) < top_k:
            def adjusted(item):
                j, score, _, _ = item
                redundancy = max((self.similar(j, prior[0]) for prior in selected), default=0)
                return score - redundancy*.12
            winner = max(candidates, key=lambda item:(adjusted(item), -self.books[item[0]]['id']))
            candidates.remove(winner)
            j = winner[0]
            if self.titles[j] in seen_titles or (self.isbns[j] and self.isbns[j] in seen_isbns): continue
            if self.authors[j] and author_counts[self.authors[j]] >= 2: continue
            selected.append(winner)
            seen_titles.add(self.titles[j])
            if self.isbns[j]: seen_isbns.add(self.isbns[j])
            if self.authors[j]: author_counts[self.authors[j]] += 1
        return selected


def build(path, report_path=None):
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        books = [dict(r) for r in db.execute('''SELECT b.id,b.title,b.authors,b.isbn,b.class_number,
          COALESCE(m.description,'') description, COALESCE(m.categories_json,'[]') categories_json
          FROM books b LEFT JOIN book_metadata m ON m.book_id=b.id AND m.fetch_status='matched' ORDER BY b.id''')]
        topics = defaultdict(set)
        for row in db.execute("SELECT book_id,topic_id FROM book_topics WHERE topic_id NOT LIKE 'ndc-%' AND topic_id NOT LIKE 'genre-%'"):
            topics[row[0]].add(row[1])
        index = RelatedIndex(books, topics)
        # Evaluate a deterministic, class-stratified sample against the saved baseline.
        sample_ids, per_class = [], Counter()
        for book in books:
            group = str(book['class_number'] or '?')[:1]
            if per_class[group] < 3:
                sample_ids.append(book['id'])
                per_class[group] += 1
        create_schema(db)
        db.execute('''CREATE TABLE IF NOT EXISTS book_recommendation_runs(
            source_book_id INTEGER, strategy TEXT, generated_at TEXT,
            PRIMARY KEY(source_book_id,strategy))''')
        db.execute('DELETE FROM book_recommendations WHERE strategy=?', (STRATEGY,))
        db.execute('DELETE FROM book_recommendation_runs WHERE strategy=?', (STRATEGY,))
        timestamp = datetime.now(timezone.utc).isoformat()
        counts, sample = Counter(), []
        for i, book in enumerate(books):
            ranked = index.rank(i)
            # Store rank scores to preserve the greedy diversity ordering through SQL.
            db.executemany('INSERT INTO book_recommendations VALUES(?,?,?,?,?,?)',
              [(book['id'], books[j]['id'], len(ranked)-rank, STRATEGY, json.dumps(reasons, ensure_ascii=False), timestamp)
               for rank,(j,score,reasons,cosine) in enumerate(ranked)])
            db.execute('INSERT INTO book_recommendation_runs VALUES(?,?,?)', (book['id'],STRATEGY,timestamp))
            counts['books'] += 1
            counts['with_related'] += bool(ranked)
            counts['recommendations'] += len(ranked)
            if book['id'] in sample_ids:
                previous = [dict(r) for r in db.execute("SELECT b.id,b.title,r.reasons_json FROM book_recommendations r JOIN books b ON b.id=r.recommended_book_id WHERE source_book_id=? AND strategy='bm25-keyword-v1' ORDER BY score DESC,recommended_book_id LIMIT 6",(book['id'],))]
                sample.append({'source':{'id':book['id'],'title':book['title'],'class_number':book['class_number']},
                               'before':previous, 'after':[{'id':books[j]['id'],'title':books[j]['title'], 'authors':books[j]['authors'], 'reasons':reasons, 'cosine':round(cosine,4)} for j,score,reasons,cosine in ranked]})
        if report_path:
            Path(report_path).parent.mkdir(parents=True, exist_ok=True)
            Path(report_path).write_text(json.dumps({'strategy':STRATEGY,'counts':counts,'samples':sample}, ensure_ascii=False, indent=2))
        return dict(counts)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    if not args.db.is_file(): parser.error('DB does not exist')
    print(json.dumps(build(args.db, args.report)))
