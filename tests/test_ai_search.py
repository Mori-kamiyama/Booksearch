import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ai_search import CatalogSearch, run


def book(i,title='英文法',pages=100,description='学習者が自分で文法を基礎から学ぶための本です。'):
    return dict(id=i,title=title,authors='著者',terms=[title],page_count=pages,level=None,description=description)


def test_constraints_intersect_and_cannot_relax():
    c=CatalogSearch([book(1),book(2,pages=500),book(3,pages=None)],max_pages=200)
    assert [b['id'] for b in c.search(['英文法'],max_pages=1000)['books']]==[1]
    assert c.max_pages==200
    with pytest.raises(ValueError):c.search(['英文法'],max_pages=500)
    with pytest.raises(ValueError):c.inspect([2])
    assert c.search(['文法'])['total']==1


def test_description_search_and_symbols():
    c=CatalogSearch([book(1,title='C++',description='プログラミングの基礎を学べる本です。'),book(2,title='C#')])
    assert [b['id'] for b in c.search(['C++'])['books']]==[1]
    assert c.search(['プログラミング 基礎'])['total']==1
    with pytest.raises(ValueError):c.search(['???'])


def test_inspection_and_literal_evidence_required():
    c=CatalogSearch([book(1)])
    with pytest.raises(ValueError):c.finish([])
    c.search(['英文法'])
    with pytest.raises(ValueError):c.finish([{'book_id':1,'evidence_id':0}])
    c.inspect([1])
    with pytest.raises(ValueError):c.finish([{'book_id':1,'evidence_id':999}])
    result=c.finish([{'book_id':1,'evidence_id':0}])
    assert result['books'][0]['id']==1


def test_unknown_pages_excluded_but_no_filter_keeps_them():
    c=CatalogSearch([book(1,pages=None)])
    assert c.search(['英文法'])['total']==1
    c=CatalogSearch([book(1,pages=None)])
    assert c.search(['英文法'],min_pages=1)['total']==0


def call(name,args):
    return {'role':'assistant','content':[{'toolUse':{'toolUseId':'test','name':name,'input':args}}]}


def test_agent_search_inspect_finish():
    actions=iter([call('search',{'queries':['英文法']}),call('inspect',{'book_ids':[1]}),
                  call('finish',{'selections':[{'book_id':1,'evidence_id':0}]})])
    result=run('文法を学びたい',CatalogSearch([book(1)]),lambda _:next(actions))
    assert result['status']=='complete' and result['model_calls']==3


def test_failed_tool_can_be_corrected():
    actions=iter([call('inspect',{'book_ids':[999]}),call('search',{'queries':['存在しない']}),call('finish',{'selections':[]})])
    seen=[]
    def planner(messages):
        seen.append(messages[:])
        return next(actions)
    result=run('存在しない',CatalogSearch([book(1)]),planner)
    assert result['books']==[] and result['status']=='complete'
    assert seen[1][-1]['content'][0]['toolResult']['status']=='error'


def test_budget_not_reported_as_no_results():
    result=run('文法',CatalogSearch([book(1)]),lambda _:call('search',{'queries':['文法']}),max_steps=2)
    assert result['status']=='incomplete'


def test_invalid_request_and_filter_types():
    for value in [True,0,-1,'200',1.5]:
        with pytest.raises(ValueError):CatalogSearch([],max_pages=value)
    for question in ['',None,'x'*1001]:
        with pytest.raises(ValueError):run(question,CatalogSearch([]),lambda _:None)


def test_multiple_finish_calls_rejected():
    c=CatalogSearch([book(1)]);c.search(['文法']);c.inspect([1])
    multi=call('finish',{'selections':[]});multi['content']*=2
    result=run('文法',c,lambda _:multi,max_steps=1)
    assert result['status']=='incomplete'


def test_natural_language_page_bounds_are_enforced_without_model_input():
    c=CatalogSearch([book(1,pages=200),book(2,pages=301),book(3,pages=None)])
    actions=iter([call('search',{'queries':['文法']}),call('finish',{'selections':[]})])
    r=run('文法 ３００ページ以下',c,lambda _:next(actions))
    assert r['constraints']['max_pages']==300
    assert c.eligible=={1}


def test_model_cannot_invent_page_limits():
    c=CatalogSearch([book(1,pages=None)])
    actions=iter([call('search',{'queries':['文法'],'min_pages':1}),call('search',{'queries':['文法']}),call('finish',{'selections':[]})])
    run('文法',c,lambda _:next(actions))
    assert c.min_pages is None and c.eligible=={1}


def test_promotional_copy_not_offered_as_evidence():
    c=CatalogSearch([book(1,description='おかげさまで8万部突破！学習者が自分で文法を基礎から学ぶための本です。')])
    c.search(['文法'])
    detail=c.inspect([1])['books'][0]
    assert len(detail['evidence'])==1
    assert detail['evidence'][0]['text'].startswith('学習者')


