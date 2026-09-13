"""Mechanically extract background tool execution from the CLI."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = ROOT / 'src' / 'mini_claude_code' / 'cli.py'
MODULE_PATH = ROOT / 'src' / 'mini_claude_code' / 'background_tasks.py'
FUNCTION_NAMES = [
    'is_slow_operation',
    'should_run_background',
    'execute_background_tool',
    'start_background_task',
    'collect_background_results',
]

HEADER = '''import threading


background_tasks = {}
background_results = {}
background_lock = threading.Lock()
background_counter = 0
_tool_handlers = {}


def configure_background_runtime(tool_handlers):
   global _tool_handlers
   _tool_handlers = tool_handlers


'''


def main() -> None:
    source = CLI_PATH.read_text(encoding='utf-8')
    lines = source.splitlines(keepends=True)
    tree = ast.parse(source)
    nodes = {
        node.name: node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in FUNCTION_NAMES
    }
    missing = [name for name in FUNCTION_NAMES if name not in nodes]
    if missing:
        raise RuntimeError(f'Missing background functions: {missing}')
    pieces = [
        ''.join(lines[nodes[name].lineno - 1:nodes[name].end_lineno])
        for name in FUNCTION_NAMES
    ]
    module_source = HEADER + '\n\n'.join(pieces)
    module_source = module_source.replace('TOOL_HANDLERS.get(tool_name)', '_tool_handlers.get(tool_name)')
    MODULE_PATH.write_text(module_source, encoding='utf-8')
    for start, end in sorted(
        ((node.lineno - 1, node.end_lineno) for node in nodes.values()), reverse=True
    ):
        del lines[start:end]
    CLI_PATH.write_text(''.join(lines), encoding='utf-8')


if __name__ == '__main__':
    main()
