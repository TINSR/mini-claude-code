"""Mechanically extract the persistent shared-task subsystem from the CLI."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = ROOT / 'src' / 'mini_claude_code' / 'cli.py'
MODULE_PATH = ROOT / 'src' / 'mini_claude_code' / 'task_manager.py'

SYMBOL_NAMES = [
    'Task',
    'task_path',
    'save_task',
    'load_task',
    'create_task',
    'list_tasks',
    'get_task',
    'can_start',
    'claim_task',
    'scan_unclaimed_tasks',
    'complete_task',
    'run_create_task',
    'run_list_tasks',
    'run_get_task',
    'run_claim_task',
    'run_complete_task',
]

HEADER = '''from dataclasses import asdict, dataclass
from pathlib import Path
import json
import random
import time


WORKDIR = Path.cwd().resolve()
TASKS_DIR = WORKDIR / '.tasks'
TASKS_DIR.mkdir(parents=True, exist_ok=True)


'''


def source_start(node: ast.AST) -> int:
    decorators = getattr(node, 'decorator_list', [])
    if decorators:
        return min(item.lineno for item in decorators) - 1
    return node.lineno - 1


def main() -> None:
    source = CLI_PATH.read_text(encoding='utf-8')
    lines = source.splitlines(keepends=True)
    tree = ast.parse(source)
    nodes = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in SYMBOL_NAMES
    }

    missing = [name for name in SYMBOL_NAMES if name not in nodes]
    if missing:
        raise RuntimeError(f'Missing task symbols in cli.py: {missing}')

    pieces = []
    for name in SYMBOL_NAMES:
        node = nodes[name]
        pieces.append(''.join(lines[source_start(node):node.end_lineno]))

    MODULE_PATH.write_text(HEADER + '\n\n'.join(pieces), encoding='utf-8')

    ranges = sorted(
        ((source_start(node), node.end_lineno) for node in nodes.values()),
        reverse=True,
    )
    for start, end in ranges:
        del lines[start:end]

    CLI_PATH.write_text(''.join(lines), encoding='utf-8')


if __name__ == '__main__':
    main()
