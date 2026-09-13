import json
from types import SimpleNamespace

import pytest

import mini_claude_code.atomic_io as atomic_io
import mini_claude_code.session_manager as session_manager


def configure_storage(tmp_path, monkeypatch):
    sessions_dir = tmp_path / '.sessions'
    sessions_dir.mkdir()
    monkeypatch.setattr(session_manager, 'SESSIONS_DIR', sessions_dir)
    monkeypatch.setattr(
        session_manager,
        'CURRENT_SESSION_FILE',
        sessions_dir / 'current',
    )


def test_session_round_trip_serializes_sdk_blocks(tmp_path, monkeypatch):
    configure_storage(tmp_path, monkeypatch)
    session = session_manager.create_session('测试会话')
    session.messages.append({
        'role': 'assistant',
        'content': [
            SimpleNamespace(
                model_dump=lambda: {'type': 'text', 'text': '你好'},
            )
        ],
    })

    session_manager.save_session(session)
    loaded = session_manager.load_session(session.id)

    assert loaded.title == '测试会话'
    assert loaded.messages[0]['content'][0]['text'] == '你好'


def test_switch_command_displays_previous_history(tmp_path, monkeypatch):
    configure_storage(tmp_path, monkeypatch)
    first = session_manager.create_session('第一个会话')
    first.messages.append({'role': 'user', 'content': '第一条问题'})
    session_manager.save_session(first)
    second = session_manager.create_session('第二个会话')

    switched, output = session_manager.handle_session_command(
        f'/switch {first.id}', second
    )

    assert switched.id == first.id
    assert '第一条问题' in output
    assert '第一个会话' in output


def test_history_hides_injected_memory_context(tmp_path, monkeypatch):
    configure_storage(tmp_path, monkeypatch)
    session = session_manager.create_session('memory')
    session.messages.append({
        'role': 'user',
        'content': '你好\n\n<relevant_memories>secret</relevant_memories>',
    })

    output = session_manager.render_history(session)

    assert '你好' in output
    assert 'secret' not in output


def test_session_command_is_handled_locally(tmp_path, monkeypatch):
    configure_storage(tmp_path, monkeypatch)
    session = session_manager.create_session('当前测试')

    selected, output = session_manager.handle_session_command('/session', session)

    assert selected is session
    assert session.id in output
    assert '当前测试' in output


def test_unknown_slash_command_does_not_reach_model(tmp_path, monkeypatch):
    configure_storage(tmp_path, monkeypatch)
    session = session_manager.create_session('test')

    result = session_manager.handle_session_command('/unknown', session)

    assert result is not None
    assert '未知命令' in result[1]


def test_session_management_commands_cover_full_lifecycle(tmp_path, monkeypatch):
    configure_storage(tmp_path, monkeypatch)
    current = session_manager.create_session('Current')

    assert '/switch' in session_manager.handle_session_command('/help', current)[1]
    created, output = session_manager.handle_session_command('/new Work', current)
    assert created.title == 'Work'
    assert '暂无对话记录' in output
    renamed, output = session_manager.handle_session_command('/rename Renamed', created)
    assert renamed.title == 'Renamed'
    assert 'Renamed' in output
    assert '会话列表' in session_manager.handle_session_command('/sessions', renamed)[1]
    assert '不能删除当前会话' in session_manager.handle_session_command(
        f'/delete {renamed.id}', renamed
    )[1]
    assert '已删除会话' in session_manager.handle_session_command(
        f'/delete {current.id}', renamed
    )[1]
    assert '找不到会话' in session_manager.handle_session_command(
        '/delete session-does-not-exist', renamed
    )[1]
    assert session_manager.handle_session_command('normal prompt', renamed) is None


def test_load_current_session_recovers_from_broken_pointer(tmp_path, monkeypatch):
    configure_storage(tmp_path, monkeypatch)
    existing = session_manager.create_session('Existing')
    session_manager.CURRENT_SESSION_FILE.write_text(
        'session-missing', encoding='utf-8'
    )

    loaded = session_manager.load_current_session()

    assert loaded.id == existing.id


def test_content_text_renders_all_block_types():
    content = [
        {'type': 'text', 'text': 'hello'},
        {'type': 'tool_use', 'name': 'read_file'},
        {'type': 'tool_result', 'content': 'file contents'},
    ]

    rendered = session_manager.content_text(content)

    assert 'hello' in rendered
    assert '调用工具 read_file' in rendered
    assert '[工具结果] file contents' in rendered


def test_interrupted_save_keeps_the_previous_file(tmp_path, monkeypatch):
    configure_storage(tmp_path, monkeypatch)
    session = session_manager.create_session('Recoverable')
    session.messages.append({'role': 'user', 'content': '第一轮'})
    session_manager.save_session(session)

    path = session_manager.session_path(session.id)
    before = path.read_text(encoding='utf-8')

    real_replace = atomic_io.os.replace

    def fail_on_session_file(source, destination):
        if str(destination) == str(path):
            raise OSError('模拟保存过程中断电')
        return real_replace(source, destination)

    monkeypatch.setattr(atomic_io.os, 'replace', fail_on_session_file)
    session.messages.append({'role': 'user', 'content': '第二轮'})

    with pytest.raises(OSError):
        session_manager.save_session(session)

    assert path.read_text(encoding='utf-8') == before
    assert json.loads(before)['messages'][0]['content'] == '第一轮'
    # 失败的写入不能留下临时文件。
    assert list(session_manager.SESSIONS_DIR.glob('*.tmp')) == []


def test_saving_another_session_does_not_move_the_current_pointer(
    tmp_path,
    monkeypatch,
):
    configure_storage(tmp_path, monkeypatch)
    current = session_manager.create_session('Current')
    other = session_manager.create_session('Other')

    session_manager.save_session(current)
    assert session_manager.CURRENT_SESSION_FILE.read_text(
        encoding='utf-8'
    ) == current.id

    other.messages.append({'role': 'user', 'content': '定时任务结果'})
    session_manager.save_session(other, mark_current=False)

    assert session_manager.CURRENT_SESSION_FILE.read_text(
        encoding='utf-8'
    ) == current.id
    assert session_manager.load_session(other.id).messages[0][
        'content'
    ] == '定时任务结果'


def test_session_validation_and_rename_errors(tmp_path, monkeypatch):
    configure_storage(tmp_path, monkeypatch)
    session = session_manager.create_session('Valid')

    assert session_manager.validate_session_id(session.id)
    assert not session_manager.validate_session_id('../escape')
    try:
        session_manager.rename_session(session, '   ')
    except ValueError as error:
        assert '不能为空' in str(error)
    else:
        raise AssertionError('empty title should fail')
