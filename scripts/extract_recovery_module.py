"""Mechanically extract API retry and recovery policy from the CLI."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = ROOT / 'src' / 'mini_claude_code' / 'cli.py'
MODULE_PATH = ROOT / 'src' / 'mini_claude_code' / 'recovery.py'
SYMBOL_NAMES = ['RecoveryState', 'retry_delay', 'call_with_retry']

HEADER = '''import os
import random
import time


PRIMARY_MODEL = None
FALLBACK_MODEL = os.getenv('FALLBACK_MODEL_ID')
DEFAULT_MAX_TOKENS = 3000
ESCALATED_MAX_TOKENS = 8000
MAX_RECOVERY_RETRIES = 3
MAX_API_RETRIES = 10
BASE_RETRY_DELAY = 0.5
MAX_CONSECUTIVE_529 = 3
CONTINUATION_PROMPT = (
    '上一次回答因为输出长度限制而中断。'
    '请直接从中断处继续，不要道歉，'
    '也不要重复前面的内容。'
)


def configure_recovery(primary_model):
   global PRIMARY_MODEL
   PRIMARY_MODEL = primary_model


'''


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
        raise RuntimeError(f'Missing recovery symbols: {missing}')
    pieces = [
        ''.join(lines[nodes[name].lineno - 1:nodes[name].end_lineno])
        for name in SYMBOL_NAMES
    ]
    module_source = HEADER + '\n\n'.join(pieces)
    # Compatibility fix: the learning baseline placed this raise inside the
    # retry loop, which caused every transient failure to stop after attempt 1.
    module_source = module_source.replace(
        '      raise RuntimeError("API 重试次数已用完")',
        '   raise RuntimeError("API 重试次数已用完")',
    )
    MODULE_PATH.write_text(module_source, encoding='utf-8')
    for start, end in sorted(
        ((node.lineno - 1, node.end_lineno) for node in nodes.values()), reverse=True
    ):
        del lines[start:end]
    CLI_PATH.write_text(''.join(lines), encoding='utf-8')


if __name__ == '__main__':
    main()
