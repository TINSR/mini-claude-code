"""Mechanically extract teammate messaging, lifecycle, and protocol handling."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = ROOT / 'src' / 'mini_claude_code' / 'cli.py'
MODULE_PATH = ROOT / 'src' / 'mini_claude_code' / 'team_runtime.py'
SYMBOL_NAMES = [
    'MessageBus', 'run_send_message', 'run_check_inbox', 'spawn_teammate_thread',
    'run_spawn_teammate', 'ProtocolState', 'new_request_id', 'match_response',
    'consume_lead_inbox', 'run_request_shutdown', 'run_request_plan',
    'run_review_plan', 'idle_poll',
]

HEADER = '''from dataclasses import dataclass, field
from pathlib import Path
import json
import random
import threading
import time

from .task_manager import (
   claim_task,
   complete_task,
   load_task,
   run_list_tasks,
   scan_unclaimed_tasks,
)
from .tools import run_glob, run_powershell, run_read, run_write
from .worktree_manager import WORKTREES_DIR


WORKDIR = Path.cwd().resolve()
MAILBOX_DIR = WORKDIR / '.mailboxes'
MAILBOX_DIR.mkdir(exist_ok=True)
IDLE_POLL_INTERVAL = 5
IDLE_TIMEOUT = 60

_client = None
_model = None
_tools = []
_extract_text = None


def configure_team_runtime(client, model, tools, extract_text):
   global _client
   global _model
   global _tools
   global _extract_text

   _client = client
   _model = model
   _tools = tools
   _extract_text = extract_text


'''


def source_start(node):
    decorators = getattr(node, 'decorator_list', [])
    return min((item.lineno for item in decorators), default=node.lineno) - 1


def main() -> None:
    source = CLI_PATH.read_text(encoding='utf-8')
    lines = source.splitlines(keepends=True)
    tree = ast.parse(source)
    nodes = {
        node.name: node for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in SYMBOL_NAMES
    }
    missing = [name for name in SYMBOL_NAMES if name not in nodes]
    if missing:
        raise RuntimeError(f'Missing team symbols: {missing}')
    pieces = [
        ''.join(lines[source_start(nodes[name]):nodes[name].end_lineno])
        for name in SYMBOL_NAMES
    ]
    module_source = HEADER + '\n\n'.join(pieces)
    module_source = module_source.replace('client.messages.create', '_client.messages.create')
    module_source = module_source.replace('model=MODEL', 'model=_model')
    module_source = module_source.replace('TOOLS', '_tools')
    module_source = module_source.replace('extract_text(', '_extract_text(')
    MODULE_PATH.write_text(module_source, encoding='utf-8')
    for start, end in sorted(
        ((source_start(node), node.end_lineno) for node in nodes.values()), reverse=True
    ):
        del lines[start:end]
    CLI_PATH.write_text(''.join(lines), encoding='utf-8')


if __name__ == '__main__':
    main()
