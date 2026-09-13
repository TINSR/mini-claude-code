import mini_claude_code.security as security
from mini_claude_code.security import (
    APPROVED_OPERATIONS,
    MCP_TOOL_ANNOTATIONS,
    check_file_operation,
    check_permission,
    check_rules,
    load_approved_operations,
    permission_hook,
    run_clear_permissions,
    run_list_permissions,
    run_revoke_permission,
)


def test_shutdown_command_is_denied() -> None:
    assert check_permission("powershell", {"command": "shutdown /s /t 0"}) == "deny"


def test_read_only_mcp_tool_does_not_need_approval() -> None:
    name = "mcp__test__read"
    MCP_TOOL_ANNOTATIONS[name] = {"readOnlyHint": True}

    try:
        assert check_rules(name, {}) is None
    finally:
        MCP_TOOL_ANNOTATIONS.pop(name, None)


def test_mutating_tools_require_approval() -> None:
    assert check_permission('write_file', {'path': 'demo.txt'}) == 'ask'
    assert 'remove-item' in check_rules(
        'powershell',
        {'command': 'Remove-Item demo.txt'},
    )


def test_file_operation_checks_the_specific_target() -> None:
    assert check_file_operation('read_file', {'path': 'README.md'})[0] == 'allow'
    assert check_file_operation('write_file', {'path': 'new.txt'})[0] == 'ask'
    assert check_file_operation('read_file', {'path': '.env'})[0] == 'ask'
    assert check_file_operation('read_file', {'path': '../secret.txt'})[0] == 'deny'


def test_always_allow_matches_the_full_operation(
    monkeypatch,
    tmp_path,
) -> None:
    APPROVED_OPERATIONS.clear()
    monkeypatch.setattr(
        security,
        'approval_store_path',
        lambda: tmp_path / 'approvals.json',
    )
    block = type('Block', (), {
        'name': 'write_file',
        'input': {'path': 'demo.txt', 'content': 'hello'},
    })()
    monkeypatch.setattr('builtins.input', lambda prompt: 'a')

    assert permission_hook(block) is None
    assert check_permission(block.name, block.input) == 'allow'
    assert check_permission(
        block.name,
        {'path': 'demo.txt', 'content': 'changed'},
    ) == 'ask'
    APPROVED_OPERATIONS.clear()


def test_approved_operations_persist_and_can_be_revoked(
    monkeypatch,
    tmp_path,
) -> None:
    APPROVED_OPERATIONS.clear()
    monkeypatch.setattr(
        security,
        'approval_store_path',
        lambda: tmp_path / 'approvals.json',
    )
    block = type('Block', (), {
        'name': 'write_file',
        'input': {'path': 'demo.txt', 'content': 'hello'},
    })()
    monkeypatch.setattr('builtins.input', lambda prompt: 'a')

    assert permission_hook(block) is None
    fingerprint = next(iter(APPROVED_OPERATIONS))
    assert '写入文件：demo.txt' in run_list_permissions()

    APPROVED_OPERATIONS.clear()
    load_approved_operations()
    assert check_permission(block.name, block.input) == 'allow'

    assert '已撤销' in run_revoke_permission(fingerprint)
    assert run_list_permissions() == '暂无长期许可'
    assert run_clear_permissions() == '已清空 0 条长期许可'


def test_agent_cannot_directly_edit_approval_store() -> None:
    assert check_file_operation(
        'write_file',
        {'path': '.mini_claude_code/approvals.json'},
    )[0] == 'deny'


def test_mcp_annotations_control_approval() -> None:
    MCP_TOOL_ANNOTATIONS['mcp__danger__delete'] = {'destructiveHint': True}
    try:
        assert '破坏性操作' in check_rules('mcp__danger__delete', {})
        assert '没有声明为只读' in check_rules('mcp__unknown__call', {})
    finally:
        MCP_TOOL_ANNOTATIONS.pop('mcp__danger__delete', None)


def test_permission_hook_respects_user_rejection(monkeypatch) -> None:
    block = type('Block', (), {
        'name': 'write_file',
        'input': {'path': 'demo.txt'},
    })()
    monkeypatch.setattr('builtins.input', lambda prompt: 'n')
    assert permission_hook(block) == '用户拒绝执行该工具'
