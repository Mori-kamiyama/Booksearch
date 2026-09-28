import json
import httpx
import lambda_handler
from server import Library


def event(method, params=None):
    return {'version': '2.0', 'routeKey': 'ANY /mcp', 'rawPath': '/mcp', 'rawQueryString': '',
            'headers': {'host': 'mcp.execute-api.ap-northeast-1.amazonaws.com',
                        'content-type': 'application/json', 'accept': 'application/json, text/event-stream'},
            'requestContext': {'domainName': 'mcp.execute-api.ap-northeast-1.amazonaws.com',
                               'http': {'method': 'POST', 'path': '/mcp', 'sourceIp': '127.0.0.1', 'protocol': 'HTTP/1.1'}},
            'body': json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}}),
            'isBase64Encoded': False}


def test_warm_lambda_initialize_tools_and_cards(monkeypatch):
    monkeypatch.setenv('CATALOG_API_URL', 'https://api.test')
    monkeypatch.setattr(lambda_handler, 'Library', lambda *args: Library(*args, transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={'id': 472, 'title': 'Python', 'description': '紹介', 'description_source': 'google_books', 'page_count': 282}))))
    for method, params in [
        ('initialize', {'protocolVersion': '2025-03-26', 'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '1'}}),
        ('tools/list', {}),
        ('tools/call', {'name': 'get_book', 'arguments': {'book_id': 472}}),
        ('tools/call', {'name': 'show_books', 'arguments': {'book_ids': [472]}}),
        ('resources/read', {'uri': 'ui://honno-ki/books-v5.html'}),
    ]:
        response = lambda_handler.handler(event(method, params), None)
        assert response['statusCode'] == 200, response
        body = json.loads(response['body'])
        assert 'error' not in body and not body['result'].get('isError'), body
        if method == 'tools/call' and params['name'] == 'get_book':
            assert body['result']['structuredContent']['description'] == '紹介'
            assert body['result']['structuredContent']['page_count'] == 282