def test_text_only_model_reply_is_retried_within_budget():
    actions=iter([{'role':'assistant','content':[{'text':'見つかりません'}]},
                  call('search',{'queries':['存在しない']}),call('finish',{'selections':[]})])
    r=run('存在しない',CatalogSearch([book(1)]),lambda _:next(actions))
    assert r['status']=='complete' and r['books']==[] and r['model_calls']==3


def test_conversation_context_and_constraints_carry_forward():
    seen=[]
    actions=iter([call('search',{'queries':['文法']}),call('finish',{'selections':[]})])
    def planner(messages):
        seen.append(messages[0]['content'][0]['text'])
        return next(actions)
    c=CatalogSearch([book(1),book(2,pages=500)])
    r=run('初心者向けにして',c,planner,history=[{'question':'英文法の本','reply':'候補です','book_ids':[1]}],constraints={'min_pages':None,'max_pages':200})
    import json
    context=json.loads(seen[0])
    assert context['conversation'][0]['books'][0]['title']=='英文法'
    assert r['constraints']['max_pages']==200 and c.eligible=={1}


def test_page_limit_can_change_or_be_cleared_by_user():
    def planner(_): return call('search',{'queries':['文法']})
    c=CatalogSearch([book(1)])
    run('500ページ以下にして',c,planner,max_steps=1,constraints={'max_pages':200})
    assert c.max_pages==500
    c=CatalogSearch([book(1)])
    run('ページ数の制限はなしで',c,planner,max_steps=1,constraints={'max_pages':200})
    assert c.max_pages is None
    c=CatalogSearch([book(1)])
    run('探し直して',c,planner,max_steps=1,history=[{'question':'200ページ以下','reply':'候補','book_ids':[]}],constraints={'max_pages':None})
    assert c.max_pages is None


def test_clarification_returns_no_invented_books():
    r=run('もっと薄い本',CatalogSearch([book(1)]),lambda _:call('clarify',{'question':'何ページ以下がよいですか？','options':['200ページ以下','300ページ以下']}))
    assert r['status']=='clarification' and r['books']==[]
    assert r['options']==['200ページ以下','300ページ以下']


def test_invalid_history_is_rejected():
    for history in [{},[{'question':'文法','reply':'候補','book_ids':[999]}],
                    [{'question':'文法','reply':'候補','book_ids':[]}]*9]:
        with pytest.raises(ValueError):run('文法',CatalogSearch([book(1)]),lambda _:None,history=history)


def test_answered_page_question_is_not_repeated_and_page_words_not_searched():
    c=CatalogSearch([book(1)])
    actions=iter([call('clarify',{'question':'何ページ以下？','options':[]}),
                  call('search',{'queries':['文法 200ページ以下']}),call('finish',{'selections':[]})])
    result=run('200ページ以下にして',c,lambda _:next(actions))
    assert result['status']=='complete' and c.eligible=={1}
    assert result['searches'][0]['queries']==['文法']


def test_zero_results_broaden_keywords_without_relaxing_pages():
    c=CatalogSearch([book(1,title='Python データ分析',pages=150),book(2,title='Python データ分析',pages=400)],max_pages=200)
    result=c.search(['Python データ分析 入門'])
    assert [b['id'] for b in result['books']]==[1]
    assert c.max_pages==200 and c.trace[0]['total']==0


def test_free_answer_changes_direction_without_forcing_pages():
    c=CatalogSearch([book(1)])
    actions=iter([call('clarify',{'question':'何ページ以下ですか？','options':['100ページ以下']}),
        call('search',{'queries':['文法']}),call('inspect',{'book_ids':[1]}),call('finish',{'selections':[{'book_id':1,'evidence_id':0}],
            'focus':'文法をやさしく学びたい','presentation':'cards','follow_up':'図解と練習問題ならどちらを重視しますか？','options':['図解','練習問題']})])
    r=run('簡単めがいい',c,lambda _:next(actions),history=[{'question':'英語の本','reply':'何ページ以下ですか？','book_ids':[]}],constraints={'max_pages':300})
    assert r['status']=='complete' and r['constraints']['max_pages']==300
    assert r['follow_up'] and r['books'][0]['id']==1


def test_comparison_only_contains_inspected_catalog_evidence():
    c=CatalogSearch([book(1)])
    c.search(['文法']);c.inspect([1])
    r=c.finish([{'book_id':1,'evidence_id':0}],presentation='comparison',focus='内容を比較')
    assert r['presentation']=='comparison' and r['books'][0]['page_count']==100
    with pytest.raises(ValueError):c.finish([],presentation='<script>')
    with pytest.raises(ValueError):c.finish([],options=['選択肢'],follow_up='')


def test_page_indifference_clears_hard_limit():
    c=CatalogSearch([book(1)])
    run('ページ数は気にしない。簡単めで',c,lambda _:call('search',{'queries':['文法']}),max_steps=1,constraints={'max_pages':300})
    assert c.max_pages is None
