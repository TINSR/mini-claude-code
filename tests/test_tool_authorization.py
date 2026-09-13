"""The same refused operation must stay refused on every execution path."""

import threading

import pytest

import mini_claude_code.background_tasks as background_tasks
import mini_claude_code.cli as cli
import mini_claude_code.security as security
import mini_claude_code.team_runtime as team_runtime
from mini_claude_code.tool_executor import ToolCall, execute_tool


class ToolBlock:
    type = 'tool_use'

    def __init__(self, name, tool_input, block_id='call-1'):
        self.name = name
        self.input = tool_input
        self.id = block_id


class TextBlock:
    type = 'text'

    def __init__(self, text):
        self.text = text


class FakeResponse:
    def __init__(self, content, stop_reason='end_turn'):
        self.content = content
        self.stop_reason = stop_reason


class FakeMessagesAPI:
    """Replays canned responses and records the tool results it was sent."""

    def __init__(self, owner, responses):
        self.owner = owner
        self.responses = list(responses)

    def create(self, **kwargs):
        for message in kwargs.get('messages', []):
            content = message.get('content')

            if not isinstance(content, list):
                continue

            for block in content:
                if isinstance(block, dict) and block.get('type') == 'tool_result':
                    if block not in self.owner.tool_results_seen:
                        self.owner.tool_results_seen.append(block)

        if len(self.responses) == 0:
            raise AssertionError('模型被调用的次数超出预期')

        response = self.responses.pop(0)

        if isinstance(response, BaseException):
            raise response

        return response


class FakeClient:
    def __init__(self, responses):
        self.tool_results_seen = []
        self.messages = FakeMessagesAPI(self, responses)


def wait_until(predicate, timeout=10.0):
    deadline = threading.Event()

    for _attempt in range(int(timeout / 0.05)):
        if predicate():
            return True
        deadline.wait(0.05)

    raise AssertionError('等待条件超时')


def prepare_cli_loop(monkeypatch, responses, handlers):
    """Wire cli.agent_loop to a fake model but keep the real Hook pipeline."""
    monkeypatch.setattr(cli, 'client', FakeClient(responses))
    monkeypatch.setattr(cli, 'assemble_tool_pool', lambda: ([], handlers))
    monkeypatch.setattr(cli, 'consume_lead_inbox', lambda: [])
    monkeypatch.setattr(cli, 'collect_background_results', lambda: [])
    monkeypatch.setattr(cli, 'update_context', lambda: {})
    monkeypatch.setattr(cli, 'get_system_prompt', lambda context: 'system')


@pytest.fixture(autouse=True)
def isolated_approvals(tmp_path, monkeypatch):
    security.APPROVED_OPERATIONS.clear()
    monkeypatch.setattr(
        security,
        'approval_store_path',
        lambda: tmp_path / '.mini_claude_code' / 'approvals.json',
    )
    security.set_execution_context()
    yield
    security.APPROVED_OPERATIONS.clear()
    security.set_execution_context()


def refuse_input(prompt):
    raise AssertionError('非交互执行路径不应该读取 stdin')


def test_write_refused_by_the_user_is_refused_on_every_path(monkeypatch, tmp_path):
    """One interactive rejection, then the same call from the other paths."""
    monkeypatch.setattr('builtins.input', lambda prompt: 'n')
    written = []
    handlers = {'write_file': lambda path, content: written.append(path)}

    block = ToolBlock('write_file', {'path': 'report.md', 'content': 'hi'})

    assert '用户拒绝执行该工具' in execute_tool(block, handlers)
    assert written == []

    # 队友、后台和 Cron 线程不能弹审批，必须直接拒绝而不是读 stdin。
    monkeypatch.setattr('builtins.input', refuse_input)

    for label in ('teammate:alice', 'background:bg_0001', 'cron'):
        security.set_execution_context(interactive=False, label=label)
        output = execute_tool(block, handlers)

        assert '需要人工审批' in output
        assert label in output
        assert written == []


def test_deny_list_blocks_the_command_on_every_path(monkeypatch):
    monkeypatch.setattr('builtins.input', refuse_input)
    ran = []
    handlers = {'powershell': lambda command: ran.append(command)}
    block = ToolBlock('powershell', {'command': 'Stop-Computer -Force'})

    for interactive in (True, False):
        security.set_execution_context(interactive=interactive, label='test')

        assert execute_tool(block, handlers) == '权限系统拒绝执行该工具'

    assert ran == []


