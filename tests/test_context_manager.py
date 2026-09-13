from mini_claude_code import context_manager
from mini_claude_code.context_manager import (
    block_type,
    extract_text,
    is_tool_result_message,
    message_has_tool_use,
    micro_compact,
    persist_large_output,
    reactive_compact,
    run_compact,
    summarize_history,
    tool_result_budget,
    write_transcript,
)


def test_block_type_supports_dicts() -> None:
    assert block_type({"type": "tool_use"}) == "tool_use"


def test_extract_text_supports_dict_blocks() -> None:
    assert extract_text([
        {"type": "thinking", "thinking": "hidden"},
        {"type": "text", "text": "summary"},
    ]) == "summary"


def test_tool_pair_detection() -> None:
    tool_use_message = {
        "role": "assistant",
        "content": [{"type": "tool_use"}],
    }
    tool_result_message = {
        "role": "user",
        "content": [{"type": "tool_result", "content": "ok"}],
    }

    assert message_has_tool_use(tool_use_message)
    assert is_tool_result_message(tool_result_message)


def test_compact_tool_acknowledges_optional_focus() -> None:
    assert run_compact() == "上下文压缩请求已接收。"
    assert run_compact("保留错误原因") == "上下文压缩请求已接收。重点：保留错误原因"


def test_write_transcript_creates_json_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(context_manager, "TRANSCRIPT_DIR", tmp_path)

    transcript_path = write_transcript([
        {"role": "user", "content": "hello"},
    ])

    assert transcript_path.exists()
    assert transcript_path.parent == tmp_path
    assert '"content": "hello"' in transcript_path.read_text(encoding="utf-8")


def test_micro_compact_preserves_old_results() -> None:
    messages = []
    for index in range(5):
        messages.append({
            'role': 'user',
            'content': [{
                'type': 'tool_result',
                'tool_use_id': f'call-{index}',
                'content': 'x' * 200,
            }],
        })

    micro_compact(messages)

    assert messages[0]['content'][0]['content'] == 'x' * 200
    assert messages[-1]['content'][0]['content'] == 'x' * 200


def test_large_tool_result_is_persisted(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(context_manager, 'TOOL_RESULTS_DIR', tmp_path)
    monkeypatch.setattr(context_manager, 'PERSIST_THRESHOLD', 10)

    result = persist_large_output('call-large', 'abcdefghijklmno')

    assert next(tmp_path.glob('*.txt')).read_text(encoding='utf-8') == 'abcdefghijklmno'
    assert '<persisted-output>' in result


def test_tool_result_budget_persists_largest_block(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(context_manager, 'TOOL_RESULTS_DIR', tmp_path)
    monkeypatch.setattr(context_manager, 'PERSIST_THRESHOLD', 10)
    messages = [{
        'role': 'user',
        'content': [
            {'type': 'tool_result', 'tool_use_id': 'large', 'content': 'x' * 50},
            {'type': 'tool_result', 'tool_use_id': 'small', 'content': 'ok'},
        ],
    }]

    prepared = tool_result_budget(messages, max_size=20)

    assert '<persisted-output>' in prepared[-1]['content'][0]['content']
    assert messages[-1]['content'][0]['content'] == 'x' * 50


class FakeMessagesAPI:
    def create(self, **kwargs):
        return type('Response', (), {
            'content': [{'type': 'text', 'text': 'compact summary'}],
            'stop_reason': 'end_turn',
        })()


class FakeClient:
    messages = FakeMessagesAPI()


def test_summary_and_reactive_compaction(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(context_manager, 'TRANSCRIPT_DIR', tmp_path)
    context_manager.configure_context_runtime(FakeClient(), 'fake-model')
    messages = [
        {'role': 'user', 'content': f'message-{index}' * 500}
        for index in range(8)
    ]

    assert summarize_history(messages) == 'compact summary'
    compacted = reactive_compact(messages)

    assert compacted[0]['content'].startswith('[历史已压缩]')
    assert compacted[-2:] == messages[-2:]


def test_prompt_too_long_detection() -> None:
    assert context_manager.is_prompt_too_long(Exception('context length exceeded'))
    assert not context_manager.is_prompt_too_long(Exception('network failed'))
