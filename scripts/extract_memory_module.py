"""Mechanically extract long-term memory functions from the modular CLI."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = ROOT / 'src' / 'mini_claude_code' / 'cli.py'
MODULE_PATH = ROOT / 'src' / 'mini_claude_code' / 'memory_manager.py'

FUNCTION_NAMES = [
    'write_memory_file',
    'rebuild_memory_index',
    'list_memory_files',
    'read_memory_file',
    'select_relevant_memories',
    'load_memories',
    'extract_memories',
    'consolidate_memories',
]

HEADER = '''from pathlib import Path
import json
import re
import time

from .skill_loader import parse_frontmatter


WORKDIR = Path.cwd().resolve()
MEMORY_DIR = WORKDIR / '.memory'
MEMORY_INDEX = MEMORY_DIR / 'MEMORY.md'
MEMORY_DIR.mkdir(parents=True, exist_ok=True)

MEMORY_TYPES = [
    'user',
    'feedback',
    'project',
    'reference',
]
CONSOLIDATE_THRESHOLD = 10
CONSOLIDATE_INTERVAL = 24 * 60 * 60
CONSOLIDATE_MARKER = MEMORY_DIR / '.last_consolidated'

_client = None
_model = None
_extract_text = None


def configure_memory_runtime(client, model, extract_text):
   global _client
   global _model
   global _extract_text

   _client = client
   _model = model
   _extract_text = extract_text


'''


def main() -> None:
    source = CLI_PATH.read_text(encoding='utf-8')
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
        raise RuntimeError(f'Missing functions in cli.py: {missing}')

    function_sources = []
    for name in FUNCTION_NAMES:
        node = nodes[name]
        function_sources.append(''.join(lines[node.lineno - 1:node.end_lineno]))

    module_source = HEADER + '\n\n'.join(function_sources)
    module_source = module_source.replace('client.messages.create', '_client.messages.create')
    module_source = module_source.replace('model=MODEL', 'model=_model')
    module_source = module_source.replace('extract_text(', '_extract_text(')
    MODULE_PATH.write_text(module_source, encoding='utf-8')

    ranges = sorted(
        ((node.lineno - 1, node.end_lineno) for node in nodes.values()),
        reverse=True,
    )
    for start, end in ranges:
        del lines[start:end]

    CLI_PATH.write_text(''.join(lines), encoding='utf-8')


if __name__ == '__main__':
    main()
