"""Local, read-only agent search loop with an injectable decision function.

A planner receives the request and tool observations and returns one action:
search, inspect, facets, or finish. No model provider or network is required.
Hard filters are held by the executor and cannot be silently relaxed by a planner.
"""
from dataclasses import dataclass, asdict
import sqlite3

@dataclass(frozen=True)
class Constraints:
    author: str = ''
    topic: str = ''
    genre: str = ''
    min_pages: int | None = None
    max_pages: int | None = None
    level: str = ''

class SearchTools:
    def __init__(self, path, constraints=Constraints()):
        self.db = sqlite3.connect(f'{path.resolve().as_uri()}?mode=ro', uri=True)
        self.db.row_factory = sqlite3.Row
        self.constraints = constraints
        if constraints.min_pages is not None and constraints.min_pages < 1:
            raise ValueError('invalid minimum pages')
        if constraints.max_pages is not None and constraints.max_pages < 1:
            raise ValueError('invalid maximum pages')
        if constraints.min_pages and constraints.max_pages and constraints.min_pages > constraints.max_pages:
            raise ValueError('inverted page range')
        if constraints.level not in ('', 'beginner', 'intermediate', 'advanced'):
            raise ValueError('invalid level')
        self.eligible = set()

    def close(self):
        self.db.close()

    def search(self, query='', topic=''):
        if not isinstance(query, str) or len(query) > 1000 or not isinstance(topic, str):
            raise ValueError('invalid search input')
        clauses, args = ['1=1'], []
        for term in query.split():
            escaped = term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            clauses.append("(b.title LIKE ? ESCAPE '\\' OR b.authors LIKE ? ESCAPE '\\')")
            args.extend([f'%{escaped}%', f'%{escaped}%'])
        if self.constraints.author:
            clauses.append('b.authors=?'); args.append(self.constraints.author)
        for selected in set(filter(None, [topic, self.constraints.topic, self.constraints.genre])):
            clauses.append('EXISTS (SELECT 1 FROM book_topics t WHERE t.book_id=b.id AND t.topic_id=?)')
            args.append(selected)
        for field, op, value in [('page_count', '>=', self.constraints.min_pages), ('page_count', '<=', self.constraints.max_pages), ('level', '=', self.constraints.level or None)]:
            if value is not None:
                clauses.append(f'EXISTS (SELECT 1 FROM book_discovery d WHERE d.book_id=b.id AND d.{field}{op}?)')
                args.append(value)
        where = ' AND '.join(clauses)
        total = self.db.execute('SELECT count(*) FROM books b WHERE ' + where, args).fetchone()[0]
        books = [dict(row) for row in self.db.execute('SELECT b.id,b.title,b.authors FROM books b WHERE ' + where + ' ORDER BY b.id LIMIT 20', args)]
        self.eligible.update(book['id'] for book in books)
        return {'books': books, 'total': total}

    def inspect(self, book_id):
        if type(book_id) is not int or book_id not in self.eligible:
            raise ValueError('inspect requires an ID returned under the hard filters')
        book = dict(self.db.execute('SELECT id,title,authors,class_number FROM books WHERE id=?', (book_id,)).fetchone())
        book['topics'] = [dict(row) for row in self.db.execute('SELECT topic_id,evidence FROM book_topics WHERE book_id=?', (book_id,))]
        row = self.db.execute('SELECT page_count,level,evidence FROM book_discovery WHERE book_id=?', (book_id,)).fetchone()
        book['metadata'] = dict(row) if row else None
        return book

    def facets(self):
        return {'topics': [dict(row) for row in self.db.execute('SELECT topic_id,count(*) AS count FROM book_topics GROUP BY topic_id')],
                'coverage': dict(self.db.execute('SELECT count(*) AS books, count(page_count) AS page_count, count(level) AS level FROM book_discovery').fetchone())}


def run_search(question, tools, planner, max_steps=6):
    """Run bounded adaptive retrieval. Planner calls must enforce their own timeout.

    Returned evidence is loaded from the catalog; planner-authored factual
    reasons are intentionally not accepted as grounded search results.
    """
    if not 1 <= max_steps <= 12:
        raise ValueError('invalid step budget')
    trace, inspected = [], {}
    for _ in range(max_steps):
        action = planner({'question': question, 'constraints': asdict(tools.constraints), 'trace': trace[:]})
        if not isinstance(action, dict):
            raise ValueError('planner must return an action object')
        name, args = action.get('tool'), action.get('args', {})
        if not isinstance(args, dict):
            raise ValueError('tool arguments must be an object')
        if name == 'finish':
            ids = args.get('book_ids', [])
            if not isinstance(ids, list) or len(ids) > 10 or any(type(ident) is not int or ident not in inspected for ident in ids):
                raise ValueError('finish may only cite inspected, eligible books')
            return {'status': 'complete', 'books': [inspected[ident] for ident in dict.fromkeys(ids)], 'trace': trace}
        functions = {'search': tools.search, 'inspect': tools.inspect, 'facets': tools.facets}
        if name not in functions:
            raise ValueError('unknown tool')
        try:
            result = functions[name](**args)
            if name == 'inspect':
                inspected[result['id']] = result
        except (TypeError, ValueError) as error:
            result = {'error': str(error)}
        trace.append({'tool': name, 'args': args, 'result': result})
    return {'status': 'budget_exhausted', 'books': [], 'trace': trace}
