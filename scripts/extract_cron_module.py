"""Mechanically extract cron scheduling and durable job persistence."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = ROOT / 'src' / 'mini_claude_code' / 'cli.py'
MODULE_PATH = ROOT / 'src' / 'mini_claude_code' / 'cron_scheduler.py'
SYMBOL_NAMES = [
    'CronJob', 'cron_field_matches', 'cron_matches', 'validate_cron_field',
    'validate_cron', 'save_durable_jobs', 'load_durable_jobs', 'schedule_job',
    'cancel_job', 'run_schedule_cron', 'run_list_crons', 'run_cancel_cron',
    'cron_scheduler_loop', 'consume_cron_queue', 'has_cron_queue',
    'queue_processor_loop',
]

HEADER = '''from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
import json
import random
import threading
import time


WORKDIR = Path.cwd().resolve()
SCHEDULED_TASKS_FILE = WORKDIR / '.scheduled_tasks.json'
scheduled_jobs = {}
cron_queue = []
cron_lock = threading.Lock()
last_fired = {}
agent_lock = threading.Lock()
_agent_loop = None
_messages = None


def configure_cron_runtime(agent_loop, messages):
   global _agent_loop
   global _messages

   _agent_loop = agent_loop
   _messages = messages


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
        raise RuntimeError(f'Missing cron symbols: {missing}')
    pieces = [
        ''.join(lines[source_start(nodes[name]):nodes[name].end_lineno])
        for name in SYMBOL_NAMES
    ]
    module_source = HEADER + '\n\n'.join(pieces)
    module_source = module_source.replace('messages.append({', '_messages.append({')
    module_source = module_source.replace('agent_loop(messages)', '_agent_loop(_messages)')
    MODULE_PATH.write_text(module_source, encoding='utf-8')
    for start, end in sorted(
        ((source_start(node), node.end_lineno) for node in nodes.values()), reverse=True
    ):
        del lines[start:end]
    CLI_PATH.write_text(''.join(lines), encoding='utf-8')


if __name__ == '__main__':
    main()
