import json
from pathlib import Path

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


def assemble_system_prompt(context):
   sections = []

   sections.append(PROMPT_SECTIONS["identity"])

   sections.append(PROMPT_SECTIONS["behavior"])

   enabled_tools = context.get("enabled_tools",[])

   if len(enabled_tools) > 0:
      tools_text = ", ".join(
         enabled_tools
      )

      sections.append(
         f"当前可用工具：{tools_text}。"
      )

   workspace = context.get(
        "workspace",
        str(WORKDIR)
    )

   sections.append(
        f"当前工作目录：{workspace}。"
    )

   memories = context.get("memories","")

   if memories != "":
      sections.append(PROMPT_SECTIONS["memory"])

      sections.append(
         "当前长期记忆索引：\n"
         + memories
      )

   return "\n\n".join(sections)


def update_context():
   memories = ""

   if MEMORY_INDEX.exists():
      memory_content = (MEMORY_INDEX.read_text(encoding="utf-8").strip())

      if memory_content != "":
         memories = memory_content

   context = {
        "enabled_tools": list(
            _tool_handlers.keys()
        ),
        "workspace": str(
            WORKDIR
        ),
        "memories": memories,
    }

   return context


def get_system_prompt(context):
   global _last_context_key
   global _last_system_prompt

   # Tool definitions already describe available tools. Retrieved memories are
   # appended to each new user turn by the CLI; neither rewrites the system prefix.
   context = {"workspace": context.get("workspace", str(WORKDIR))}

   context_key = json.dumps(
      context,
      sort_keys=True,
      ensure_ascii=False,
      default=str,
   )

   if (
      context_key == _last_context_key
      and _last_system_prompt is not None
    ):
        print(
            "[System Prompt] 复用本地稳定提示词（非 API 缓存统计）"
        )

        return _last_system_prompt

   prompt = assemble_system_prompt(context)

   _last_context_key = context_key
   _last_system_prompt = prompt

   print("[System Prompt] 已重新组装")

   return prompt
