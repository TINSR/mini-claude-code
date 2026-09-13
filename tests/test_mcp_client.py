import sys
import threading
import time
from pathlib import Path

import pytest

import mini_claude_code.mcp_client as mcp_client

FAKE_SERVER = Path(__file__).resolve().parent / 'fake_mcp_server.py'

# 迟到响应的延迟必须明显大于客户端超时，慢机器上也不能提前返回。
LATE_ARRIVAL_SECONDS = 6.0


def fake_server(mode, delay=LATE_ARRIVAL_SECONDS):
    return [sys.executable, str(FAKE_SERVER), mode, str(delay)]


class FakeMCPClient:
    tools = [
        {
            'name': 'search web',
            'description': 'Search the web',
            'inputSchema': {
                'type': 'object',
                'properties': {'query': {'type': 'string'}},
            },
            'annotations': {'readOnlyHint': True},
        }
    ]

    def call_tool(self, name, arguments):
        return f'{name}:{arguments["query"]}'


def test_dynamic_mcp_tool_registration(monkeypatch):
    annotations = {}
    mcp_client.configure_mcp_runtime(
        [{'name': 'read_file'}],
        {'read_file': object()},
        annotations,
    )
    monkeypatch.setattr(mcp_client, 'mcp_clients', {'baidu search': FakeMCPClient()})

    tools, handlers = mcp_client.assemble_tool_pool()
    dynamic_name = 'mcp__baidu_search__search_web'

    assert any(tool['name'] == dynamic_name for tool in tools)
    assert handlers[dynamic_name](query='agent') == 'search web:agent'
    assert annotations[dynamic_name] == {'readOnlyHint': True}


def test_unknown_mcp_server_returns_available_names(monkeypatch):
    monkeypatch.setattr(mcp_client, 'MCP_SERVERS', {'baidu': ['python', 'server.py']})
    result = mcp_client.connect_mcp('missing')
    assert '未知 MCP Server' in result
    assert 'baidu' in result


def test_bundled_stdio_server_handshake():
    root = Path(__file__).resolve().parents[1]
    client = mcp_client.MCPClient(
        'test-search',
        [sys.executable, str(root / 'mcp_servers' / 'web_search_server.py')],
    )

    try:
        client.start()
        assert any(tool['name'] == 'search_web' for tool in client.tools)
    finally:
        client.close()


def test_initialized_notification_precedes_the_first_normal_request():
    """The server reports the order it actually observed on the wire."""
    client = mcp_client.MCPClient('fake', fake_server('normal'), timeout=15)

    try:
        client.start()

        assert [tool['name'] for tool in client.tools] == [
            'echo',
            'handshake_order',
        ]

        order = client.call_tool('handshake_order', {}).split(',')

        assert order == [
            'initialize',
            'notifications/initialized',
            'tools/list',
            'tools/call',
        ]
    finally:
        client.close()


def test_notifications_and_stray_ids_do_not_answer_a_request():
    """``normal`` emits a notification and an unrequested id during startup."""
    client = mcp_client.MCPClient('fake', fake_server('normal'), timeout=15)

    try:
        client.start()

        assert client.call_tool('echo', {'text': '你好'}) == '你好'
        assert client._pending == {}
    finally:
        client.close()


def test_request_timeout_releases_the_caller():
    client = mcp_client.MCPClient('fake', fake_server('hang_on_call'), timeout=1.0)

    try:
        client.start()
        started = time.monotonic()

        with pytest.raises(TimeoutError, match='超时'):
            client.send_request('tools/call', {'name': 'echo', 'arguments': {}})

        assert time.monotonic() - started < 10
        # 放弃等待后必须清理挂起请求，否则会错配后续响应。
        assert client._pending == {}
    finally:
        client.close()


