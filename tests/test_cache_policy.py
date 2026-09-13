import copy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from anthropic.types import TextBlock, ToolUseBlock

from mini_claude_code import cli
from mini_claude_code import context_manager as context
from mini_claude_code import model_gateway as gateway
from mini_claude_code.tools.filesystem import run_read
from mini_claude_code.usage_report import summarize


def fake_client(host="https://api.deepseek.com/anthropic", response=None):
    response = response or SimpleNamespace(
        content=[TextBlock(type="text", text="done")],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=100, cache_read_input_tokens=900, output_tokens=10),
    )
    return SimpleNamespace(
        base_url=host, messages=SimpleNamespace(create=Mock(return_value=response))
    )


def result(value, ident="call"):
    return {"type": "tool_result", "tool_use_id": ident, "content": value}


def long_history():
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"progress {i} " * 300}
        for i in range(12)
    ]


def test_old_prefix_survives_repeated_tool_rounds_and_new_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(context, "TOOL_RESULTS_DIR", tmp_path / "outputs")
    client = fake_client()
    requests = []

    def respond(**kwargs):
        requests.append(copy.deepcopy(kwargs))
        index = len(requests)
        content = [ToolUseBlock(type="tool_use", id=f"call-{index}", name="read_file", input={})]
        if index == 7:
            content = [TextBlock(type="text", text="done")]
        return SimpleNamespace(content=content, stop_reason="end_turn", usage=None)

    client.messages.create.side_effect = respond
    monkeypatch.setattr(cli, "client", client)
    monkeypatch.setattr(
        cli,
        "assemble_tool_pool",
        lambda: (
            [{"name": "read_file", "input_schema": {"type": "object"}}],
            {"read_file": lambda: "file-content" * 2000},
        ),
    )
    monkeypatch.setattr(cli, "consume_lead_inbox", lambda: [])
    monkeypatch.setattr(cli, "collect_background_results", lambda: [])
    counter = iter(range(100))
    monkeypatch.setattr(
        cli,
        "update_context",
        lambda: {
            "workspace": str(tmp_path),
            "memories": str(next(counter)),
        },
    )
    monkeypatch.setattr(cli, "trigger_hooks", lambda *args: None)
    monkeypatch.setattr(cli, "should_run_background", lambda *args: False)
    messages = [{"role": "user", "content": "review"}]
    cli.agent_loop(messages)
    assert len(requests) == 7
    for old, new in zip(requests, requests[1:], strict=False):
        assert old["system"] == new["system"]
        assert old["tools"] == new["tools"]
        assert gateway.serializable(
            new["messages"][: len(old["messages"])]
        ) == gateway.serializable(old["messages"])
    events = [json.loads(line) for line in gateway.USAGE_LOG.read_text().splitlines()]
    assert all(event["stable_request_prefix"] for event in events[1:])


def test_anthropic_markers_are_only_on_request_copy():
    messages = [{"role": "user", "content": "hello"}]
    tools = [{"name": "z", "input_schema": {}}, {"name": "a", "input_schema": {}}]
    original = copy.deepcopy([messages, tools])
    client = fake_client("https://api.anthropic.com")
    gateway.call_model(
        client, "main", messages=messages, tools=tools, system="stable", max_tokens=500
    )
    sent = client.messages.create.call_args.kwargs
    assert sent["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert sent["messages"][-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert sent["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert [messages, tools] == original


@pytest.mark.parametrize("host", ["https://api.deepseek.com/anthropic", "https://example.invalid"])
def test_compatible_endpoints_do_not_get_unrequested_cache_markers(host):
    client = fake_client(host)
    gateway.call_model(client, "main", messages=[{"role": "user", "content": "hello"}])
    assert "cache_control" not in json.dumps(client.messages.create.call_args.kwargs)


def test_anthropic_does_not_mark_thinking_blocks():
    request = gateway.prepare_request(
        fake_client("https://api.anthropic.com"),
        {
            "messages": [
                {"role": "user", "content": "hi"},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "hidden", "signature": "sig"},
                    ],
                },
            ],
        },
    )
    assert "cache_control" not in request["messages"][-1]["content"][0]
    assert "cache_control" in request["messages"][0]["content"][0]


