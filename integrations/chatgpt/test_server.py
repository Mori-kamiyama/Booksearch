import asyncio
import httpx
import pytest
from server import Library, create_server, cover_url, WIDGET_URI


def test_tools_and_validation():
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={'books': [{'id': 1, 'title': 'Python', 'isbn': '123'}], 'total': 12})
    async def check():
        server = create_server(Library('http://api.test', 'https://books.test', transport=httpx.MockTransport(respond)))
        tools = await server.list_tools()
        assert {tool.name for tool in tools} == {'search_books', 'get_book', 'show_books'}
        assert all(tool.annotations.readOnlyHint and not tool.annotations.destructiveHint for tool in tools)
        await server.call_tool('search_books', {'query': 'Python', 'max_pages': 200})
        assert requests[0].url.params['max_pages'] == '200'
        for arguments in [{'query': ' '}, {'query': 'Python', 'limit': 11}, {'query': 'Python', 'min_pages': 300, 'max_pages': 200}]:
            with pytest.raises(Exception):
                await server.call_tool('search_books', arguments)
        with pytest.raises(Exception):
            await server.call_tool('get_book', {'book_id': '../jobs'})
        assert len(requests) == 1
    asyncio.run(check())


def test_enrichment_does_not_cross_catalogs():
    library = Library('http://api.test', 'https://books.test')
    library.metadata = {1: {'isbn': '123', 'title': 'Python', 'description': '確認済み紹介', 'page_count': 100}}
    result = library.book({'id': 1, 'title': 'Other', 'isbn': '999'}, True)
    assert result['description'] == '' and result['page_count'] is None
    result = library.book({'id': 1, 'title': 'Python', 'isbn': '123', 'shelf_candidates': [{'shelf_id': 'base/1', 'confidence': .4}]}, True)
    assert result['description'] == '確認済み紹介'
    assert result['url'] == 'https://books.test/books/1'
    assert result['shelf_candidates'][0]['url'].endswith('/map/base%2F1')
    assert '推定' in result['location_note']


def test_upstream_failure_is_not_empty_results():
    async def check():
        library = Library('http://api.test', 'https://books.test', transport=httpx.MockTransport(lambda _: httpx.Response(503)))
        with pytest.raises(ValueError, match='再試行'):
            await library.request('/api/books/search')
    asyncio.run(check())


def test_cover_urls():
    assert cover_url('http://books.google.com/books/content?id=123').startswith('https:')
    for value in [None, 'javascript:alert(1)', 'https://evil.test/a', 'https://user:pass@books.google.com/a']:
        assert cover_url(value) is None


def test_cards_resource_and_authoritative_ids():
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={'id': 472, 'title': 'Python', 'thumbnail': 'https://books.google.com/books/content?id=1'})
    async def check():
        server = create_server(Library('http://api.test', 'https://books.test', transport=httpx.MockTransport(respond)))
        tools = await server.list_tools()
        cards = next(t for t in tools if t.name == 'show_books')
        assert cards.meta['ui']['resourceUri'] == WIDGET_URI
        resources = list(await server.read_resource(WIDGET_URI))
        assert resources[0].mime_type == 'text/html;profile=mcp-app'
        assert resources[0].meta['ui']['csp']['connectDomains'] == []
        assert '__SITE_ORIGIN__' not in resources[0].content
        await server.call_tool('show_books', {'book_ids': [472, 472]})
        assert len(requests) == 1
        for ids in [[], [0], list(range(1,7))]:
            with pytest.raises(Exception):
                await server.call_tool('show_books', {'book_ids':ids})
    asyncio.run(check())


def test_recommendations_belong_to_selected_books():
    async def check():
        requests = []
        def respond(request):
            requests.append(request)
            return httpx.Response(200, json={'id': int(request.url.path.rsplit('/', 1)[1]), 'title': 'Python'})
        server = create_server(Library('http://api.test', 'https://books.test', transport=httpx.MockTransport(respond)))
        result = await server.call_tool('show_books', {
            'book_ids': [472, 179],
            'recommendations': [{'book_id': 179, 'reason': '図解で基礎を学びたい目的に合う候補です。'}],
        })
        data = result[1]
        assert data['books'][0]['recommendation_reason'] is None
        assert data['books'][1]['recommendation_reason'].startswith('図解')
        requests.clear()
        for reasons in [
            [{'book_id': 999, 'reason': '対象外'}],
            [{'book_id': 472, 'reason': ' '}],
            [{'book_id': 472, 'reason': 'a' * 201}],
            [{'book_id': 472, 'reason': '重複'}] * 2,
        ]:
            with pytest.raises(Exception):
                await server.call_tool('show_books', {'book_ids': [472], 'recommendations': reasons})
        assert requests == []
    asyncio.run(check())
