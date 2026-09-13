"""Mechanically extract Git worktree management from the CLI."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = ROOT / 'src' / 'mini_claude_code' / 'cli.py'
MODULE_PATH = ROOT / 'src' / 'mini_claude_code' / 'worktree_manager.py'

FUNCTION_NAMES = [
    'validate_worktree_name',
    'run_git',
    'log_worktree_event',
    'bind_task_to_worktree',
    'create_worktree',
    'count_worktree_changes',
    'keep_worktree',
    'remove_worktree',
    'run_create_worktree',
    'run_keep_worktree',
    'run_remove_worktree',
]

HEADER = '''from pathlib import Path
import json
import re
import subprocess
import time

from .task_manager import load_task, save_task


WORKDIR = Path.cwd().resolve()
WORKTREES_DIR = WORKDIR / '.worktrees'
WORKTREES_DIR.mkdir(exist_ok=True)


'''


def main() -> None:
    source = CLI_PATH.read_text(encoding='utf-8')
    lines = source.splitlines(keepends=True)
    tree = ast.parse(source)
    nodes = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in FUNCTION_NAMES
    }
    missing = [name for name in FUNCTION_NAMES if name not in nodes]
    if missing:
        raise RuntimeError(f'Missing worktree functions: {missing}')

    pieces = [
        ''.join(lines[nodes[name].lineno - 1:nodes[name].end_lineno])
        for name in FUNCTION_NAMES
    ]
    MODULE_PATH.write_text(HEADER + '\n\n'.join(pieces), encoding='utf-8')

    for start, end in sorted(
        ((node.lineno - 1, node.end_lineno) for node in nodes.values()),
        reverse=True,
    ):
        del lines[start:end]
    CLI_PATH.write_text(''.join(lines), encoding='utf-8')


if __name__ == '__main__':
    main()
