"""Mechanically extract dynamic system-prompt assembly from the CLI."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = ROOT / 'src' / 'mini_claude_code' / 'cli.py'
MODULE_PATH = ROOT / 'src' / 'mini_claude_code' / 'prompt_manager.py'
FUNCTION_NAMES = ['assemble_system_prompt', 'update_context', 'get_system_prompt']

HEADER = '''from pathlib import Path
import json

from .memory_manager import MEMORY_INDEX


WORKDIR = Path.cwd().resolve()
PROMPT_SECTIONS = {
   'identity': (
      '你是一个编程 Agent。'
      '请主动完成任务，不要只解释应该怎么做。'
   ),
   'behavior': (
      '需要时请使用工具。'
      '修改文件之前，先读取并检查文件内容。'
      '当用户要求你记住某件事时，不要自行创建 '
      'CLAUDE.md 或其他记忆文件。'
      '每轮对话结束后，记忆系统会自动保存重要信息。'
   ),
   'memory': (
      '请遵守长期记忆中与当前任务相关的用户偏好和项目事实。'
   ),
}

_tool_handlers = {}
_last_context_key = None
_last_system_prompt = None


def configure_prompt_runtime(tool_handlers):
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
        raise RuntimeError(f'Missing prompt functions: {missing}')

    pieces = [
        ''.join(lines[nodes[name].lineno - 1:nodes[name].end_lineno])
        for name in FUNCTION_NAMES
    ]
    module_source = HEADER + '\n\n'.join(pieces)
    module_source = module_source.replace('TOOL_HANDLERS.keys()', '_tool_handlers.keys()')
    MODULE_PATH.write_text(module_source, encoding='utf-8')

    for start, end in sorted(
        ((node.lineno - 1, node.end_lineno) for node in nodes.values()),
        reverse=True,
    ):
        del lines[start:end]
    CLI_PATH.write_text(''.join(lines), encoding='utf-8')


if __name__ == '__main__':
    main()