def test_saved_approval_lets_a_teammate_run_the_same_operation(monkeypatch):
    written = []
    handlers = {'write_file': lambda path, content: written.append(path) or 'ok'}
    block = ToolBlock('write_file', {'path': 'notes.md', 'content': 'text'})

    monkeypatch.setattr('builtins.input', lambda prompt: 'a')
    assert execute_tool(block, handlers) == 'ok'

    monkeypatch.setattr('builtins.input', refuse_input)
    security.set_execution_context(interactive=False, label='teammate:alice')

    assert execute_tool(block, handlers) == 'ok'
    assert written == ['notes.md', 'notes.md']


def test_approval_does_not_leak_into_a_teammate_worktree(monkeypatch, tmp_path):
    """Approving ``notes.md`` in the main workspace must not cover a Worktree."""
    worktree = tmp_path / '.worktrees' / 'feature'
    worktree.mkdir(parents=True)

    handlers = {'write_file': lambda path, content: 'ok'}
    block = ToolBlock('write_file', {'path': 'notes.md', 'content': 'text'})

    monkeypatch.setattr('builtins.input', lambda prompt: 'a')
    assert execute_tool(block, handlers) == 'ok'

    monkeypatch.setattr('builtins.input', refuse_input)
    security.set_execution_context(
        interactive=False,
        base_dir=str(worktree),
        label='teammate:alice',
    )

    assert '需要人工审批' in execute_tool(block, handlers)


def test_file_checks_resolve_against_the_teammate_workspace(tmp_path):
    worktree = tmp_path / 'wt'
    worktree.mkdir()
    (worktree / '.env').write_text('SECRET=1', encoding='utf-8')

    security.set_execution_context(
        interactive=False,
        base_dir=str(worktree),
        label='teammate:alice',
    )

    permission, reason = security.check_file_operation('read_file', {'path': '.env'})
    assert permission == 'ask'
    assert str(worktree) in reason

    # Worktree 之外仍然不可访问，许可文件也不可被工具改写。
    assert security.check_file_operation(
        'read_file',
        {'path': '../escape.txt'},
    )[0] == 'deny'
    assert security.check_file_operation(
        'write_file',
        {'path': '.mini_claude_code/approvals.json'},
    )[0] == 'deny'


def test_execution_context_is_per_thread():
    security.set_execution_context(interactive=True, label='main')
    observed = {}

    def worker():
        security.set_execution_context(interactive=False, label='teammate:bob')
        observed['worker'] = security.current_execution_context()

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    assert observed['worker']['interactive'] is False
    assert security.current_execution_context()['label'] == 'main'


def test_execution_context_manager_restores_the_previous_value():
    security.set_execution_context(interactive=True, label='main')

    with security.execution_context(interactive=False, label='teammate:bob'):
        assert security.current_execution_context()['label'] == 'teammate:bob'

    assert security.current_execution_context()['interactive'] is True


def test_runtime_issued_calls_are_authorized_too(monkeypatch):
    """Calls the runtime makes itself, e.g. a teammate auto-claim."""
    monkeypatch.setattr('builtins.input', refuse_input)
    claimed = []
    handlers = {'write_file': lambda path, content: claimed.append(path)}

    security.set_execution_context(interactive=False, label='teammate:alice')
    output = execute_tool(
        ToolCall('write_file', {'path': 'auto.md', 'content': 'x'}, 'auto-1'),
        handlers,
    )

    assert '需要人工审批' in output
    assert claimed == []

    allowed = execute_tool(
        ToolCall('claim_task', {'task_id': 'task-1'}, 'auto-claim-task-1'),
        {'claim_task': lambda task_id: f'已认领任务 {task_id}'},
    )
    assert allowed == '已认领任务 task-1'


def test_executor_reports_unknown_tools_and_handler_failures(monkeypatch):
    monkeypatch.setattr('builtins.input', refuse_input)

    def explode():
        raise ValueError('boom')

    assert '未知工具 missing' in execute_tool(ToolBlock('missing', {}), {})
    assert 'ValueError: boom' in execute_tool(
        ToolBlock('explode', {}),
        {'explode': explode},
    )


