"""Offline prefix replay. No API requests; this does NOT measure cache hit tokens."""

import argparse
import copy
import json
from pathlib import Path

from mini_claude_code.context_manager import prepare_tool_results


def append_round(messages, index, legacy):
    """Add one tool call/result exchange, applying the old rewrite if asked.

    ``cache_live_benchmark.py`` sends exactly these messages to a real provider,
    so the two reports describe the same requests. Keep this the only definition.
    """
    messages.extend(
        [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": f"call-{index}",
                        "name": "read_file",
                        "input": {"path": "sample.txt"},
                    }
                ],
            },
            {
                "role": "user",
                "content": prepare_tool_results(
                    [
                        {
                            "type": "tool_result",
                            "tool_use_id": f"call-{index}",
                            "content": f"File version {index}: " + "sample line\n" * 300,
                        }
                    ]
                ),
            },
        ]
    )
    if legacy:
        results = [
            block
            for message in messages
            if isinstance(message["content"], list)
            for block in message["content"]
            if block["type"] == "tool_result"
        ]
        for block in results[:-3]:
            if len(block["content"]) > 120:
                block["content"] = "[较早的工具结果已压缩，需要时请重新运行工具]"
    return messages


def build_messages(legacy, upto):
    """The conversation as it stands after round ``upto``."""
    messages = [{"role": "user", "content": "Review this synthetic project."}]
    for index in range(upto + 1):
        append_round(messages, index, legacy)
    return messages


def replay(legacy=False, rounds=10):
    messages = [{"role": "user", "content": "Review this synthetic project."}]
    previous = None
    stable = 0
    transmitted_chars = 0
    for index in range(rounds):
        append_round(messages, index, legacy)
        if previous is not None and messages[: len(previous)] == previous:
            stable += 1
        transmitted_chars += len(json.dumps(messages, ensure_ascii=False))
        previous = copy.deepcopy(messages)
    return {
        "requests": rounds,
        "comparable_pairs": rounds - 1,
        "unchanged_previous_prefixes": stable,
        "serialized_message_chars_total": transmitted_chars,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = {
        "measurement": "Offline structural replay, not provider cache hits or billing.",
        "legacy_rolling_rewrite": replay(legacy=True),
        "append_only": replay(),
        "tradeoff": "Append-only can send more characters. Real usage and task quality must "
        "be measured before claiming cost savings.",
    }
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
