"""Mechanically extract the context-management functions from the modular CLI.

This script exists to make the low-intrusion refactor reproducible. It locates
complete top-level functions with Python's AST and moves their source text
without reformatting their bodies.
"""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = ROOT / "src" / "mini_claude_code" / "cli.py"
MODULE_PATH = ROOT / "src" / "mini_claude_code" / "context_manager.py"

FUNCTION_NAMES = [
    "run_compact",
    "estimate_size",
    "block_type",
    "message_has_tool_use",
    "is_tool_result_message",
    "snip_compact",
    "collect_tool_results",
    "micro_compact",
    "persist_large_output",
    "tool_result_budget",
    "write_transcript",
    "summarize_history",
    "compact_history",
    "reactive_compact",
    "is_prompt_too_long",
]

HEADER = '''from pathlib import Path
import json


WORKDIR = Path.cwd().resolve()
CONTEXT_LIMIT = 50_000
KEEP_RECENT_TOOL_RESULTS = 3
PERSIST_THRESHOLD = 30_000
TOOL_RESULTS_DIR = WORKDIR / ".transcripts" / "tool-results"
TRANSCRIPT_DIR = WORKDIR / ".transcripts"

_client = None
_model = None


def configure_context_runtime(client, model):
   global _client
   global _model

   _client = client
   _model = model


'''


def main() -> None:
    source = CLI_PATH.read_text(encoding="utf-8")
    lines = source.splitlines(keepends=True)
    tree = ast.parse(source)
    nodes = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in FUNCTION_NAMES
    }

    missing = [name for name in FUNCTION_NAMES if name not in nodes]
    if missing:
        raise RuntimeError(f"Missing functions in cli.py: {missing}")

    function_sources = []
    for name in FUNCTION_NAMES:
        node = nodes[name]
        function_sources.append("".join(lines[node.lineno - 1 : node.end_lineno]))

    module_source = HEADER + "\n\n".join(function_sources)
    module_source = module_source.replace("client.messages.create", "_client.messages.create")
    module_source = module_source.replace("model=MODEL", "model=_model")
    MODULE_PATH.write_text(module_source, encoding="utf-8")

    ranges = sorted(
        ((node.lineno - 1, node.end_lineno) for node in nodes.values()),
        reverse=True,
    )
    for start, end in ranges:
        del lines[start:end]

    CLI_PATH.write_text("".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()