def test_teammate_loop_cannot_write_without_a_saved_approval(tmp_path, monkeypatch):
    """Drive the real teammate loop and check the write never reaches disk."""
    monkeypatch.setattr('builtins.input', refuse_input)
    monkeypatch.setattr(team_runtime, 'BUS', team_runtime.MessageBus(tmp_path))
    monkeypatch.setattr(team_runtime, 'active_teammates', {})
    monkeypatch.setattr(
        team_runtime,
        'fit_context',
        lambda messages, *args, **kwargs: messages,
    )
    monkeypatch.setattr(
        team_runtime,
        'prepare_tool_results',
        lambda blocks, base_dir=None: blocks,
    )

    written = []
    monkeypatch.setattr(
        team_runtime,
        'run_write',
        lambda path, content, base_dir=None: written.append(path),
    )

    # configure_team_runtime 写模块级状态；测试结束必须还原。
    monkeypatch.setattr(team_runtime, '_client', None)
    monkeypatch.setattr(team_runtime, '_model', None)
    monkeypatch.setattr(team_runtime, '_tools', [])
    monkeypatch.setattr(team_runtime, '_extract_text', None)

    client = FakeClient([
        FakeResponse(
            [ToolBlock('write_file', {'path': 'plan.md', 'content': 'x'})],
            'tool_use',
        ),
        # 第二轮直接失败，让队友线程立刻收尾退出。
        RuntimeError('stop the teammate loop'),
    ])
    team_runtime.configure_team_runtime(
        client,
        'test-model',
        [{'name': 'write_file', 'description': 'w', 'input_schema': {'type': 'object'}}],
        lambda content: 'summary',
    )

    team_runtime.spawn_teammate_thread('alice', 'reviewer', 'write the plan')
    wait_until(lambda: 'alice' not in team_runtime.active_teammates)

    assert written == []
    results = client.tool_results_seen
    assert len(results) == 1
    assert '需要人工审批' in results[0]['content']
    assert 'teammate:alice' in results[0]['content']


def test_background_worker_marks_itself_non_interactive(monkeypatch):
    observed = {}

    def record(**arguments):
        observed['context'] = security.current_execution_context()
        return 'done'

    monkeypatch.setattr(background_tasks, '_tool_handlers', {'powershell': record})
    monkeypatch.setattr(background_tasks, 'background_tasks', {})
    monkeypatch.setattr(background_tasks, 'background_results', {})

    block = ToolBlock('powershell', {'command': 'pytest', 'run_in_background': True})
    background_tasks.start_background_task(block)

    for _attempt in range(100):
        if 'context' in observed:
            break
        threading.Event().wait(0.05)

    assert observed['context']['interactive'] is False
    assert observed['context']['label'].startswith('background:')
    # 上下文是 with 作用域，不会留在线程上。
    assert security.current_execution_context()['label'] == 'main'


def test_main_loop_reports_the_rejection_instead_of_writing(monkeypatch):
    monkeypatch.setattr('builtins.input', lambda prompt: 'n')
    written = []
    prepare_cli_loop(
        monkeypatch,
        [
            FakeResponse(
                [ToolBlock('write_file', {'path': 'demo.txt', 'content': 'x'})],
                'tool_use',
            ),
            FakeResponse([TextBlock('done')]),
        ],
        {'write_file': lambda path, content: written.append(path)},
    )
    messages = [{'role': 'user', 'content': 'write demo.txt'}]

    cli.agent_loop(messages)

    assert written == []
    assert '用户拒绝执行该工具' in messages[-2]['content'][0]['content']


def test_subagent_reports_the_rejection_instead_of_writing(monkeypatch):
    monkeypatch.setattr('builtins.input', lambda prompt: 'n')
    written = []
    client = FakeClient([
        FakeResponse(
            [ToolBlock('write_file', {'path': 'demo.txt', 'content': 'x'})],
            'tool_use',
        ),
        FakeResponse([TextBlock('子任务结束')]),
    ])
    monkeypatch.setattr(cli, 'client', client)
    monkeypatch.setattr(
        cli,
        'SUB_HANDLERS',
        {'write_file': lambda path, content: written.append(path)},
    )

    assert cli.spawn_subagent('write demo.txt') == '子任务结束'
    assert written == []
    assert '用户拒绝执行该工具' in client.tool_results_seen[0]['content']