def test_a_late_response_is_dropped_instead_of_reused():
    """The abandoned reply must not become the answer to a later request."""
    client = mcp_client.MCPClient(
        'fake',
        fake_server('late_first_call'),
        timeout=2.0,
    )

    try:
        client.start()
        started = time.monotonic()

        with pytest.raises(TimeoutError):
            client.send_request(
                'tools/call',
                {'name': 'echo', 'arguments': {'text': 'first'}},
            )

        second = client.send_request(
            'tools/call',
            {'name': 'echo', 'arguments': {'text': 'second'}},
        )
        assert second['result']['content'][0]['text'] == 'second'

        # 等迟到的第一条响应真正到达，它不能污染下一个请求。
        remaining = LATE_ARRIVAL_SECONDS + 1 - (time.monotonic() - started)

        if remaining > 0:
            time.sleep(remaining)

        third = client.send_request(
            'tools/call',
            {'name': 'echo', 'arguments': {'text': 'third'}},
        )
        assert third['result']['content'][0]['text'] == 'third'
        assert client._pending == {}
    finally:
        client.close()


def test_concurrent_callers_each_receive_their_own_response():
    client = mcp_client.MCPClient('fake', fake_server('concurrent'), timeout=20)
    results = {}

    def call(text, delay):
        results[text] = client.call_tool('echo', {'text': text, 'delay': delay})

    try:
        client.start()

        # 慢的先发出、后返回，快的后发出、先返回。
        threads = [
            threading.Thread(target=call, args=('slow', 1.5)),
            threading.Thread(target=call, args=('fast', 0)),
        ]

        for thread in threads:
            thread.start()
            time.sleep(0.2)

        for thread in threads:
            thread.join(timeout=25)

        assert results == {'slow': 'slow', 'fast': 'fast'}
        assert client._pending == {}
    finally:
        client.close()


def test_server_exit_fails_the_pending_request_immediately():
    client = mcp_client.MCPClient('fake', fake_server('crash_on_call'), timeout=20)

    try:
        client.start()

        with pytest.raises(RuntimeError, match='退出码'):
            client.send_request('tools/call', {'name': 'echo', 'arguments': {}})

        # 连接已经死亡，后续请求不应该再等一个完整超时。
        started = time.monotonic()

        with pytest.raises(RuntimeError):
            client.send_request('tools/list', {})

        assert time.monotonic() - started < 5
    finally:
        client.close()


def test_call_tool_turns_transport_failures_into_tool_output():
    client = mcp_client.MCPClient('fake', fake_server('hang_on_call'), timeout=1.0)

    try:
        client.start()
        output = client.call_tool('echo', {'text': 'hi'})

        assert 'MCP 工具执行失败' in output
        assert '超时' in output
    finally:
        client.close()


def test_close_stops_the_process_and_is_repeatable():
    client = mcp_client.MCPClient('fake', fake_server('normal'), timeout=15)
    client.start()
    process = client.process

    client.close()

    assert process.poll() is not None
    assert client.process is None

    client.close()

    with pytest.raises(RuntimeError, match='已关闭'):
        client.send_request('tools/list', {})


def test_failed_handshake_does_not_register_or_leak_the_server(monkeypatch):
    monkeypatch.setattr(
        mcp_client,
        'MCP_SERVERS',
        {'fake': fake_server('silent_init')},
    )
    monkeypatch.setattr(mcp_client, 'mcp_clients', {})
    monkeypatch.setattr(mcp_client, 'HANDSHAKE_TIMEOUT', 1.0)

    started = []
    real_popen = mcp_client.subprocess.Popen

    def record_popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        started.append(process)
        return process

    monkeypatch.setattr(mcp_client.subprocess, 'Popen', record_popen)

    result = mcp_client.connect_mcp('fake')

    assert 'MCP 连接失败' in result
    assert '超时' in result
    assert mcp_client.mcp_clients == {}
    assert len(started) == 1
    assert started[0].poll() is not None


def test_close_mcp_clients_shuts_every_connection_down(monkeypatch):
    monkeypatch.setattr(mcp_client, 'mcp_clients', {})
    monkeypatch.setattr(
        mcp_client,
        'MCP_SERVERS',
        {'fake': fake_server('normal')},
    )

    assert '已连接 MCP Server' in mcp_client.connect_mcp('fake')
    assert '已经连接' in mcp_client.connect_mcp('fake')

    process = mcp_client.mcp_clients['fake'].process

    mcp_client.close_mcp_clients()

    assert mcp_client.mcp_clients == {}
    assert process.poll() is not None
