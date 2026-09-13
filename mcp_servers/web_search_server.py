import json
import os
import sys

from pathlib import Path
from urllib import error
from urllib import request

from dotenv import load_dotenv


# MCP stdio transports exchange UTF-8 JSON lines. Windows may otherwise use
# the active ANSI code page for redirected standard streams.
if hasattr(sys.stdin, 'reconfigure'):
    sys.stdin.reconfigure(encoding='utf-8')

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

PROJECT_DIR = (
    Path(__file__).resolve().parent.parent
)

load_dotenv(
    PROJECT_DIR / '.env'
)

BAIDU_SEARCH_URL = (
    'https://qianfan.baidubce.com'
    '/v2/ai_search/web_search'
)

def send_message(message):
    text = json.dumps(
        message,
        ensure_ascii=False,
    )

    sys.stdout.write(text + '\n')
    sys.stdout.flush()

TOOLS = [
    {
        'name': 'search_web',
        'description': (
            '使用百度搜索查询网页，'
            '返回标题、网址和摘要'
        ),
        'annotations': {
            'readOnlyHint': True,
            'destructiveHint': False,
        },
        'inputSchema': {
            'type': 'object',
            'properties': {
                'query': {
                    'type': 'string',
                    'description': '要搜索的关键词',
                },
                'max_results': {
                    'type': 'integer',
                    'description': '最多返回几条结果',
                    'default': 5,
                },
            },
            'required': [
                'query',
            ],
        },
    },
]

def search_baidu(query,max_results=5,):
    api_key = os.getenv('BAIDU_SEARCH_API_KEY')

    if not api_key:
        raise ValueError(
            '没有配置 '
            'BAIDU_SEARCH_API_KEY'
        )

    max_results = max(1,min(int(max_results), 20),)

    payload = {
        'messages': [
            {
              'role': 'user',
              'content': query,
            },
        ],
        'search_source': ('baidu_search_v2'),
        'resource_type_filter': [
            {
              'type': 'web',
              'top_k': max_results,
            },
        ],
    }

    body = json.dumps(
        payload,
        ensure_ascii=False,
    ).encode('utf-8')

    http_request = request.Request(
        BAIDU_SEARCH_URL,
        data=body,
        method='POST',
        headers={
            'Authorization': (
                f'Bearer {api_key}'
            ),
            'Content-Type': (
                'application/json'
            ),
        },
    )

    try:
        with request.urlopen(
            http_request,
            timeout=30,
        ) as response:
            response_text = (response.read().decode('utf-8'))

    except error.HTTPError as http_error:
        error_text = (
            http_error.read().decode(
                'utf-8',
                errors='replace',
            )
        )

        raise RuntimeError(
            f'百度 API 返回 '
            f'{http_error.code}：'
            f'{error_text}'
        )

    except error.URLError as url_error:
        raise RuntimeError(
            f'无法连接百度搜索：'
            f'{url_error.reason}'
        )

    data = json.loads(response_text)

    references = data.get('references',[],)

    if len(references) == 0:
        return '没有找到相关网页'

    result_lines = []

    for index, item in enumerate(
        references[:max_results],
        start=1,
    ):
        title = item.get(
            'title',
            '没有标题',
        )

        url = item.get(
            'url',
            '',
        )

        snippet = item.get(
            'snippet',
            item.get('content', ''),
        )

        result_lines.append(
            f'{index}. {title}\n'
            f'网址：{url}\n'
            f'摘要：{snippet}'
        )

    return '\n\n'.join(result_lines)

def handle_request(request):
    method = request.get('method')
    request_id = request.get('id')

    if method == 'initialize':
        return {
            'jsonrpc': '2.0',
            'id': request_id,
            'result': {
                'protocolVersion': (
                    '2025-06-18'
                ),
                'capabilities': {
                    'tools': {},
                },
                'serverInfo': {
                    'name': (
                        'baidu-web-search'
                    ),
                    'version': '1.0.0',
                },
            },
        }

    if method == 'tools/list':
        return {
            'jsonrpc': '2.0',
            'id': request_id,
            'result': {
                'tools': TOOLS,
            },
        }

    if method == 'tools/call':
        params = request.get( 'params',{},)

        tool_name = params.get( 'name')

        arguments = params.get('arguments',{},)

        if tool_name != 'search_web':
            return {
                'jsonrpc': '2.0',
                'id': request_id,
                'error': {
                    'code': -32602,
                    'message': (
                        f'未知工具：'
                        f'{tool_name}'
                    ),
                },
            }

        try:
            search_result = search_baidu(
                query=arguments.get('query','',),
                max_results=arguments.get('max_results',5,),
              )

            return {
                'jsonrpc': '2.0',
                'id': request_id,
                'result': {
                    'content': [
                        {
                            'type': 'text',
                            'text': (
                                search_result
                            ),
                        },
                    ],
                    'isError': False,
                },
            }

        except Exception as search_error:
            return {
                'jsonrpc': '2.0',
                'id': request_id,
                'result': {
                    'content': [
                        {
                            'type': 'text',
                            'text': (
                                f'搜索失败：'
                                f'{search_error}'
                            ),
                        },
                    ],
                    'isError': True,
                },
            }

    if method == ('notifications/initialized'):
        return None

    return {
        'jsonrpc': '2.0',
        'id': request_id,
        'error': {
            'code': -32601,
            'message': (
                f'未知方法：{method}'
            ),
        },
    }

def main():
    for line in sys.stdin:
        line = line.strip()

        if line == '':
            continue

        try:
            request = json.loads(line)
            response = handle_request(request)

            if response is not None:
                send_message(response)

        except Exception as error:
            print(
                f'Server 错误：{error}',
                file=sys.stderr,
            )


if __name__ == '__main__':
    main()
