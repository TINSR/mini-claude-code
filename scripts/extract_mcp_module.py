"""Mechanically extract the stdio MCP client and dynamic tool discovery."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = ROOT / 'src' / 'mini_claude_code' / 'cli.py'
MODULE_PATH = ROOT / 'src' / 'mini_claude_code' / 'mcp_client.py'
SYMBOL_NAMES = [
    'MCPClient',
    'connect_mcp',
    'normalize_mcp_name',
    'make_mcp_handler',
    'assemble_tool_pool',
]

HEADER = '''from pathlib import Path
import json
import re
import subprocess
import sys


WORKDIR = Path.cwd().resolve()
mcp_clients = {}
MCP_SERVERS = {
   'baidu': [
      sys.executable,
      str(WORKDIR / 'mcp_servers' / 'web_search_server.py'),
   ],
}

_tools = []
_tool_handlers = {}
_tool_annotations = {}


def configure_mcp_runtime(tools, tool_handlers, tool_annotations):
   global _tools
   global _tool_handlers
   global _tool_annotations

   _tools = tools
   _tool_handlers = tool_handlers
   _tool_annotations = tool_annotations


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
        raise RuntimeError(f'Missing MCP symbols: {missing}')
    pieces = [
        ''.join(lines[nodes[name].lineno - 1:nodes[name].end_lineno])
        for name in SYMBOL_NAMES
    ]
    module_source = HEADER + '\n\n'.join(pieces)
    module_source = module_source.replace('tools = list(TOOLS)', 'tools = list(_tools)')
    module_source = module_source.replace('handlers = dict(TOOL_HANDLERS)', 'handlers = dict(_tool_handlers)')
    module_source = module_source.replace('MCP_TOOL_ANNOTATIONS[full_name]', '_tool_annotations[full_name]')
    MODULE_PATH.write_text(module_source, encoding='utf-8')
    for start, end in sorted(
        ((node.lineno - 1, node.end_lineno) for node in nodes.values()), reverse=True
    ):
        del lines[start:end]
    CLI_PATH.write_text(''.join(lines), encoding='utf-8')


if __name__ == '__main__':
    main()
