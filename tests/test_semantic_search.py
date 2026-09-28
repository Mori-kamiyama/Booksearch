import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from evaluate_semantic_search import eligible, fuse, search, unit, document, lexical, Bedrock


def book(i,title='Python',pages=100,level=None):
    return dict(id=i,title=title,authors='著者',isbn=str(i),page_count=pages,level=level,
                topics=['genre-it'],terms=[title.lower()],description='内容紹介')

class Provider:
    def __init__(self,fail=''): self.fail,self.seen=fail,[]
    def embed(self,text,kind):
        if self.fail=='embed': raise TimeoutError('embedding timeout')
        return [1,0]
    def rerank(self,query,candidates):
        self.seen=candidates
        if self.fail=='rerank': raise TimeoutError('rerank timeout')
        return candidates


def test_filters_before_retrieval_and_rerank():
    books=[book(1,pages=120,level='beginner'),book(2,pages=None),book(3,pages=500,level='beginner'),book(4,level='advanced')]
    p=Provider()
    r=search(books,{b['id']:[1,0] for b in books},{'query':'Python','filters':{'max_pages':200,'level':'beginner'}},p)
    assert [b['id'] for b in p.seen]==[1]
    assert [b['id'] for b in r['reranked']]==[1]
    assert not eligible(books[0],{'topic':'english-learning'})
    with pytest.raises(ValueError): eligible(books[0],{'unknown':1})


def test_fusion_exact_and_deduplication():
    a,b=book(1),book(2,'関連書籍')
    for query in ['Python','1']:
        assert [x['id'] for x in fuse([a,b],[b,a],query)]==[1,2]


def test_explicit_failures_keep_fallback_results():
    books=[book(1)]
    r=search(books,{1:[1,0]},{'query':'Python'},Provider('embed'))
    assert r['status']=='embedding_failed_keyword_fallback' and r['reranked']==books
    r=search(books,{1:[1,0]},{'query':'Python'},Provider('rerank'))
    assert r['status']=='rerank_failed_hybrid_fallback' and r['reranked']==books


def test_invalid_vectors_and_dimension_mismatch():
    for v in ([],[0,0],[float('nan')],[float('inf')]):
        with pytest.raises(ValueError): unit(v)
    r=search([book(1)],{1:[1,0,0]},{'query':'Python'},Provider())
    assert r['status']=='embedding_failed_keyword_fallback'


def test_empty_filters_skip_models():
    r=search([book(1)],{1:[1,0]},{'query':'Python','filters':{'max_pages':50}},Provider('embed'))
    assert r['status']=='ok' and r['reranked']==[]


def test_symbols_and_document_limit():
    books=[book(1,'C++'),book(2,'C#')]
    assert [b['id'] for b in lexical(books,'C++')]==[1]
    assert lexical(books,'???')==[]
    books[0]['description']='長い内容紹介'*1000
    assert len(document(books[0]))==2000


def test_rerank_pins_exact_and_validates_indices():
    p=object.__new__(Bedrock)
    p.region='ap-northeast-1'
    class Client:
        results=[{'index':1},{'index':0}]
        def rerank(self,**kwargs): return {'results':self.results}
    p.rank_client=Client()
    assert p.rerank('Python',[book(1),book(2,'関連書籍')])[0]['id']==1
    p.rank_client.results=[{'index':0},{'index':0}]
    with pytest.raises(ValueError): p.rerank('Python',[book(1),book(2)])
