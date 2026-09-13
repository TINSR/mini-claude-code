"""Configurable stdio MCP server for exercising the client's transport rules.

Run as ``python fake_mcp_server.py <mode> [late_call_delay_seconds]``. Modes:

``normal``          full lifecycle; also emits a notification and a response
                    for an id nobody requested, which the client must drop
``silent_init``     never answers ``initialize``
``hang_on_call``    normal handshake, then never answers ``tools/call``
``late_first_call`` answers the first ``tools/call`` long after the client has
                    given up, and later calls immediately
``crash_on_call``   exits as soon as a ``tools/call`` arrives
``concurrent``      answers every request on its own thread, honouring an
                    ``arguments.delay`` so responses come back out of order
"""

import json
import sys
import threading
import time

MODE = sys.argv[1] if len(sys.argv) > 1 else 'normal'
# 迟到响应的延迟由测试指定，必须明显大于测试用的客户端超时。
LATE_FIRST_CALL_DELAY = float(sys.argv[2]) if len(sys.argv) > 2 else 6.0

if hasattr(sys.stdin, 'reconfigure'):
    sys.stdin.reconfigure(encoding='utf-8')

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

TOOLS = [
    {
        'name': 'echo',
        'description': 'Echo the text back',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'text': {'type': 'string'},
                'delay': {'type': 'number'},
            },
        },
        'annotations': {'readOnlyHint': True},
    },
    {
        'name': 'handshake_order',
        'description': 'Report the order of the methods this server received',
        'inputSchema': {'type': 'object', 'properties': {}},
        'annotations': {'readOnlyHint': True},
    },
]

write_lock = threading.Lock()
state_lock = threading.Lock()
received = []
call_count = {'tools/call': 0}


def send(message):
    with write_lock:
        sys.stdout.write(json.dumps(message, ensure_ascii=False) + '\n')
        sys.stdout.flush()


def handle(request):
    method = request.get('method')
    request_id = request.get('id')

    with state_lock:
        received.append(method)

    if method == 'initialize':
        if MODE == 'silent_init':
            return None

        # 客户端必须忽略通知和没人等待的响应，不能把它们当成 initialize 的回复。
        send({
            'jsonrpc': '2.0',
            'method': 'notifications/message',
            'params': {'level': 'info', 'data': 'server starting'},
        })
        send({
            'jsonrpc': '2.0',
            'id': 9999,
            'result': {'note': 'nobody is waiting for this id'},
        })

        return {
            'jsonrpc': '2.0',
            'id': request_id,
            'result': {
                'protocolVersion': '2025-06-18',
                'capabilities': {'tools': {}},
                'serverInfo': {'name': 'fake-mcp', 'version': '1.0.0'},
            },
        }

    if method == 'notifications/initialized':
        return None

    if method == 'tools/list':
        return {
            'jsonrpc': '2.0',
            'id': request_id,
            'result': {'tools': TOOLS},
        }

    if method == 'tools/call':
        if MODE == 'hang_on_call':
            return None

        if MODE == 'crash_on_call':
            sys.stdout.flush()
            sys.exit(1)

        params = request.get('params', {})
        arguments = params.get('arguments', {})

        with state_lock:
            call_count['tools/call'] += 1
            call_number = call_count['tools/call']

        if MODE == 'late_first_call' and call_number == 1:
            time.sleep(LATE_FIRST_CALL_DELAY)

        delay = float(arguments.get('delay', 0))

        if delay > 0:
            time.sleep(delay)

        if params.get('name') == 'handshake_order':
            with state_lock:
                text = ','.join(received)
        else:
            text = str(arguments.get('text', ''))

        return {
            'jsonrpc': '2.0',
            'id': request_id,
            'result': {
                'content': [{'type': 'text', 'text': text}],
                'isError': False,
            },
        }

    return {
        'jsonrpc': '2.0',
        'id': request_id,
        'error': {'code': -32601, 'message': f'未知方法：{method}'},
    }


def respond(request):
    response = handle(request)

    if response is not None:
        send(response)


def main():
    while True:
        line = sys.stdin.readline()

        if line == '':
            return

        if line.strip() == '':
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue

        if MODE in ('concurrent', 'late_first_call'):
            threading.Thread(
                target=respond,
                args=(request,),
                daemon=True,
            ).start()
        else:
            respond(request)


if __name__ == '__main__':
    main()
