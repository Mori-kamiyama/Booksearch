import importlib.util
import sqlite3
import sys
from pathlib import Path
import pytest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from build_discovery_index import build
from discovery_agent import SearchTools, Constraints, run_search

@pytest.fixture
def catalog(tmp_path):
    source, output = tmp_path/'source.db', tmp_path/'output.db'
    db = sqlite3.connect(source)
    db.executescript('''CREATE TABLE books(id INTEGER,title TEXT,authors TEXT,class_number TEXT);
    INSERT INTO books VALUES(1,'C言語入門','田中','007'),(2,'C++入門','田中','007'),(3,'ブルータリズム建築','佐藤','520');
    CREATE TABLE book_metadata(book_id INTEGER,fetch_status TEXT,page_count INTEGER,source TEXT,source_id TEXT);
    INSERT INTO book_metadata VALUES(1,'matched',180,'catalog','1'),(2,'ambiguous',100,'catalog','2');''')
    db.close()
    before = source.read_bytes()
    coverage = build(source, output, tmp_path/'index.json')
    assert source.read_bytes() == before
    return source, output, coverage

def test_index_preserves_unknowns_and_excludes_ambiguous_metadata(catalog):
    source, output, coverage = catalog
    assert coverage == {'books': 3, 'page_count': 1, 'level': 0}
    db = sqlite3.connect(output)
    assert db.execute("SELECT book_id FROM book_topics WHERE topic_id='c-language'").fetchall() == [(1,)]
    assert db.execute('SELECT page_count,level FROM book_discovery WHERE book_id=2').fetchone() == (None, None)
    with pytest.raises(ValueError): build(source, source, source.parent/'bad.json')

def test_agent_refines_after_empty_search_and_grounds_results(catalog):
    tools = SearchTools(catalog[1], Constraints(topic='c-language', max_pages=200))
    def planner(state):
        trace = state['trace']
        if not trace: return {'tool':'search','args':{'query':'初心者向け'}}
        if len(trace)==1:
            assert trace[-1]['result']['total']==0
            return {'tool':'search','args':{}}
        if len(trace)==2: return {'tool':'inspect','args':{'book_id': trace[-1]['result']['books'][0]['id']}}
        return {'tool':'finish','args':{'book_ids':[1]}}
    try:
        result = run_search('薄いC言語の本', tools, planner)
        assert result['status']=='complete'
        assert result['books'][0]['metadata']['page_count']==180
        assert result['books'][0]['topics'][0]['evidence']
    finally: tools.close()

def test_agent_cannot_relax_filters_or_invent_books(catalog):
    tools = SearchTools(catalog[1], Constraints(topic='c-language', max_pages=100))
    assert tools.search()['total']==0
    with pytest.raises(ValueError): tools.inspect(1)
    with pytest.raises(ValueError): run_search('request',tools,lambda _: {'tool':'finish','args':{'book_ids':[1]}})
    result=run_search('request', tools, lambda _: {'tool':'search','args':{'max_pages':999}}, max_steps=2)
    assert result['status']=='budget_exhausted'
    assert all('error' in step['result'] for step in result['trace'])
    tools.close()
