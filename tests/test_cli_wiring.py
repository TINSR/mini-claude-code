import ast
import threading
from pathlib import Path

import pytest

import mini_claude_code.cli as cli
import mini_claude_code.session_manager as session_manager

ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = ROOT / 'src' / 'mini_claude_code' / 'cli.py'


def test_cli_has_no_private_prompt_builder():
    """The prompt belongs to prompt_manager, not to a copy inside the CLI."""
    tree = ast.parse(CLI_PATH.read_text(encoding='utf-8'))
    imports = {
        alias.asname or alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    definitions = {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }

    assert 'get_system_prompt' in imports
    assert 'assemble_system_prompt' not in definitions


def test_cli_extracts_only_text_blocks():
    assert cli.extract_text([
        {'type': 'thinking', 'thinking': 'hidden'},
        {'type': 'text', 'text': 'visible'},
    ]) == 'visible'


def test_todo_write_updates_shared_state():
    result = cli.run_todo_write([
        {'content': 'verify CLI', 'status': 'completed'},
    ])

    assert result == '已更新 1 个任务'
    assert cli.CURRENT_TODOS[0]['content'] == 'verify CLI'


def test_build_system_contains_workspace_and_tools():
    prompt = cli.build_system()

    assert str(cli.WORKDIR) in prompt
    assert '当前可用 Skills' in prompt


class TextBlock:
    type = 'text'

    def __init__(self, text):
        self.text = text


class ToolBlock:
    type = 'tool_use'

    def __init__(self, name, tool_input, block_id='call-1'):
        self.name = name
        self.input = tool_input
        self.id = block_id


class FakeResponse:
    def __init__(self, content, stop_reason='end_turn'):
        self.content = content
        self.stop_reason = stop_reason


class FakeMessagesAPI:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return next(self.responses)


class FakeClient:
    def __init__(self, responses):
        self.messages = FakeMessagesAPI(responses)


def prepare_agent_loop(monkeypatch, responses, handlers=None):
    monkeypatch.setattr(cli, 'client', FakeClient(responses))
    monkeypatch.setattr(
        cli,
        'assemble_tool_pool',
        lambda: ([], handlers or {}),
    )
    monkeypatch.setattr(cli, 'consume_lead_inbox', lambda: [])
    monkeypatch.setattr(cli, 'collect_background_results', lambda: [])
    monkeypatch.setattr(cli, 'update_context', lambda: {})
    monkeypatch.setattr(cli, 'get_system_prompt', lambda context: 'system')
    monkeypatch.setattr(cli, 'trigger_hooks', lambda *args: None)


def test_agent_loop_sends_the_built_prompt_to_the_model(monkeypatch):
    seen = []
    prepare_agent_loop(monkeypatch, [FakeResponse([TextBlock('done')])])
    monkeypatch.setattr(
        cli,
        'get_system_prompt',
        lambda context: seen.append(context) or 'prompt for this turn',
    )
    monkeypatch.setattr(cli, 'update_context', lambda: {'skills': ['pdf']})

    cli.agent_loop([{'role': 'user', 'content': 'hello'}])

    request = cli.client.messages.requests[0]
    assert request['system'] == 'prompt for this turn'
    assert seen[0]['skills'] == ['pdf']


def test_agent_loop_handles_plain_text_response(monkeypatch, capsys):
    prepare_agent_loop(
        monkeypatch,
        [FakeResponse([TextBlock('done')])],
    )
    messages = [{'role': 'user', 'content': 'hello'}]

    cli.agent_loop(messages)

    assert messages[-1]['role'] == 'assistant'
    assert 'done' in capsys.readouterr().out


def test_agent_loop_executes_tool_and_returns_result(monkeypatch):
    calls = []
    prepare_agent_loop(
        monkeypatch,
        [
            FakeResponse([ToolBlock('echo', {'value': 'ok'})], 'tool_use'),
            FakeResponse([TextBlock('finished')]),
        ],
        {'echo': lambda value: calls.append(value) or value},
    )
    messages = [{'role': 'user', 'content': 'run echo'}]

    cli.agent_loop(messages)

    assert calls == ['ok']
    tool_result_message = messages[-2]
    assert tool_result_message['content'][0]['type'] == 'tool_result'
    assert tool_result_message['content'][0]['content'] == 'ok'


def test_agent_loop_reports_only_current_turn_tool_requests(monkeypatch):
    stop_calls = []
    prepare_agent_loop(
        monkeypatch,
        [
            FakeResponse([ToolBlock('echo', {'value': 'ok'})], 'tool_use'),
            FakeResponse([TextBlock('finished')]),
        ],
        {'echo': lambda value: value},
    )

    def capture_hooks(event, *args):
        if event == 'Stop':
            stop_calls.append(args)
        return None

    monkeypatch.setattr(cli, 'trigger_hooks', capture_hooks)
    messages = [
        {
            'role': 'user',
            'content': [
                {
                    'type': 'tool_result',
                    'tool_use_id': 'old-call',
                    'content': 'old result',
                },
            ],
        },
        {'role': 'user', 'content': 'run echo'},
    ]

    cli.agent_loop(messages)

    assert stop_calls[0][1] == 1


def test_agent_loop_reports_unknown_and_failed_tools(monkeypatch):
    def fail():
        raise ValueError('boom')

    prepare_agent_loop(
        monkeypatch,
        [
            FakeResponse([
                ToolBlock('missing', {}, 'missing-call'),
                ToolBlock('fail', {}, 'failed-call'),
            ], 'tool_use'),
            FakeResponse([TextBlock('finished')]),
        ],
        {'fail': fail},
    )
    messages = [{'role': 'user', 'content': 'run'}]

    cli.agent_loop(messages)

    results = messages[-2]['content']
    assert '未知工具' in results[0]['content']
    assert 'ValueError' in results[1]['content']


def configure_sessions(tmp_path, monkeypatch):
    sessions_dir = tmp_path / '.sessions'
    sessions_dir.mkdir()
    monkeypatch.setattr(session_manager, 'SESSIONS_DIR', sessions_dir)
    monkeypatch.setattr(
        session_manager,
        'CURRENT_SESSION_FILE',
        sessions_dir / 'current',
    )
    return sessions_dir


def test_running_task_is_saved_to_its_own_session_after_a_switch(
    tmp_path,
    monkeypatch,
):
    """Switching sessions mid-turn must not redirect the result."""
    configure_sessions(tmp_path, monkeypatch)
    owner = session_manager.create_session('拥有任务的会话')
    switched = session_manager.create_session('切换后的会话')
    monkeypatch.setitem(cli.SESSION_STATE, 'session', owner)

    def switch_session_during_the_turn(messages):
        messages.append({'role': 'assistant', 'content': '任务结果'})
        cli.SESSION_STATE['session'] = switched

    monkeypatch.setattr(cli, 'agent_loop', switch_session_during_the_turn)

    cli.run_session_agent(owner)

    assert session_manager.load_session(owner.id).messages[-1][
        'content'
    ] == '任务结果'
    assert session_manager.load_session(switched.id).messages == []
    # 任务所属会话不是当前会话时，不能抢走 current 指针。
    assert session_manager.CURRENT_SESSION_FILE.read_text(
        encoding='utf-8'
    ) == switched.id


def test_running_task_still_saves_when_the_turn_raises(tmp_path, monkeypatch):
    configure_sessions(tmp_path, monkeypatch)
    owner = session_manager.create_session('会话')
    monkeypatch.setitem(cli.SESSION_STATE, 'session', owner)

    def explode(messages):
        messages.append({'role': 'user', 'content': '中断前的输入'})
        raise RuntimeError('boom')

    monkeypatch.setattr(cli, 'agent_loop', explode)

    with pytest.raises(RuntimeError):
        cli.run_session_agent(owner)

    assert session_manager.load_session(owner.id).messages[-1][
        'content'
    ] == '中断前的输入'


def test_resolve_session_falls_back_and_reports_missing_sessions(
    tmp_path,
    monkeypatch,
):
    configure_sessions(tmp_path, monkeypatch)
    current = session_manager.create_session('当前')
    other = session_manager.create_session('其他')
    monkeypatch.setitem(cli.SESSION_STATE, 'session', current)

    assert cli.resolve_session('') is current
    assert cli.resolve_session(current.id) is current
    assert cli.resolve_session(other.id).id == other.id
    assert cli.resolve_session('session-does-not-exist') is None
    assert cli.resolve_session('../escape') is None


def test_agent_loop_recovers_from_context_error(monkeypatch):
    prepare_agent_loop(monkeypatch, [])
    calls = {'count': 0}

    def fake_retry(callback, state):
        calls['count'] += 1
        if calls['count'] == 1:
            raise RuntimeError('context length exceeded')
        return FakeResponse([TextBlock('recovered')])

    monkeypatch.setattr(cli, 'call_with_retry', fake_retry)
    monkeypatch.setattr(
        cli,
        'reactive_compact',
        lambda messages: [{'role': 'user', 'content': 'summary'}],
    )
    messages = [{'role': 'user', 'content': 'large prompt'}]

    cli.agent_loop(messages)

    assert calls['count'] == 2
    assert messages[0]['content'] == 'summary'


@pytest.mark.parametrize('exit_kind', ['exit', 'eof', 'interrupt', 'turn_error', 'save_error'])
def test_cli_exit_saves_session_and_closes_resources(tmp_path, monkeypatch, exit_kind):
    configure_sessions(tmp_path, monkeypatch)
    session = session_manager.create_session('退出测试')
    closed = []
    monkeypatch.setattr('sys.argv', ['mycc'])
    monkeypatch.setattr(cli, 'MODEL', 'test-model')
    monkeypatch.setattr(cli, 'Anthropic', lambda **kwargs: type(
        'Client', (), {'close': lambda self: closed.append('api')},
    )())
    for name in ('configure_context_runtime', 'configure_memory_runtime',
                 'configure_team_runtime', 'configure_cron_runtime',
                 'load_approved_operations', 'load_durable_jobs'):
        monkeypatch.setattr(cli, name, lambda *args: None)
    monkeypatch.setattr(cli, 'load_current_session', lambda: session)
    stopped = []
    started = threading.Barrier(3)

    def worker(stop_event):
        started.wait(timeout=5)
        assert stop_event.wait(timeout=5)
        stopped.append(True)

    monkeypatch.setattr(cli, 'cron_scheduler_loop', worker)
    monkeypatch.setattr(cli, 'queue_processor_loop', worker)
    monkeypatch.setattr(cli, 'close_mcp_clients', lambda: closed.append('mcp'))

    def read_input(prompt):
        started.wait(timeout=5)
        session.messages.append({'role': 'user', 'content': '应保存的内容'})
        if exit_kind == 'eof':
            raise EOFError
        if exit_kind == 'interrupt':
            raise KeyboardInterrupt
        return 'run' if exit_kind == 'turn_error' else 'exit'

    monkeypatch.setattr('builtins.input', read_input)
    monkeypatch.setattr(cli, 'load_memories', lambda messages: '')
    monkeypatch.setattr(cli, 'trigger_hooks', lambda *args: None)

    def fail_turn(session):
        raise RuntimeError('model failed')

    monkeypatch.setattr(cli, 'run_session_agent', fail_turn)
    if exit_kind == 'save_error':
        def fail_save(session):
            raise OSError('disk full')
        monkeypatch.setattr(cli, 'save_session', fail_save)
    if exit_kind == 'turn_error':
        with pytest.raises(RuntimeError, match='model failed'):
            cli.main()
    else:
        cli.main()

    assert closed == ['mcp', 'api']
    assert len(stopped) == 2
    if exit_kind != 'save_error':
        persisted = session_manager.load_session(session.id).messages
        assert persisted[0]['content'] == '应保存的内容'