def test_discussing_cache_control_does_not_disable_cache():
    request = gateway.prepare_request(
        fake_client("https://api.anthropic.com"),
        {
            "messages": [{"role": "user", "content": "Explain cache_control"}],
        },
    )
    assert request["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}


def test_existing_explicit_cache_markers_are_preserved():
    kwargs = {
        "system": [
            {"type": "text", "text": "stable", "cache_control": {"type": "ephemeral", "ttl": "1h"}}
        ],
        "messages": [{"role": "user", "content": "hi"}],
    }
    assert gateway.prepare_request(fake_client("https://api.anthropic.com"), kwargs) == kwargs


def test_cache_opt_out_and_explicit_compatible_opt_in(monkeypatch):
    monkeypatch.setenv("PROMPT_CACHE_MODE", "off")
    assert "cache_control" not in json.dumps(
        gateway.prepare_request(
            fake_client("https://api.anthropic.com"),
            {"messages": [{"role": "user", "content": "hi"}]},
        )
    )
    monkeypatch.setenv("PROMPT_CACHE_MODE", "anthropic")
    assert "cache_control" in json.dumps(
        gateway.prepare_request(
            fake_client("https://example.invalid"),
            {"messages": [{"role": "user", "content": "hi"}]},
        )
    )


def test_budget_is_enforced_before_paid_call(monkeypatch):
    monkeypatch.setenv("CONTEXT_TOKEN_BUDGET", "16000")
    client = fake_client()
    with pytest.raises(ValueError, match="budget"):
        gateway.call_model(
            client, "main", messages=[{"role": "user", "content": "x" * 60000}], max_tokens=3000
        )
    client.messages.create.assert_not_called()


def test_budget_counts_system_and_tools():
    assert gateway.estimate_tokens([], system="x" * 9000) > 3000
    assert gateway.estimate_tokens([], tools=[{"description": "x" * 9000}]) > 3000


def test_subagent_checks_and_applies_context_budget(monkeypatch):
    client = fake_client()
    monkeypatch.setattr(cli, "client", client)
    fit = Mock(return_value=[{"role": "user", "content": "shortened context"}])
    monkeypatch.setattr(cli, "fit_context", fit)
    assert cli.spawn_subagent("review") == "done"
    fit.assert_called_once()
    assert client.messages.create.call_args.kwargs["messages"][0]["content"] == "shortened context"


def test_subagent_stops_without_paid_request_when_compaction_fails(monkeypatch):
    client = fake_client()
    monkeypatch.setattr(cli, "client", client)
    monkeypatch.setattr(cli, "fit_context", Mock(side_effect=ValueError("budget exceeded")))
    assert "budget exceeded" in cli.spawn_subagent("review")
    client.messages.create.assert_not_called()


def test_tool_results_persist_individually_without_mutation(tmp_path, monkeypatch):
    monkeypatch.setattr(context, "TOOL_RESULTS_DIR", tmp_path)
    blocks = [result("x" * 68000)]
    before = copy.deepcopy(blocks)
    prepared = context.prepare_tool_results(blocks)
    assert blocks == before
    assert len(prepared[0]["content"]) < 3000
    assert next(tmp_path.glob("*.txt")).read_text() == "x" * 68000


def test_batch_budget_keeps_all_tool_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(context, "TOOL_RESULTS_DIR", tmp_path)
    blocks = [result("x" * 11000, str(i)) for i in range(5)]
    prepared = context.prepare_tool_results(blocks)
    assert sum(len(block["content"]) for block in prepared) <= 24000
    assert [block["tool_use_id"] for block in prepared] == [str(i) for i in range(5)]


def test_result_files_do_not_collide_on_id_reuse(tmp_path, monkeypatch):
    monkeypatch.setattr(context, "TOOL_RESULTS_DIR", tmp_path)
    a = context.persist_large_output("../escape", "A" * 13000)
    b = context.persist_large_output("../escape", "B" * 13000)
    assert a != b
    assert len(list(tmp_path.glob("*.txt"))) == 2


def test_worktree_result_reference_is_readable_inside_its_own_workspace(tmp_path):
    worktree = tmp_path / "worktree"
    output = "sample line\n" * 2000
    prepared = context.prepare_tool_results([result(output)], base_dir=worktree)
    path = next((worktree / ".transcripts/tool-results").glob("*.txt"))
    assert str(path) in prepared[0]["content"]
    assert path.read_text() == output
    assert run_read(str(path), limit=2, base_dir=worktree).endswith("sample line\nsample line")


def test_worktree_summary_snapshot_stays_readable(tmp_path, monkeypatch):
    worktree = tmp_path / "worktree"
    monkeypatch.setattr(context, "summarize_history", Mock(return_value="summary"))
    compacted = context.compact_history(long_history(), base_dir=worktree)
    snapshot = next((worktree / ".transcripts").glob("transcript_*.jsonl"))
    assert str(snapshot) in compacted[0]["content"]
    assert "progress" in run_read(str(snapshot), limit=1, base_dir=worktree)


@pytest.mark.parametrize(
    "content,reason",
    [
        ([], "end_turn"),
        ([{"type": "text", "text": "unfinished"}], "max_tokens"),
        ([{"type": "text", "text": "  "}], "end_turn"),
        ([{"type": "text", "text": "子 Agent 没有返回文本结论"}], "end_turn"),
    ],
)
def test_invalid_summary_never_replaces_history(tmp_path, monkeypatch, content, reason):
    monkeypatch.setattr(context, "TRANSCRIPT_DIR", tmp_path)
    monkeypatch.setattr(
        context,
        "_client",
        fake_client(
            response=SimpleNamespace(
                content=content,
                stop_reason=reason,
                usage=None,
            )
        ),
    )
    messages = long_history()
    before = copy.deepcopy(messages)
    assert context.compact_history(messages) is messages
    assert messages == before


def test_summary_failure_is_transactional(tmp_path, monkeypatch):
    monkeypatch.setattr(context, "TRANSCRIPT_DIR", tmp_path)
    monkeypatch.setattr(context, "summarize_history", Mock(side_effect=RuntimeError("offline")))
    messages = long_history()
    assert context.compact_history(messages) is messages


def test_compact_boundary_keeps_tool_use_and_result(tmp_path, monkeypatch):
    monkeypatch.setattr(context, "TRANSCRIPT_DIR", tmp_path)
    summarize_mock = Mock(return_value="goal and next steps")
    monkeypatch.setattr(context, "summarize_history", summarize_mock)
    messages = long_history()
    messages[5] = {
        "role": "assistant",
        "content": [
            {"type": "tool_use", "id": "call", "name": "read_file", "input": {}},
        ],
    }
    messages[6] = {"role": "user", "content": [result("ok")]}
    compacted = context.compact_history(messages)
    assert compacted[1:] == messages[5:]
    assert summarize_mock.call_args.args[0] == messages[:5]


def test_summary_sees_recent_material_and_omits_thinking(monkeypatch):
    client = fake_client(
        response=SimpleNamespace(
            content=[{"type": "text", "text": "summary"}], stop_reason="end_turn", usage=None
        )
    )
    monkeypatch.setattr(context, "_client", client)
    monkeypatch.setenv("CONTEXT_TOKEN_BUDGET", "64000")
    messages = [
        {"role": "user", "content": "x" * 81000},
        {"role": "assistant", "content": [{"type": "thinking", "thinking": "THINKING"}]},
        {"role": "user", "content": "LATEST"},
    ]
    context.summarize_history(messages)
    sent = client.messages.create.call_args.kwargs
    assert "LATEST" in sent["messages"][0]["content"]
    assert "THINKING" not in sent["messages"][0]["content"]
    assert messages[1]["content"][0]["thinking"] == "THINKING"


def test_usage_logs_failures_without_prompt_or_error_text():
    client = fake_client()
    client.messages.create.side_effect = RuntimeError("PRIVATE ERROR")
    with pytest.raises(RuntimeError):
        gateway.call_model(
            client, "summary", messages=[{"role": "user", "content": "PRIVATE PROMPT"}]
        )
    raw = gateway.USAGE_LOG.read_text()
    assert "PRIVATE" not in raw
    assert json.loads(raw)["error_type"] == "RuntimeError"


def test_log_failure_does_not_repeat_successful_call(tmp_path, monkeypatch):
    monkeypatch.setattr(gateway, "USAGE_LOG", tmp_path)  # Directory, not a writable log file.
    client = fake_client()
    assert gateway.call_model(client, "main", messages=[]).stop_reason == "end_turn"
    client.messages.create.assert_called_once()


def test_usage_report_preserves_missing_fields_and_handles_torn_last_line(tmp_path):
    path = tmp_path / "usage.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "provider": "compatible",
                        "model": "m",
                        "purpose": "main",
                        "usage": {"input_tokens": 10, "cache_read_input_tokens": 100},
                    }
                ),
                json.dumps(
                    {"provider": "compatible", "model": "m", "purpose": "main", "usage": None}
                ),
                "{incomplete",
            ]
        )
    )
    report = summarize([path])
    assert report["malformed_lines"] == 1
    group = report["groups"][0]
    assert group["requests"] == 2 and group["usage_present"] == 1
    assert "output_tokens" not in group["raw_token_sums"]
    assert group["field_samples"]["cache_read_input_tokens"] == 1


def test_read_pagination_and_bounds(tmp_path):
    (tmp_path / "file").write_text("\n".join(str(i) for i in range(450)))
    for offset, end in [(0, 200), (200, 400), (400, 450)]:
        assert run_read("file", offset=offset, base_dir=tmp_path).splitlines()[1:] == [
            str(i) for i in range(offset, end)
        ]
    assert "错误" in run_read("file", offset=-1, base_dir=tmp_path)
    assert "错误" in run_read("file", limit=True, base_dir=tmp_path)


def test_tools_order_is_stable_and_semantic_change_is_visible():
    client = fake_client()
    messages = [{"role": "user", "content": "test"}]
    tools = [{"name": "z", "input_schema": {}}, {"name": "a", "input_schema": {}}]
    gateway.call_model(client, "main", messages=messages, tools=tools)
    gateway.call_model(client, "main", messages=messages, tools=list(reversed(tools)))
    tools[0]["description"] = "changed"
    gateway.call_model(client, "main", messages=messages, tools=tools)
    events = [json.loads(line) for line in gateway.USAGE_LOG.read_text().splitlines()]
    assert events[1]["stable_request_prefix"]
    assert events[2]["prefix_config_changed"]
