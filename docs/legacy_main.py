import os
import yaml
from dotenv import load_dotenv
from datetime import datetime
import subprocess
import re
import sys
import threading
from pathlib import Path
from dataclasses import (
   dataclass,
   asdict,
   field,
)
import random
import json
from anthropic import (
    Anthropic,
    BadRequestError,
)
import time
load_dotenv()

client = Anthropic(
    api_key=os.getenv("ANTHROPIC_API_KEY"),
    base_url=os.getenv("ANTHROPIC_BASE_URL"),
)

MODEL = os.getenv("MODEL_ID")
#缓存
_last_context_key = None
_last_system_prompt = None

BASE_SYSTEM = f"""
你是一个运行在 Windows 上的 Coding Agent。
当前工作目录是：{os.getcwd()}。

需要查看或修改文件时，请优先使用专用工具。

遇到复杂任务时：
1. 使用 todo_write 创建任务计划；
2. 每次只能有一个任务处于 in_progress；
3. 完成任务后更新状态。

对于复杂且相对独立的子任务，可以使用 task 委派：
1. 调查多个文件；
2. 独立分析某个模块；
3. 中间过程很多，但主 Agent 只需要结论。

简单任务不要创建 Todo，也不要委派子 Agent。
"""

PROMPT_SECTIONS = {
   "identity": (
      "你是一个编程 Agent。"
      "请主动完成任务，不要只解释应该怎么做。"
   ),

   "behavior": (
      "需要时请使用工具。"
      "修改文件之前，先读取并检查文件内容。"
      "当用户要求你记住某件事时，不要自行创建 "
      "CLAUDE.md 或其他记忆文件。"
      "每轮对话结束后，记忆系统会自动保存重要信息。"
   ),

   "memory": (
      "请遵守长期记忆中与当前任务相关的用户偏好和项目事实。"
   ),
}

SUB_SYSTEM = f"""
你是一个运行在 Windows 上的子 Coding Agent。
当前工作目录是：{os.getcwd()}。

你只负责完成主 Agent 委派给你的一个子任务。
请直接调查或执行，不要创建 Todo，也不要继续委派其他 Agent。
任务完成后返回简洁、准确的结论。
"""

CURRENT_TODOS = []

TOOLS = [
   {
      "name":"powershell",
      "description": "Run a shell command on the computer.",
      "input_schema":{
         "type": "object",
         "properties": {
            "command": {
              "type": "string"
            },
         "run_in_background": {
            "type": "boolean",
            "description": (
            "是否在后台运行该命令。"
            "预计耗时较长时设为 true。"
               )
            }
          },
          "required": ["command"]
      }
   },
   {
      "name":"read_file",
      "description": "Read the contents of a UTF-8 text file.",
      "input_schema":{
         "type": "object",
         "properties": {
            "path": {
               "type": "string"
            },
            "limit":{
               "type":"integer"
            }
            },
         "required": ["path"]
      }
   },
   {
      "name": "write_file",
      "description": "Create or overwrite a UTF-8 text file.",
      "input_schema":{
         "type":"object",
         "properties": {
            "path": {
                "type": "string"
            },
            "content": {
                "type": "string"
            }
        },
        "required": ["path", "content"]
      }
   },
   {
      "name":"edit_file",
      "description": "Replace the first occurrence of text in a UTF-8 file.",
      "input_schema":{
         "type": "object",
         "properties": {
            "path": {
               "type": "string"
            },
            "old_text": {
               "type": "string"
            },
            "new_text": {
               "type": "string"
            }
            },
            "required": ["path","old_text","new_text"]
      }
   },
   {
      "name":"glob",
      "description": "Find files in the working directory using a glob pattern.",
      "input_schema":{
         "type": "object",
         "properties": {
            "pattern": {
               "type": "string"
            },
            },
            "required": ["pattern"]
      }
   },
   {
      "name": "todo_write",
      "description": (
        "Create or update the task list. "
        "Use this before starting a complex task "
        "and update task statuses during execution."
      ),
      "input_schema":{
         "type": "object",
         "properties":{
            "todos":{
               "type": "array",
               "items": {
                  "type": "object",
                  "properties": {
                     "content":{
                        "type": "string"
                     },
                     "status":{
                        "type": "string",
                        "enum":[
                           "pending",
                           "in_progress",
                           "completed"
                           ]
                     }
                  },
                  "required":[
                     "content",
                     "status"
                  ]
               }
            }
         },
         "required":["todos"]
      }
   },
   {
      "name": "task",
      "description": (
         "Launch a subagent to handle one complex, "
         "independent subtask. "
         "The subagent returns only its final conclusion."
         ),
      "input_schema": {
         "type": "object",
         "properties": {
               "description": {
                  "type": "string"
               }
         },
         "required": ["description"]
      }
   },
   {
      "name": "load_skill",
      "description": (
         "Load the complete instructions of an available Skill. "
         "Use the Skill catalog in the system prompt to choose a name."
      ),
      "input_schema": {
         "type": "object",
         "properties": {
               "name": {
                  "type": "string"
               }
         },
         "required": ["name"]
      }
   }
      
]



SUB_TOOL_NAMES = {
    "powershell",
    "read_file",
    "write_file",
    "edit_file",
    "glob",
}

SUB_TOOLS = [
   tool
   for tool in TOOLS
   if tool["name"] in SUB_TOOL_NAMES
]



WORKDIR = Path.cwd().resolve()

DENY_LIST = [
    "stop-computer",
    "restart-computer",
    "format-volume",
    "clear-disk",
    "shutdown",
    "remove-item -recurse -force c:\\",
]

AUTO_ALLOW_TOOLS = [
    "read_file",
    "glob",
]

HOOKS = {
    "UserPromptSubmit": [],
    "PreToolUse": [],
    "PostToolUse": [],
    "Stop": [],
}

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
            TOOL_HANDLERS.keys()
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
            "[System Prompt] 使用缓存"
        )

        return _last_system_prompt

   prompt = assemble_system_prompt(context)

   _last_context_key = context_key
   _last_system_prompt = prompt

   print("[System Prompt] 已重新组装")

   return prompt

def spawn_subagent(description):
   print("\n[Subagent spawned]")
   print("子任务：", description)

   sub_messages = [
        {
            "role": "user",
            "content": description
        }
    ]
   for round_number in range(30):
      response = client.messages.create(
            model=MODEL,
            system=SUB_SYSTEM,
            tools=SUB_TOOLS,
            messages=sub_messages,
            max_tokens=3000,
        )
      sub_messages.append({
         "role":"assistant",
         "content":response.content
      })

      tool_blocks=[
         block
         for block in response.content
         if  block.type == "tool_use" 
      ]

      if len(tool_blocks)==0:
         conclusion = extract_text(
            response.content
         )

         print("[Subagent done]\n")
         return conclusion

      result = []

      for block in tool_blocks:
         print(f"[sub] 调用工具：{block.name}")

         blocked = trigger_hooks("PreToolUse",block)

         if blocked is not None:
            output = str(blocked)
         else:
            handler = SUB_HANDLERS.get(block.name)
            if handler is None:
               output = (
                  f"错误：子 Agent 没有工具 "
                  f"{block.name}"
                  )
            else:
               output = handler(
                  **block.input
               )
               trigger_hooks(
                     "PostToolUse",
                     block,
                     output
                  )
         result.append({
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": output,
         })
      sub_messages.append({
         "role":"user",
         "content":result
      })
   return "子 Agent 达到最大运行轮数，未能完成任务"


def extract_text(content):
   text_parts = []

   if isinstance(content,str):
      return content

   for block in content:
      if getattr(block,"type",None)=="text":
         text_parts.append(block.text)

   if len(text_parts)==0:
      return "子 Agent 没有返回文本结论"

   return "\n".join(text_parts)
   


def run_todo_write(todos):
   global CURRENT_TODOS

   CURRENT_TODOS = todos
   lines = ["\n## 当前任务"]

   for todo in CURRENT_TODOS:
      status = todo["status"]

      icons = {
         "pending":" ",
         "in_progress": "▸",
         "completed": "✓",
      }

      icon = icons[status]

      lines.append(
         f"[{icon}] {todo['content']}"
      )

   print("\n".join(lines))
   return f"已更新 {len(CURRENT_TODOS)} 个任务"


def register_hook(event, callback):
    HOOKS[event].append(callback)

def trigger_hooks(event, *args):
    for callback in HOOKS[event]:
        result = callback(*args)

        if result is not None:
            return result

    return None

def log_hook(block):
    print(f"[HOOK] 即将调用工具：{block.name}")

    return None

def check_deny_list(command):
   normalized_command = command.lower()

   for pattern in DENY_LIST:
      if pattern in normalized_command:
         return f"禁止执行：命令包含危险操作 {pattern}"

   return None

def check_permission(tool_name, tool_input):
   if tool_name =="powershell":
      command = tool_input.get("command","")
      reason = check_deny_list(command)
      if reason is not None:
         print(reason)
         return "deny" 

   reason=check_rules(tool_name,tool_input)

   if reason is not None:
      print("需要审批：",reason)
      return "ask"

   return "allow"


def permission_hook(block):
    permission = check_permission(block.name,block.input)
    if permission == "deny":
        return "权限系统拒绝执行该工具"

    if permission == "ask":
        confirm = input("允许执行吗？(y/n) ")

        if confirm.lower() != "y":
            return "用户拒绝执行该工具"

    return None

def user_prompt_hook(query):
    print(f"[HOOK] 收到用户输入，当前工作目录：{WORKDIR}")
    return None

def post_tool_log_hook(block, output):
    output_length = len(str(output))
    print(f"[HOOK] 工具执行完成：{block.name}，输出长度：{output_length}")
    return None

def stop_summary_hook(messages):
    tool_count = 0

    for message in messages:
        content = message.get("content")

        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "tool_result":
                    tool_count += 1

    print(f"[HOOK] Agent 即将停止，本次会话累计调用工具：{tool_count} 次")
    return None

def check_rules(tool_name, tool_input):
    if tool_name in ("write_file", "edit_file"):
        return "该工具会修改文件"

    if tool_name.startswith('mcp__'):
        annotations = MCP_TOOL_ANNOTATIONS.get(
            tool_name,
            {},
        )

        if annotations.get('readOnlyHint') is True:
            return None

        if annotations.get('destructiveHint') is True:
            return '该 MCP 工具声明会执行破坏性操作'

        return '该 MCP 外部工具没有声明为只读操作'

    if tool_name == "powershell":
        command = tool_input.get(
            "command",
            ""
        ).lower()

        risky_keywords = [
            "remove-item",
            "set-content",
            "move-item",
            "rename-item",
            "new-item",
            "start-process",
            "invoke-webrequest",
        ]

        for keyword in risky_keywords:
            if keyword in command:
                return f"PowerShell 命令包含敏感操作：{keyword}"

    return None

def safe_path(path, base_dir=None):
   if base_dir is None:
      base = WORKDIR
   else:
      base = Path(base_dir).resolve()

   target = (base / path).resolve()

   if target != base and base not in target.parents:
      raise ValueError("不允许访问当前工作目录之外的文件")

   return target

def run_powershell(command,run_in_background=False,cwd=None):
   result = subprocess.run(
      ["powershell", "-NoProfile", "-Command", command],
      cwd=cwd if cwd is not None else os.getcwd(),
      capture_output=True,#表示不要让 PowerShell 直接把内容打印到屏幕，而是把结果交给Python保存。
      text=True,
      timeout=60,
   )

   output = (result.stdout or "") + (result.stderr or "")

   if output.strip() == "":
      return "(没有输出)"
   else:
      return output.strip()
def run_read(path,limit=None,base_dir=None):
   target = safe_path(path,base_dir)
   if not target.exists():
      return f"错误：文件不存在：{path}"
   if not target.is_file():
      return f"错误：这不是文件：{path}"

   lines = target.read_text(encoding="utf-8").splitlines()

   if limit is not None:
      lines = lines[:limit]

   return "\n".join(lines)

def run_write(path,content,base_dir=None):
   target = safe_path(path,base_dir)

   target.parent.mkdir(
      parents=True,
      exist_ok=True
   )
   target.write_text(
    content,
    encoding="utf-8"
   )
   return f"成功写入文件：{path}"

def run_edit(path,old_text,new_text):
   target = safe_path(path)

   if not target.exists():
      return f"错误：文件不存在：{path}"
   if not target.is_file():
      return f"错误：这不是文件：{path}"

   text = target.read_text(encoding="utf-8")

   if old_text not in text:
      return "错误：没有找到需要替换的内容"

   new_content = text.replace(old_text,new_text,1)

   target.write_text(new_content,encoding="utf-8")

   return f"成功编辑文件：{path}"

def run_glob(pattern,base_dir=None):
   pattern_path = Path(pattern)

   if pattern_path.is_absolute():
      return "错误：不允许使用绝对路径查找"
   if ".." in pattern_path.parts:
      return "错误：不允许查找工作目录之外的文件"
   if base_dir is None:
      base = WORKDIR
   else:
      base = Path(base_dir).resolve()

   matches = []

   for path in base.glob(pattern):
      relative_path = path.relative_to(base)
      matches.append(str(relative_path))

   if len(matches) == 0:
      return "没有找到匹配的文件"

   return "\n".join(matches[:200])


TOOL_HANDLERS = {
   "powershell":run_powershell,
   "read_file":run_read,
   "write_file": run_write,
   "edit_file":run_edit,
   "glob":run_glob,
   "todo_write": run_todo_write,
   "task": spawn_subagent
}

SUB_HANDLERS = {
    name: TOOL_HANDLERS[name]
    for name in SUB_TOOL_NAMES
}


#-----------------------skill-------------------------------

SKILLS_DIR = WORKDIR / "skills"

SKILL_REGISTRY = {}

def parse_frontmatter(text):
   if not text.startswith("---"):
      return {},text

   parts = text.split("---",2)

   if len(parts)<3:
      return {},text

   try:
      meta = yaml.safe_load(parts[1]) or {}
   except yaml.YAMLError:
      meta = {}

   body = parts[2].strip()#strip() 删除正文开头和结尾多余的空格、空行

   return meta,body

def scan_skills():
   if not SKILLS_DIR.exists():
      return

   for skill_dir in sorted(SKILLS_DIR.iterdir()):
      if not skill_dir.is_dir():
         continue

      skill_file = skill_dir/"SKILL.md"

      if not skill_file.exists():
         continue

      raw = skill_file.read_text(encoding="utf-8")

      meta,body = parse_frontmatter(raw)

      name = meta.get("name",skill_dir.name)

      description = meta.get("description","没有描述")

      SKILL_REGISTRY[name] = {
         "name": name,
         "description": description,
         "content": raw,
      }

scan_skills()

def list_skills():
   if len(SKILL_REGISTRY)==0:
      return "- 暂无可用 Skill"

   lines=[]

   for skill in SKILL_REGISTRY.values():
      lines.append(
         f"- {skill['name']}: "
         f"{skill['description']}"
      )

   return "\n".join(lines)

def build_system():
    skill_catalog = list_skills()

    return (
        BASE_SYSTEM
        + "\n\n当前可用 Skills：\n"
        + skill_catalog
        + "\n\n需要某个 Skill 的完整说明时，"
        + "请先调用 load_skill 工具。"
    )


SYSTEM = build_system()

def load_skill(name):
   skill = SKILL_REGISTRY.get(name)

   if skill is None:
      return f"未找到 Skill：{name}"

   return skill["content"]

TOOL_HANDLERS["load_skill"] = load_skill

def run_compact(focus=''):
   return (
      '上下文压缩请求已接收。'
      + (f'重点：{focus}' if focus else '')
   )

TOOLS.append({
   'name': 'compact',
   'description': (
      '压缩较早的对话历史，并使用摘要继续当前任务'
   ),
   'input_schema': {
      'type': 'object',
      'properties': {
         'focus': {
            'type': 'string',
            'description': '压缩摘要需要重点保留的信息',
         },
      },
      'required': [],
   },
})

TOOL_HANDLERS['compact'] = run_compact

#-----------------------skill-------------------------------

#-----------------------上下文管理---------------------------

CONTEXT_LIMIT = 50_000
KEEP_RECENT_TOOL_RESULTS = 3
PERSIST_THRESHOLD = 30_000

TOOL_RESULTS_DIR = (
   WORKDIR
   / ".task_outputs"
   / "tool-results"
)

TRANSCRIPT_DIR = (
    WORKDIR / ".transcripts"
)

def estimate_size(messages):
   return len(str(messages))

def block_type(block):
   if isinstance(block,dict):
      return block.get("type")

   return getattr(block,"type",None)

def message_has_tool_use(message):
   if message.get("role") != "assistant":
      return False

   content = message.get("content")
   if not isinstance(content, list):
        return False
   for block in content:
      if block_type(block) == "tool_use":
         return True

   return False

def is_tool_result_message(message):
   if message.get("role") != "user":
      return False

   content = message.get("content")

   if not isinstance(content, list):
      return False

   for block in content:
      if block_type(block) == "tool_result":
         return True

   return False

def snip_compact(messages, max_messages=50):
   if len(messages) <= max_messages:
      return messages

   keep_head = 3
   keep_tail = max_messages - keep_head
   tail_start = len(messages)-keep_tail
   head_end = keep_head#头部三条加上尾部47条
   # 防止头部结束在 tool_use 和 tool_result 中间
   if head_end > 0 and message_has_tool_use(messages[head_end - 1]):
      while head_end < len(messages) and is_tool_result_message(messages[head_end]):
         head_end += 1

   # 防止尾部开始在 tool_use 和 tool_result 中间
   if (
      tail_start > 0
      and tail_start < len(messages)
      and is_tool_result_message(
         messages[tail_start]
      )
      and message_has_tool_use(
         messages[tail_start - 1]
      )
    ):
      tail_start -= 1

   if head_end >= tail_start:
        return messages

   snipped_count = (tail_start - head_end)

   placeholder = {
      "role": "user",
      "content": (
         f"[中间有 {snipped_count} 条旧消息"
         f"已被压缩]"
      )
   }
   return (
      messages[:head_end]
      + [placeholder]
      + messages[tail_start:]
   )

def collect_tool_results(messages):
   tool_results = []
   for message in messages:
      content = message.get("content")

      if not isinstance(content,list):
         continue

      for block in content:
         if block_type(block) == "tool_result":
            tool_results.append(block)

   return tool_results

def micro_compact(messages):
   tool_results = collect_tool_results(messages)
   if(len(tool_results)<=KEEP_RECENT_TOOL_RESULTS):
      return messages

   old_results=tool_results[:-KEEP_RECENT_TOOL_RESULTS]

   for block in old_results:
      content = str(block.get("content",""))

      if len(content)>120:
         block["content"]=("[较早的工具结果已压缩，需要时请重新运行工具]")

   return messages

def persist_large_output(tool_use_id,output):
   if len(output) <= PERSIST_THRESHOLD:
      return output

   TOOL_RESULTS_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

   path = (
        TOOL_RESULTS_DIR
        / f"{tool_use_id}.txt"
   )

   if not path.exists():
      path.write_text(
         output,
         encoding="utf-8"
      )

   preview = output[:2000]

   return (
      "<persisted-output>\n"
      f"完整结果保存在：{path}\n"
      f"预览：\n{preview}\n"
      "</persisted-output>"
   )

def tool_result_budget(messages,max_size=200_000):
   if len(messages)==0:
      return messages

   last_message = messages[-1]

   if last_message.get("role")!= "user":
      return messages

   content = last_message.get("content")

   if not isinstance(content,list):
      return messages

   blocks = []

   for block in content:
      if block_type(block) == "tool_result":
            blocks.append(block)

   total_size = 0
   for block in blocks:
      total_size += len(
         str(block.get("content", ""))
      )

   if total_size <= max_size:
      return messages

   ranked_blocks = sorted(
      blocks,
      key=lambda block: len(
         str(block.get("content", ""))
      ),
      reverse=True
   )

   for block in ranked_blocks:
      if total_size <= max_size:
         break

      old_content = str(
         block.get("content", "")
      )
      if len(old_content) <= PERSIST_THRESHOLD:
         continue

      tool_use_id = block.get(
         "tool_use_id",
         "unknown"
      )
      block["content"] = (persist_large_output(tool_use_id,old_content))
      total_size = 0
      for current_block in blocks:
         total_size += len(str(current_block.get("content","")))

   return messages

def write_transcript(messages):
   TRANSCRIPT_DIR.mkdir(
      parents=True,
      exist_ok=True
   )
   timestamp = int(time.time())
   path = (
      TRANSCRIPT_DIR
      / f"transcript_{timestamp}.jsonl"
   )
   with path.open("w",encoding="utf-8") as file:
      for message in messages:
         line = json.dumps(
            message,
            ensure_ascii=False,
            default=str
         )
         file.write(line + "\n")
   return path

def summarize_history(messages):
   conversation = json.dumps(
      messages,
      ensure_ascii=False,
      default=str
   )
   conversation = conversation[:80_000]

   prompt = f"""
      请总结下面的 Coding Agent 对话历史。

      必须保留：
      1. 用户当前目标；
      2. 用户提出的重要约束；
      3. 已经完成的工作；
      4. 修改或创建过的文件；
      5. 重要发现和错误；
      6. 尚未完成的下一步。

      只输出摘要，不要调用工具。

      对话历史：
      {conversation}
   """

   response = client.messages.create(
      model=MODEL,
      system=(
         "你负责压缩对话历史。"
         "只返回准确、简洁的文字摘要，"
         "不要调用任何工具。"
      ),
      messages=[
         {
            "role": "user",
            "content": prompt
         }
      ],
      max_tokens=3000,
    )

   return extract_text(response.content)

def compact_history(messages):
   transcript_path = write_transcript(messages)
   print(
      f"[Compact] 完整历史已保存："
      f"{transcript_path}"
   )
   summary = summarize_history(messages)
   compacted_message = {
      "role": "user",
      "content": (
         "[历史已压缩]\n\n"
         + summary
      )
   }
   return [compacted_message]

def reactive_compact(messages):
   write_transcript(messages)
   tail_start = max(0,len(messages) - 5)
   if (tail_start > 0 and is_tool_result_message(messages[tail_start])
      and message_has_tool_use(messages[tail_start - 1])):
      tail_start-=1
   old_messages = messages[:tail_start]
   recent_messages = messages[tail_start:]
   if len(old_messages) == 0:
      return compact_history(messages)

   summary = summarize_history(old_messages)
   summary_message = {
      "role": "user",
      "content": (
         "[较早历史已应急压缩]\n\n"
         + summary
      )
   }
   return [
      summary_message,
      *recent_messages
   ]

def is_prompt_too_long(error):
   error_text = str(error).lower()

   keywords = [
      "prompt_too_long",
      "prompt too long",
      "context length",
      "too many tokens",
   ]

   for keyword in keywords:
      if keyword in error_text:
         return True

   return False

#-----------------------上下文管理---------------------------

#-----------------------记忆管理-----------------------------

MEMORY_DIR = WORKDIR / ".memory"
MEMORY_INDEX = MEMORY_DIR / "MEMORY.md"

MEMORY_DIR.mkdir(
    parents=True,
    exist_ok=True
)

MEMORY_TYPES = [
    "user",
    "feedback",
    "project",
    "reference",
]

def write_memory_file(name,memory_type,description,body):
   slug = (name.lower().replace(" ","-").replace("/","-"))

   file_path = MEMORY_DIR/f"{slug}.md"

   content = (
      "---\n"
      f"name: {name}\n"
      f"description: {description}\n"
      f"type: {memory_type}\n"
      "---\n\n"
      f"{body}\n"
   )

   file_path.write_text(
      content,
      encoding="utf-8"
   )
   rebuild_memory_index()
   return file_path

def rebuild_memory_index():
   lines = []

   for file_path in sorted(MEMORY_DIR.glob("*.md")):
      if file_path.name == "MEMORY.md":
         continue
      raw = file_path.read_text(encoding="utf8")

      meta,body=parse_frontmatter(raw)

      name = meta.get("name",file_path.stem)
      description = meta.get("description",body[:80])
      line = (
         f"- [{name}]({file_path.name})"
         f" — {description}"
      )
      lines.append(line)

   index_content = "\n".join(lines)

   MEMORY_INDEX.write_text(index_content,encoding="utf-8")

def list_memory_files():
   memories = []

   for file_path in sorted(MEMORY_DIR.glob("*.md")):
      if file_path.name == "MEMORY.md":
         continue

      raw = file_path.read_text(encoding="utf-8")

      meta, body = parse_frontmatter(raw)

      memories.append({
         "filename": file_path.name,
         "name": meta.get("name",file_path.stem),
         "description": meta.get("description",""),
         "type": meta.get("type","user"),
         "body": body,
      })

   return memories

def read_memory_file(filename):
   file_path = MEMORY_DIR / filename

   if not file_path.exists():
        return None

   return file_path.read_text(encoding="utf-8")

def select_relevant_memories(messages,max_items=5):
   memories = list_memory_files()

   if len(memories) == 0:
      return []

   recent_messages = messages[-3:]

   recent_text = str(recent_messages)

   catalog_lines = []

   for index, memory in enumerate(memories):
      catalog_lines.append(
         f"{index}: "
         f"{memory['name']} — "
         f"{memory['description']}"
      )

   catalog = "\n".join(catalog_lines)

   prompt = (
      "请根据最近对话，从记忆目录中选择相关记忆。\n"
      "只返回 JSON 数字列表，例如：[0, 2]。\n"
      "如果没有相关记忆，返回 []。\n\n"
      f"最近对话：\n{recent_text}\n\n"
      f"记忆目录：\n{catalog}"
   )

   try:
      response = client.messages.create(
         model=MODEL,
         messages=[
               {
                  "role": "user",
                  "content": prompt
               }
         ],
         max_tokens=500,
      )

      text = extract_text(response.content).strip()

      match = re.search(r"\[.*?\]",text)

      if match is None:
         return []

      indices = json.loads(match.group())

      selected = []

      for index in indices:
         if (isinstance(index, int) and 0 <= index < len(memories)):
            selected.append(memories[index]["filename"])

         if len(selected) >= max_items:
                break

      return selected

   except Exception:
      return []

def load_memories(messages):
   filenames = select_relevant_memories(messages)

   if len(filenames) == 0:
        return ""

   parts = [
      "<relevant_memories>"
    ]

   for filename in filenames:
      content = read_memory_file(filename)

      if content is not None:
         parts.append(content)

   parts.append(
      "</relevant_memories>"
    )

   return "\n\n".join(parts)

def extract_memories(messages):
   recent_messages = messages[-10:]

   dialogue = str(recent_messages)

   existing_memories = (list_memory_files())

   existing_lines = []

   for memory in existing_memories:
        existing_lines.append(
         f"- {memory['name']}: "
         f"{memory['description']}"
      )

   if len(existing_lines) == 0:
        existing_text = "暂无记忆"
   else:
        existing_text = "\n".join(
            existing_lines
        )

   prompt = (
        "请从对话中提取值得长期保存的信息。\n"
        "包括用户偏好、用户反馈、项目事实和重要参考信息。\n"
        "不要保存普通问题和临时信息。\n"
        "不要重复已有记忆。\n\n"
        "返回 JSON 数组，格式如下：\n"
        "[\n"
        "  {\n"
        '    "name": "记忆名称",\n'
        '    "type": "user",\n'
        '    "description": "一句话简介",\n'
        '    "body": "完整记忆内容"\n'
        "  }\n"
        "]\n"
        "如果没有新记忆，返回 []。\n\n"
        f"已有记忆：\n{existing_text}\n\n"
        f"最近对话：\n{dialogue[:4000]}"
   )

   try:
      response = client.messages.create(
         model=MODEL,
         messages=[
            {
               "role": "user",
               "content": prompt
            }
         ],
         max_tokens=800,
      )

      text = extract_text(
            response.content
      )

      match = re.search(
         r"\[.*\]",
         text,
         re.DOTALL
      )

      if match is None:
            return 0

      json_text = match.group()

      try:
         new_memories = json.loads(json_text)
      except json.JSONDecodeError:
         json_text = json_text.replace("\\'", "'")
         new_memories = json.loads(json_text)

   except Exception as error:
      print(f"[Memory 提取失败] {error}")
        
      return 0

   saved_count = 0
   for memory in new_memories:
      name = memory.get("name")

      memory_type = memory.get("type","user")

      description = memory.get("description","")

      body = memory.get("body","")

      if not name or not body:
            continue

      if memory_type not in MEMORY_TYPES:
         memory_type = "user"

      write_memory_file(
         name=name,
         memory_type=memory_type,
         description=description,
         body=body,
      )

      saved_count += 1

   if saved_count > 0:
      print(
         f"[Memory] 新增了 "
         f"{saved_count} 条记忆"
      )

   return saved_count

CONSOLIDATE_THRESHOLD = 10
CONSOLIDATE_INTERVAL = 24 * 60 * 60
CONSOLIDATE_MARKER = MEMORY_DIR / ".last_consolidated"


def consolidate_memories():
   memories = list_memory_files()

   if len(memories) < CONSOLIDATE_THRESHOLD:
        return

   if CONSOLIDATE_MARKER.exists():
      last_time = CONSOLIDATE_MARKER.stat().st_mtime
      elapsed = time.time() - last_time

      if elapsed < CONSOLIDATE_INTERVAL:
         return

   catalog_parts = []

   for memory in memories:
        catalog_parts.append(
            f"## {memory['filename']}\n"
            f"name: {memory['name']}\n"
            f"type: {memory['type']}\n"
            f"description: "
            f"{memory['description']}\n"
            f"{memory['body']}"
        )

   catalog = "\n\n".join(
        catalog_parts
   )

   prompt = (
        "请整理下面这些长期记忆。\n"
        "要求：\n"
        "1. 合并内容重复的记忆\n"
        "2. 删除过时或互相冲突的记忆\n"
        "3. 最多保留30条记忆\n"
        "4. 优先保留重要的用户偏好\n"
        "5. 返回JSON数组\n\n"
        "每一项格式为：\n"
        "{name, type, description, body}\n\n"
        f"现有记忆：\n{catalog[:16000]}"
   )

   try:
      response = client.messages.create(
         model=MODEL,
         messages=[
               {
                  "role": "user",
                  "content": prompt
               }
         ],
         max_tokens=3000,
      )

      text = extract_text(response.content).strip()

      match = re.search(
            r"\[.*\]",
            text,
            re.DOTALL
      )

      if match is None:
            return

      json_text = match.group()

      try:
         consolidated = json.loads(json_text)
      except json.JSONDecodeError:
         json_text = json_text.replace("\\'", "'")
         consolidated = json.loads(json_text)

      if not isinstance(consolidated, list) or len(consolidated) == 0:
         return

   except Exception as error:
      print(
         f"[Memory 整理失败] {error}"
      )
      return

   for file_path in MEMORY_DIR.glob("*.md"):
      if file_path.name != "MEMORY.md":
         file_path.unlink()

   saved_count = 0

   for memory in consolidated:
      name = memory.get("name")
      memory_type = memory.get(
         "type",
         "user"
      )
      description = memory.get(
            "description",
            ""
      )
      body = memory.get(
            "body",
            ""
      )

      if not name or not body:
            continue

      if memory_type not in MEMORY_TYPES:
            memory_type = "user"

      write_memory_file(
            name=name,
            memory_type=memory_type,
            description=description,
            body=body,
      )

      saved_count += 1

   rebuild_memory_index()
   CONSOLIDATE_MARKER.touch()

   print(
        f"[Memory] 已将 "
        f"{len(memories)} 条记忆"
        f"整理为 {saved_count} 条"
   )

#-----------------------记忆管理-----------------------------
#-----------------------错误处理-----------------------------

PRIMARY_MODEL = MODEL

FALLBACK_MODEL = os.getenv(
   "FALLBACK_MODEL_ID"
)

DEFAULT_MAX_TOKENS = 3000

ESCALATED_MAX_TOKENS = 8000

MAX_RECOVERY_RETRIES = 3

MAX_API_RETRIES = 10

BASE_RETRY_DELAY = 0.5

MAX_CONSECUTIVE_529 = 3

CONTINUATION_PROMPT = (
    "上一次回答因为输出长度限制而中断。"
    "请直接从中断处继续，不要道歉，"
    "也不要重复前面的内容。"
)
class RecoveryState:
    def __init__(self):
      self.has_escalated = False

      self.recovery_count = 0

      self.consecutive_529 = 0

      self.has_attempted_compact = False

      self.current_model = PRIMARY_MODEL

def retry_delay(attempt):
   base_delay = min(BASE_RETRY_DELAY* (2 ** attempt),32)

   jitter = random.uniform(0,base_delay * 0.25)

   return base_delay + jitter

def call_with_retry(request_function,state):
   for attempt in range(MAX_API_RETRIES):
      try:
         response = request_function()

         state.consecutive_529 = 0

         return response

      except Exception as error:
         error_name = (type(error).__name__.lower())

         error_message = (str(error).lower())

         is_rate_limit = (
               "ratelimit" in error_name
               or "429" in error_message
            )

         is_overloaded = (
               "overloaded" in error_name
               or "529" in error_message
               or "overloaded" in error_message
         )

         if not (is_rate_limit or is_overloaded):
               raise

         if is_overloaded:
            state.consecutive_529 += 1

            if (state.consecutive_529>= MAX_CONSECUTIVE_529 and FALLBACK_MODEL):
               state.current_model = (FALLBACK_MODEL)

               state.consecutive_529 = 0

               print("[Recovery] 主模型持续过载，"
                     "切换到备用模型")

            delay = retry_delay(attempt)

            print(
                f"[Recovery] API 暂时不可用，"
                f"{delay:.1f} 秒后重试 "
                f"({attempt + 1}/"
                f"{MAX_API_RETRIES})"
            )

            time.sleep(delay)

      raise RuntimeError("API 重试次数已用完")
#-----------------------错误处理-----------------------------

#-----------------------长任务-------------------------------

TASKS_DIR = WORKDIR / ".tasks"

TASKS_DIR.mkdir(
    parents=True,
    exist_ok=True
)

WORKTREES_DIR = (
   WORKDIR / '.worktrees'
)

WORKTREES_DIR.mkdir(
   exist_ok=True
)

def validate_worktree_name(name):
   if name in ('.', '..'):
      return 'Worktree 名称不合法'

   if not re.fullmatch(
      r'[A-Za-z0-9._-]{1,64}',
      name,
   ):
      return (
         'Worktree 名称只能包含字母、'
         '数字、点、下划线和短横线，'
         '长度为 1 到 64'
      )

   return None

def run_git(arguments):
   try:
      result = subprocess.run(
         ['git'] + arguments,
         cwd=WORKDIR,
         capture_output=True,
         text=True,
         timeout=60,
      )

   except subprocess.TimeoutExpired:
      return (False,'Git 命令执行超时',)

   output = (result.stdout + result.stderr).strip()

   if output == '':
      output = '(没有输出)'

   return (result.returncode == 0,output,)

def log_worktree_event(event_type,worktree_name,task_id='',):
   event = {
      'type': event_type,
      'worktree': worktree_name,
      'task_id': task_id,
      'ts': time.time(),
   }

   events_file = (
      WORKTREES_DIR
      / 'events.jsonl'
   )

   line = json.dumps(event,ensure_ascii=False,)

   with events_file.open(
      'a',
      encoding='utf-8',
   ) as file:
      file.write(line + '\n')

def bind_task_to_worktree(task_id,worktree_name,):
   task = load_task(task_id)

   task.worktree = worktree_name

   save_task(task)

   print(
      f'[Worktree] 任务 {task.id} '
      f'绑定到 {worktree_name}'
   )

def create_worktree(name,task_id='',):
   error = validate_worktree_name(name)

   if error is not None:
      return f'创建失败：{error}'

   path = WORKTREES_DIR / name

   if path.exists():
      return (f'创建失败：{name} 已经存在')

   if task_id != '':
      try:
         load_task(task_id)

      except FileNotFoundError:
         return (
            f'创建失败：找不到任务 '
            f'{task_id}'
         )

   success, output = run_git([
      'worktree',
      'add',
      str(path),
      '-b',
      f'wt/{name}',
      'HEAD',
   ])

   if not success:
      return (
         f'Git 创建 Worktree 失败：'
         f'{output}'
      )

   if task_id != '':
      bind_task_to_worktree(task_id,name,)

   log_worktree_event('create',name,task_id,)

   return (
      f'Worktree {name} 创建成功\n'
      f'目录：{path}\n'
      f'分支：wt/{name}'
   )

def count_worktree_changes(path):
   try:
      status_result = subprocess.run(
         [
            'git',
            'status',
            '--porcelain',
         ],
         cwd=path,
         capture_output=True,
         text=True,
         timeout=30,
      )

      main_head_result = subprocess.run(
         [
            'git',
            'rev-parse',
            'HEAD',
         ],
         cwd=WORKDIR,
         capture_output=True,
         text=True,
         timeout=30,
      )

      main_head = (main_head_result.stdout.strip())

      commit_result = subprocess.run(
         [
            'git',
            'rev-list',
            '--count',
            f'{main_head}..HEAD',
         ],
         cwd=path,
         capture_output=True,
         text=True,
         timeout=30,
      )

   except subprocess.TimeoutExpired:
      return -1, -1

   if (
      status_result.returncode != 0
      or main_head_result.returncode != 0
      or commit_result.returncode != 0
   ):
      return -1, -1

   changed_files = len(
      status_result.stdout.splitlines()
   )

   new_commits = int(
      commit_result.stdout.strip()
      or '0'
   )

   return changed_files, new_commits

def keep_worktree(name):
   error = validate_worktree_name(name)

   if error is not None:
      return f'保留失败：{error}'

   path = WORKTREES_DIR / name

   if not path.exists():
      return (f'保留失败：找不到 {name}')

   log_worktree_event('keep',name,)

   return (
      f'已保留 Worktree {name}\n'
      f'目录：{path}\n'
      f'分支：wt/{name}'
   )

def remove_worktree(name,discard_changes=False,):
   error = validate_worktree_name(name)

   if error is not None:
      return f'删除失败：{error}'

   path = WORKTREES_DIR / name

   if not path.exists():
      return (f'删除失败：找不到 {name}')

   if not discard_changes:
      changed_files, new_commits = (count_worktree_changes(path))

      if (changed_files < 0 or new_commits < 0):
         return (
            '无法确认 Worktree 是否有改动，'
            '因此拒绝删除'
         )

      if (changed_files > 0 or new_commits > 0):
         return (
            f'拒绝删除：存在 '
            f'{changed_files} 个文件改动和 '
            f'{new_commits} 个新提交。'
            '请保留 Worktree，或者明确设置 '
            'discard_changes=True'
         )

   success, output = run_git([
      'worktree',
      'remove',
      str(path),
      '--force',
   ])

   if not success:
      return (f'删除 Worktree 失败：{output}')

   run_git([
      'branch',
      '-D',
      f'wt/{name}',
   ])

   log_worktree_event('remove',name,)

   return (f'已删除 Worktree {name}')


def run_create_worktree(name,task_id='',):
   return create_worktree(name,task_id,)


def run_keep_worktree(name):
   return keep_worktree(name)


def run_remove_worktree(name,discard_changes=False,):
   return remove_worktree(name,discard_changes,)

TOOL_HANDLERS.update({
   'create_worktree': (
      run_create_worktree
   ),
   'keep_worktree': (
      run_keep_worktree
   ),
   'remove_worktree': (
      run_remove_worktree
   ),
})

TOOLS.extend([
   {
      'name': 'create_worktree',
      'description': (
         '创建独立 Git Worktree，'
         '并可绑定到指定任务'
      ),
      'input_schema': {
         'type': 'object',
         'properties': {
            'name': {
               'type': 'string',
               'description': (
                  'Worktree 名称'
               ),
            },
            'task_id': {
               'type': 'string',
               'description': (
                  '可选的任务 ID'
               ),
            },
         },
         'required': ['name'],
      },
   },
   {
      'name': 'keep_worktree',
      'description': (
         '保留 Worktree 及其分支，'
         '等待人工审查'
      ),
      'input_schema': {
         'type': 'object',
         'properties': {
            'name': {
               'type': 'string',
            },
         },
         'required': ['name'],
      },
   },
   {
      'name': 'remove_worktree',
      'description': (
         '删除 Worktree。存在改动时'
         '默认拒绝，除非明确允许丢弃'
      ),
      'input_schema': {
         'type': 'object',
         'properties': {
            'name': {
               'type': 'string',
            },
            'discard_changes': {
               'type': 'boolean',
               'description': (
                  '是否明确丢弃所有改动'
               ),
            },
         },
         'required': ['name'],
      },
   },
])

@dataclass
class Task:
   id: str
   subject: str
   description: str
   status: str
   owner: str | None
   blockedBy: list[str]
   worktree: str | None = None

def task_path(task_id):
   return (TASKS_DIR / f"{task_id}.json")

def save_task(task):
   task_data = asdict(task)

   json_text = json.dumps(task_data,ensure_ascii=False,
indent=2,)

   file_path = task_path(task.id)

   file_path.write_text(json_text,encoding="utf-8",)

def load_task(task_id):
   file_path = task_path(task_id)

   json_text = file_path.read_text(encoding="utf-8")

   task_data = json.loads(json_text)

   return Task(**task_data)

def create_task(subject,description="",blockedBy=None):
   task_id = (
        f"task_{int(time.time())}_"
        f"{random.randint(0, 9999):04d}"
    )

   task = Task(
        id=task_id,
        subject=subject,
        description=description,
        status="pending",
        owner=None,
        blockedBy=blockedBy or [],
   )

   save_task(task)

   return task

def list_tasks():
   tasks = []

   for file_path in sorted(
      TASKS_DIR.glob(
         "task_*.json"
      )
    ):
      json_text = file_path.read_text(encoding="utf-8")

      task_data = json.loads(json_text)

      task = Task(**task_data)

      tasks.append(task)

   return tasks

def get_task(task_id):
   task = load_task(task_id)

   task_data = asdict(task)

   return json.dumps(
        task_data,
        ensure_ascii=False,
        indent=2,
    )

def can_start(task_id):
   task = load_task(task_id)

   for dependency_id in task.blockedBy:
      dependency_path = task_path(dependency_id)

      if not dependency_path.exists():
         return False

      dependency = load_task(dependency_id)

      if dependency.status != "completed":
         return False

   return True

def claim_task(task_id,owner="agent"):
   task = load_task(task_id)

   if task.status != "pending":
      return (
         f"任务当前状态是 "
         f"{task.status}，无法认领"
      )

   if task.owner is not None:
      return (f'任务已经由 {task.owner} 认领')

   if not can_start(task_id):
      return "任务仍被依赖阻塞"

   task.owner = owner
   task.status = "in_progress"

   save_task(task)

   return (
      f"已认领任务："
      f"{task.id} - {task.subject}"
   )

def scan_unclaimed_tasks():
   unclaimed_tasks = []

   for task in list_tasks():
      if task.status != 'pending':
         continue

      if task.owner is not None:
         continue

      if not can_start(task.id):
         continue

      unclaimed_tasks.append(task)

   return unclaimed_tasks

def complete_task(task_id):
   task = load_task(task_id)

   if task.status != "in_progress":
      return (
         f"任务当前状态是 "
         f"{task.status}，无法完成"
      )

   task.status = "completed"

   save_task(task)

   unblocked = []

   for other_task in list_tasks():
      if (
         other_task.status == "pending"
         and task_id
         in other_task.blockedBy
         and can_start(other_task.id)
      ):
         unblocked.append(other_task.subject)

   message = (
        f"已完成任务："
        f"{task.id} - {task.subject}"
    )

   if len(unblocked) > 0:
      message += (
         "\n已解锁任务："
         + ", ".join(unblocked)
      )

   return message

def run_create_task(subject,description="",blockedBy=None):
   task = create_task(
        subject=subject,
        description=description,
        blockedBy=blockedBy,
   )

   return (
        f"已创建任务："
        f"{task.id} - {task.subject}"
   )

def run_list_tasks():
   tasks = list_tasks()

   if len(tasks) == 0:
      return "当前没有任务"

   lines = []

   for task in tasks:
      if task.status == "pending":
         icon = "[ ]"

      elif task.status == "in_progress":
         icon = "[>]"

      elif task.status == "completed":
            icon = "[✓]"

      else:
         icon = "[?]"

      line = (
         f"{icon} {task.id}: "
         f"{task.subject} "
         f"({task.status})"
      )

      if task.owner is not None:
         line += (f" owner={task.owner}")

         if len(task.blockedBy) > 0:
            line += (f" blockedBy="
                f"{task.blockedBy}")

      lines.append(line)

   return "\n".join(lines)

def run_get_task(task_id):
   try:
      return get_task(task_id)

   except FileNotFoundError:
      return (
            f"错误：找不到任务 "
            f"{task_id}"
      )

def run_claim_task(task_id):
   return claim_task(task_id=task_id,owner="agent",)

def run_complete_task(task_id):
   return complete_task(task_id)


TOOL_HANDLERS.update({
   "create_task": run_create_task,
   "list_tasks": run_list_tasks,
   "get_task": run_get_task,
   "claim_task": run_claim_task,
   "complete_task": run_complete_task,
})

TOOLS.extend([
   {
      "name": "create_task",
      "description": "创建持久化任务，可设置依赖的任务ID列表",
      "input_schema": {
         "type": "object",
         "properties": {
            "subject": {"type": "string", "description": "任务标题"},
            "description": {"type": "string", "description": "任务详细说明"},
            "blockedBy": {
               "type": "array",
               "description": "必须先完成的任务ID列表",
               "items": {"type": "string"},
            },
         },
         "required": ["subject"],
      },
   },
   {
      "name": "list_tasks",
      "description": "列出所有任务的状态、负责人和依赖关系",
      "input_schema": {
         "type": "object",
         "properties": {},
      },
   },
   {
      "name": "get_task",
      "description": "根据任务ID查看完整任务信息",
      "input_schema": {
         "type": "object",
         "properties": {
            "task_id": {"type": "string", "description": "任务ID"},
         },
         "required": ["task_id"],
      },
   },
   {
      "name": "claim_task",
      "description": "认领未被阻塞的 pending 任务，并设为 in_progress",
      "input_schema": {
         "type": "object",
         "properties": {
            "task_id": {"type": "string", "description": "任务ID"},
         },
         "required": ["task_id"],
      },
   },
   {
      "name": "complete_task",
      "description": "完成 in_progress 任务，并检查被解锁的下游任务",
      "input_schema": {
         "type": "object",
         "properties": {
            "task_id": {"type": "string", "description": "任务ID"},
         },
         "required": ["task_id"],
      },
   },
])


#-----------------------长任务-------------------------------
#-----------------------后台任务-----------------------------

background_tasks = {}

background_results = {}

background_lock = threading.Lock()

background_counter = 0

def is_slow_operation(tool_name,tool_input):
   if tool_name != "powershell":
      return False

   command = tool_input.get("command","").lower()

   slow_keywords = [
      "pip install",
      "npm install",
      "pytest",
      "build",
      "compile",
      "deploy",
      "docker build",
      "start-sleep",
   ]

   return any(keyword in command
      for keyword in slow_keywords
   )

def should_run_background(tool_name,tool_input):
   if tool_input.get(
        "run_in_background",
        False
   ):
      return True

   return is_slow_operation(tool_name,tool_input)

def execute_background_tool(block):
   tool_name = block.name

   tool_input = dict(block.input)

   tool_input.pop("run_in_background",None)

   handler = TOOL_HANDLERS.get(tool_name)

   if handler is None:
      return (
         f"错误：未知工具 "
         f"{tool_name}"
      )

   return handler(**tool_input)

def start_background_task(block):
   global background_counter

   background_counter += 1

   background_id = (f"bg_{background_counter:04d}")
   command = block.input.get("command","")

   with background_lock:
      background_tasks[background_id] = {
            "tool_use_id": block.id,
            "command": command,
            "status": "running",
        }

   def worker():
      try:
         output = (execute_background_tool(block))

      except Exception as error:
         output = (
               f"后台任务执行失败："
               f"{error}"
         )

      with background_lock:
            background_tasks[background_id]["status"] ="completed"

            background_results[background_id] = output

   thread = threading.Thread(
        target=worker,
        daemon=True,
    )

   thread.start()

   return background_id

def collect_background_results():
   completed_items = []

   with background_lock:
      completed_ids = []

      for background_id, task in (background_tasks.items()):
         if task["status"] == "completed":
            completed_ids.append(background_id)

      for background_id in completed_ids:
         task = background_tasks.pop(background_id)

         output = background_results.pop(background_id,"")

         completed_items.append((background_id,task,output,))

   notifications = []

   for(background_id,task,output) in completed_items:
      notification = (
         "<task_notification>\n"
         f"<task_id>{background_id}</task_id>\n"
         "<status>completed</status>\n"
         f"<command>{task['command']}</command>\n"
         f"<summary>{str(output)[:2000]}</summary>\n"
         "</task_notification>"
      )

      notifications.append(notification)

   return notifications

#-----------------------后台任务-----------------------------
#-----------------------定时任务-----------------------------
SCHEDULED_TASKS_FILE = (
    WORKDIR
    / ".scheduled_tasks.json"
)

scheduled_jobs = {}

cron_queue = []

cron_lock = threading.Lock()

last_fired = {}
agent_lock = threading.Lock()

@dataclass
class CronJob:
   id: str
   cron: str
   prompt: str
   recurring: bool
   durable: bool
#以下三个函数都是校验表达式的，无关紧要
def cron_field_matches(field,value):
   try:
      if field == "*":
         return True

      if "," in field:
         options = field.split(",")

         return any(cron_field_matches(option,value) for option in options)

      if field.startswith("*/"):
         step = int(field[2:])

         if step <= 0:
            return False

         return value % step == 0

      if "-" in field:
         start_text, end_text = (field.split("-", 1))

         start = int(start_text)
         end = int(end_text)

         return start <= value <= end

      return value == int(field)

   except ValueError:
      return False

def cron_matches(cron_expression,current_time):
   fields = (cron_expression.strip().split())

   if len(fields) != 5:
        return False

   minute = fields[0]
   hour = fields[1]
   day = fields[2]
   month = fields[3]
   weekday = fields[4]

   cron_weekday = (current_time.weekday() + 1) % 7

   minute_ok = cron_field_matches(minute,current_time.minute)

   hour_ok = cron_field_matches(hour,current_time.hour)

   day_ok = cron_field_matches(day,current_time.day)

   month_ok = cron_field_matches(month,current_time.month)

   weekday_ok = cron_field_matches(weekday,cron_weekday)

   if not (
        minute_ok
        and hour_ok
        and month_ok
    ):
      return False

   day_is_any = day == "*"
   weekday_is_any = weekday == "*"

   if day_is_any and weekday_is_any:
      return True

   if day_is_any:
        return weekday_ok

   if weekday_is_any:
        return day_ok

   return day_ok or weekday_ok

def validate_cron_field(field,minimum,maximum):
   if field == "*":
      return None

   if field.startswith("*/"):
      step_text = field[2:]

      if not step_text.isdigit():
         return f"无效步长：{field}"

      if int(step_text) <= 0:
         return f"步长必须大于0：{field}"

      return None

   if "," in field:
      for part in field.split(","):
         error = validate_cron_field(part.strip(),minimum,
maximum,)

         if error is not None:
            return error

      return None

   if "-" in field:
      start_text, end_text = (field.split("-", 1))

      if (not start_text.isdigit() or not end_text.isdigit()):
         return f"无效范围：{field}"

        
      start = int(start_text)
      end = int(end_text)

      if (start < minimum or start > maximum or end < minimum or end > maximum):
         return (
               f"{field} 超出范围 "
               f"{minimum}-{maximum}"
         )

      if start > end:
         return f"范围起点大于终点：{field}"

      return None

   if not field.isdigit():
        return f"无效字段：{field}"

   value = int(field)

   if (value < minimum or value > maximum):
      return (
         f"{value} 超出范围 "
         f"{minimum}-{maximum}")

   return None

def validate_cron(cron_expression):
   fields = (cron_expression.strip().split())

   if len(fields) != 5:
      return ( "Cron 表达式必须有5个字段")

   bounds = [
        (0, 59),
        (0, 23),
        (1, 31),
        (1, 12),
        (0, 6),
   ]

   names = [
        "分钟",
        "小时",
        "日期",
        "月份",
        "星期",
    ]

   for field, bound, name in zip(
      fields,
      bounds,
      names,
    ):
      minimum, maximum = bound

      error = validate_cron_field(
         field,
         minimum,
         maximum,
      )

      if error is not None:
         return (
            f"{name}字段错误："
            f"{error}"
         )

   return None

def save_durable_jobs():
   durable_jobs = []

   for job in scheduled_jobs.values():
      if job.durable:
         durable_jobs.append(asdict(job))

   json_text = json.dumps(
      durable_jobs,
      ensure_ascii=False,
      indent=2,
   )

   SCHEDULED_TASKS_FILE.write_text(json_text,encoding="utf-8",)

def load_durable_jobs():
   if not SCHEDULED_TASKS_FILE.exists():
      return

   try:
      json_text = (SCHEDULED_TASKS_FILE.read_text(encoding="utf-8"))

      job_data_list = json.loads(json_text)

      for job_data in job_data_list:
         job = CronJob(**job_data)
         error = validate_cron(job.cron)

         if error is not None:
            print(
               f"[Cron] 跳过无效任务 "
               f"{job.id}：{error}"
            )
            continue

         scheduled_jobs[job.id] = job

   except Exception as error:
      print(
         f"[Cron] 加载失败：{error}"
      )

def schedule_job(cron,prompt,recurring=True,durable=True,):
   error = validate_cron(cron)

   if error is not None:
      return f"错误：{error}"

   job_id = (
      f"cron_"
      f"{random.randint(0, 999999):06d}"
   )

   job = CronJob(
      id=job_id,
      cron=cron,
      prompt=prompt,
      recurring=recurring,
      durable=durable,
   )

   with cron_lock:
      scheduled_jobs[job.id] = job

   if durable:
      save_durable_jobs()

   return job

def cancel_job(job_id):
   with cron_lock:
      job = scheduled_jobs.pop(job_id,None)

   if job is None:
      return (
         f"错误：找不到定时任务 "
         f"{job_id}"
      )

   if job.durable:
      save_durable_jobs()

   return (
      f"已取消定时任务 "
      f"{job_id}"
   )

def run_schedule_cron(
   cron,
   prompt,
   recurring=True,
   durable=True,
):
   result = schedule_job(
      cron,
      prompt,
      recurring,
      durable,
   )

   if isinstance(result, str):
      return f'创建失败：{result}'

   return (
      f'已创建定时任务 {result.id}\n'
      f'时间表达式：{result.cron}\n'
      f'任务内容：{result.prompt}\n'
      f'是否重复：{result.recurring}\n'
      f'是否持久化：{result.durable}'
   )

def run_list_crons():
   with cron_lock:
      jobs = list(scheduled_jobs.values())

   if len(jobs) == 0:
      return '当前没有定时任务'

   lines = []

   for job in jobs:
      recurring_text = (
         '重复执行'
         if job.recurring
         else '仅执行一次'
      )
      durable_text = (
         '持久化'
         if job.durable
         else '仅本次运行'
      )

      lines.append(
         f'{job.id}: {job.cron} '
         f'[{recurring_text}, {durable_text}] '
         f'→ {job.prompt}'
      )

   return '\n'.join(lines)

def run_cancel_cron(job_id):
   return cancel_job(job_id)

TOOL_HANDLERS.update({
   'schedule_cron': run_schedule_cron,
   'list_crons': run_list_crons,
   'cancel_cron': run_cancel_cron,
})

TOOLS.extend([
   {
      'name': 'schedule_cron',
      'description': '创建一个 Cron 定时任务',
      'input_schema': {
         'type': 'object',
         'properties': {
            'cron': {
               'type': 'string',
               'description': '五段 Cron 表达式',
            },
            'prompt': {
               'type': 'string',
               'description': '定时交给 Agent 的任务内容',
            },
            'recurring': {
               'type': 'boolean',
               'description': '是否重复执行',
            },
            'durable': {
               'type': 'boolean',
               'description': '是否保存到磁盘',
            },
         },
         'required': ['cron', 'prompt'],
      },
   },
   {
      'name': 'list_crons',
      'description': '列出当前所有定时任务',
      'input_schema': {
         'type': 'object',
         'properties': {},
      },
   },
   {
      'name': 'cancel_cron',
      'description': '根据任务 ID 取消定时任务',
      'input_schema': {
         'type': 'object',
         'properties': {
            'job_id': {
               'type': 'string',
               'description': '要取消的定时任务 ID',
            },
         },
         'required': ['job_id'],
      },
   },
])

def cron_scheduler_loop():
   while True:
      time.sleep(1)

      current_time = datetime.now()

      minute_marker = (current_time.strftime("%Y-%m-%d %H:%M"))

      with cron_lock:
         jobs = list(scheduled_jobs.values())

         for job in jobs:
            try:
               if not cron_matches(job.cron,current_time):
                     continue

               if (last_fired.get(job.id)== minute_marker):
                     continue

               cron_queue.append(job)

               last_fired[job.id] = minute_marker

               print(
                  f"[Cron] 触发任务 "
                  f"{job.id}："
                  f"{job.prompt}"
               )

               if not job.recurring:
                  scheduled_jobs.pop(job.id,None)

               if job.durable:
                  save_durable_jobs()

            except Exception as error:
                  print(
                     f"[Cron] 任务 "
                     f"{job.id} 检查失败："
                     f"{error}"
                  )

def consume_cron_queue():
   with cron_lock:
      fired_jobs = list(cron_queue)

      cron_queue.clear()

   return fired_jobs

def has_cron_queue():
   with cron_lock:
      return len(cron_queue) > 0
def queue_processor_loop():
   while True:
      time.sleep(0.2)

      if not has_cron_queue():
         continue

      acquired = agent_lock.acquire(blocking=False)

      if not acquired:
         continue

      try:
         fired_jobs = (consume_cron_queue())

         if len(fired_jobs) == 0:
               continue

         for job in fired_jobs:
            messages.append({
               "role": "user",
               "content": (
                  f"[定时任务 {job.id}]\n"
                  f"{job.prompt}"
               ),
            })

            print(
               f"\n[Cron] 自动交付任务 "
               f"{job.id}"
               )

         agent_loop(messages)

      finally:
         agent_lock.release()
#-----------------------定时任务-----------------------------
#-----------------------多agent------------------------------
MAILBOX_DIR = WORKDIR / '.mailboxes'

MAILBOX_DIR.mkdir(
   exist_ok=True
)

class MessageBus:
   def __init__(self, mailbox_dir):
      self.mailbox_dir = mailbox_dir

      self.mailbox_dir.mkdir(exist_ok=True)

      self.lock = threading.Lock()

   def send(self,from_agent,to_agent,content,msg_type='message',metadata=None,):

      message = {
         'from': from_agent,
         'to': to_agent,
         'content': content,
         'type': msg_type,
         'metadata': metadata or {},
         'ts': time.time(),
      }

      inbox = (
         self.mailbox_dir
         / f'{to_agent}.jsonl'
      )

      line = json.dumps(
         message,
         ensure_ascii=False,
      )

      with self.lock:
         with inbox.open(
            'a',
            encoding='utf-8',
         ) as file:
            file.write(line + '\n')
   def read_inbox(self, agent):
      inbox = (
         self.mailbox_dir
         / f'{agent}.jsonl'
      )

      with self.lock:
         if not inbox.exists():
            return []

         lines = inbox.read_text(encoding='utf-8').splitlines()

         inbox.unlink()

      messages = []

      for line in lines:
         if line.strip() == '':
            continue

         try:
            message = json.loads(line)
            messages.append(message)

         except json.JSONDecodeError:
            continue

      return messages

BUS = MessageBus(MAILBOX_DIR)

def run_send_message(to,content,):
   BUS.send('lead',to,content,)

   return f'消息已发送给 {to}'


def run_check_inbox():
   messages = consume_lead_inbox()
   if len(messages) == 0:
      return '收件箱为空'

   lines = []

   for message in messages:
      sender = message.get('from','unknown',)

      content = message.get('content','',)

      lines.append(f'[{sender}] {content[:200]}')

   return '\n'.join(lines)

active_teammates = {}

def spawn_teammate_thread(name,role,prompt,):
   if name in active_teammates:
      return (f'队友 {name} 已经存在')

   system_prompt = (
      f'你是队友 Agent：{name}。'
      f'你的角色是：{role}。'
      '请使用工具完成分配给你的任务。'
      '完成后，将结果发送给 lead。'
   )

   wt_context = {
      'path': None,
   }

   protocol_context = {
      'waiting_plan': None,
   }
   teammate_tool_names = {
      'powershell',
      'read_file',
      'write_file',
      'glob',
      'list_tasks',
      'claim_task',
      'complete_task',
   }

   teammate_tools = [
      tool
      for tool in TOOLS
      if tool['name'] in teammate_tool_names
   ]

   teammate_tools.append({
      'name': 'send_message',
      'description': '向另一个 Agent 发送消息',
      'input_schema': {
         'type': 'object',
         'properties': {
            'to': {
               'type': 'string',
            },
            'content': {
               'type': 'string',
            },
         },
         'required': [
            'to',
            'content',
         ],
      },
   })

   teammate_tools.append({
      'name': 'submit_plan',
      'description': '向 Lead 提交计划并等待审批',
      'input_schema': {
         'type': 'object',
         'properties': {
            'plan': {
               'type': 'string',
            },
         },
         'required': ['plan'],
      },
   })

   def teammate_list_tasks():
      return run_list_tasks()


   def teammate_claim_task(task_id):
      result = claim_task(task_id,owner=name,)

      if result.startswith('已认领任务'):
         task = load_task(task_id)

         if task.worktree is not None:
            worktree_path = (
               WORKTREES_DIR / task.worktree
            ).resolve()

            if worktree_path.exists():
               wt_context['path'] = str(worktree_path)
               print(
                  f'[Worktree] {name} '
                  f'切换到 {worktree_path}'
               )

      return result


   def teammate_complete_task(task_id):
      return complete_task(task_id)

   def teammate_send_message(to,content,):
      BUS.send(name,to,content,)

      return f'消息已发送给 {to}'

   def teammate_submit_plan(plan):
      request_id = new_request_id()

      pending_requests[request_id] = ProtocolState(
         request_id=request_id,
         type='plan_approval',
         sender=name,
         target='lead',
         status='pending',
         payload=plan,
      )

      protocol_context['waiting_plan'] = request_id

      BUS.send(
         name,
         'lead',
         plan,
         'plan_approval_request',
         {
            'request_id': request_id,
         },
      )

      return f'计划已提交，审批请求：{request_id}'

   def teammate_powershell(command,run_in_background=False,):
      return run_powershell(
         command,
         run_in_background,
         cwd=wt_context['path'],
      )

   def teammate_read(path,limit=None,):
      return run_read(
         path,
         limit,
         base_dir=wt_context['path'],
      )

   def teammate_write(path,content,):
      return run_write(
         path,
         content,
         base_dir=wt_context['path'],
      )

   def teammate_glob(pattern):
      return run_glob(
         pattern,
         base_dir=wt_context['path'],
      )

   teammate_handlers = {
      'powershell': teammate_powershell,
      'read_file': teammate_read,
      'write_file': teammate_write,
      'glob': teammate_glob,
      'send_message': teammate_send_message,
      'submit_plan': teammate_submit_plan,
      'list_tasks': teammate_list_tasks,
      'claim_task':teammate_claim_task,
      'complete_task':teammate_complete_task,

   }

   def handle_inbox_message(message, sub_messages):
      message_type = message.get(
         'type',
         'message',
      )

      metadata = message.get('metadata',{},)

      if (message_type== 'shutdown_request'):
         request_id = metadata.get('request_id','',)

         BUS.send(
            name,
            'lead',
            f'{name} 已完成收尾并退出',
            'shutdown_response',
            {
               'request_id': request_id,
               'approve': True,
            },
         )

         print(
            f'[Protocol] {name} '
            f'同意关机请求 {request_id}'
         )

         return True

      if message_type == 'plan_approval_response':
         request_id = metadata.get(
            'request_id',
            '',
         )

         if request_id == protocol_context['waiting_plan']:
            protocol_context['waiting_plan'] = None

         approved = metadata.get(
            'approve',
            False,
         )

         sub_messages.append({
            'role': 'user',
            'content': (
               '[计划已批准]'
               if approved
               else (
                  '[计划被拒绝] '
                  + message.get('content', '')
               )
            ),
         })

      return False

   def run():
      sub_messages = [
         {
            'role': 'user',
            'content': prompt,
         }
      ]

      shutdown_requested = False

      while not shutdown_requested:
         inbox_messages = BUS.read_inbox(name)
         normal_messages = []

         for inbox_message in inbox_messages:
            if handle_inbox_message(
               inbox_message,
               sub_messages,
            ):
               shutdown_requested = True
               break

            if (
               inbox_message.get('type')
               == 'plan_approval_response'
            ):
               continue

            normal_messages.append(inbox_message)

         if shutdown_requested:
            break

         if len(normal_messages) > 0:
            inbox_text = json.dumps(
               normal_messages,
               ensure_ascii=False,
            )

            sub_messages.append({
               'role': 'user',
               'content': f'<inbox>{inbox_text}</inbox>',
            })

         if protocol_context['waiting_plan'] is not None:
            time.sleep(IDLE_POLL_INTERVAL)
            continue

         try:
            response = client.messages.create(
               model=MODEL,
               system=system_prompt,
               messages=sub_messages,
               tools=teammate_tools,
               max_tokens=8000,
            )

         except Exception as error:
            print(
               f'[Teammate] {name} '
               f'请求模型失败：{error}'
            )
            break

         sub_messages.append({
            'role': 'assistant',
            'content': response.content,
         })

         for block in response.content:
            if block.type == 'text':
               print(f'[{name}] {block.text}')

         tool_blocks = [
            block
            for block in response.content
            if block.type == 'tool_use'
         ]

         if len(tool_blocks) > 0:
            tool_results = []
            plan_submitted = False

            for block in tool_blocks:
               if plan_submitted:
                  output = (
                     '计划正在等待 Lead 审批，'
                     '本工具暂未执行'
                  )

               else:
                  handler = teammate_handlers.get(block.name)

                  if handler is None:
                     output = f'未知工具：{block.name}'

                  else:
                     try:
                        output = handler(**block.input)

                     except Exception as error:
                        output = f'工具执行失败：{error}'

               tool_results.append({
                  'type': 'tool_result',
                  'tool_use_id': block.id,
                  'content': str(output),
               })

               if block.name == 'submit_plan':
                  plan_submitted = True

            sub_messages.append({
               'role': 'user',
               'content': tool_results,
            })
            continue

         summary = extract_text(response.content)

         if summary.strip() == '':
            summary = '队友已完成本轮任务'

         BUS.send(name, 'lead', summary, 'result')

         print(
            f'[Teammate] {name} '
            f'进入自治空闲状态'
         )

         idle_result = idle_poll(
            name,
            sub_messages,
            claim_handler=teammate_claim_task,
         )

         if idle_result == 'shutdown':
            shutdown_requested = True
            break

         if idle_result == 'timeout':
            break

         if idle_result == 'work':
            continue

      active_teammates.pop(name, None)
      print(f'[Teammate] {name} 已安全退出')
   active_teammates[name] = True

   thread = threading.Thread(target=run,daemon=True,)

   thread.start()

   print(
      f'[Teammate] 已启动 {name}，'
      f'角色：{role}'
   )

   return (f'队友 {name} 已经启动')


def run_spawn_teammate(name,role,prompt,):
   return spawn_teammate_thread(name,role,prompt,)

TOOL_HANDLERS.update({
   'spawn_teammate': run_spawn_teammate,
   'send_message': run_send_message,
   'check_inbox': run_check_inbox,
})

TOOLS.extend([
   {
      'name': 'spawn_teammate',
      'description': (
         '启动一个能够独立工作的队友 Agent'
      ),
      'input_schema': {
         'type': 'object',
         'properties': {
            'name': {
               'type': 'string',
               'description': '队友的唯一名字',
            },
            'role': {
               'type': 'string',
               'description': '队友承担的角色',
            },
            'prompt': {
               'type': 'string',
               'description': '交给队友的具体任务',
            },
         },
         'required': [
            'name',
            'role',
            'prompt',
         ],
      },
   },
   {
      'name': 'send_message',
      'description': (
         '以 Lead 身份向指定队友发送消息'
      ),
      'input_schema': {
         'type': 'object',
         'properties': {
            'to': {
               'type': 'string',
               'description': '接收消息的队友名字',
            },
            'content': {
               'type': 'string',
               'description': '要发送的消息内容',
            },
         },
         'required': [
            'to',
            'content',
         ],
      },
   },
   {
      'name': 'check_inbox',
      'description': (
         '读取并清空 Lead 的消息收件箱'
      ),
      'input_schema': {
         'type': 'object',
         'properties': {},
      },
   },
])

#------------------------------------------------------------

@dataclass
class ProtocolState:
   request_id: str
   type: str
   sender: str
   target: str
   status: str
   payload: str

   created_at: float = field(
      default_factory=time.time
   )


pending_requests = {}


def new_request_id():
   return (
      f'req_'
      f'{random.randint(0, 999999):06d}'
   )
def match_response(response_type,request_id,approve,):
   state = pending_requests.get(request_id)

   if state is None:
      print(
         f'[Protocol] 未知请求：'
         f'{request_id}'
      )
      return

   if (state.type == 'shutdown' and response_type!='shutdown_response'):
      print('[Protocol] 回复类型不匹配')
      return

   if (state.type == 'plan_approval'and response_type!='plan_approval_response'):
      print('[Protocol] 回复类型不匹配')
      return

   if state.status != 'pending':
      print(
         f'[Protocol] 请求 {request_id} '
         f'已经处理过'
      )
      return

   if approve:
      state.status = 'approved'
   else:
      state.status = 'rejected'

   print(
      f'[Protocol] {request_id} '
      f'状态更新为 {state.status}'
   )

def consume_lead_inbox(route_protocol=True):
   messages = BUS.read_inbox('lead')

   if len(messages) == 0:
      return []

   if route_protocol:
      for message in messages:
         metadata = message.get('metadata',{},)

         request_id = metadata.get('request_id','',)

         message_type = message.get('type','',)

         if (request_id != ''and message_type.endswith('_response')):
            approve = metadata.get('approve',False,)
            match_response(message_type,request_id,approve,)

   return messages

def run_request_shutdown(teammate):
   if teammate not in active_teammates:
      return (
         f'队友 {teammate} '
         f'当前没有运行'
      )

   request_id = new_request_id()

   state = ProtocolState(
      request_id=request_id,
      type='shutdown',
      sender='lead',
      target=teammate,
      status='pending',
      payload='请完成收尾并安全退出',
   )

   pending_requests[request_id] = state

   BUS.send(
      'lead',
      teammate,
      'Lead 请求你完成收尾并安全退出',
      'shutdown_request',
      {
         'request_id': request_id,
      },
   )

   print(
      f'[Protocol] 已向 {teammate} '
      f'发送关机请求 {request_id}'
   )

   return (
      f'已向 {teammate} 发送关机请求，'
      f'请求编号：{request_id}'
   )


def run_request_plan(teammate, task):
   if teammate not in active_teammates:
      return f'队友 {teammate} 当前没有运行'

   BUS.send(
      'lead',
      teammate,
      f'请先提交执行计划：{task}',
      'message',
   )

   return f'已要求 {teammate} 为任务提交计划'


def run_review_plan(
   request_id,
   approve,
   feedback='',
):
   state = pending_requests.get(request_id)

   if state is None:
      return f'没有找到审批请求：{request_id}'

   if state.type != 'plan_approval':
      return f'请求 {request_id} 不是计划审批请求'

   if state.status != 'pending':
      return (
         f'请求 {request_id} 已经处理：'
         f'{state.status}'
      )

   state.status = (
      'approved'
      if approve
      else 'rejected'
   )

   message = feedback or (
      '计划已批准，可以继续执行'
      if approve
      else '计划被拒绝，请修改后重新提交'
   )

   BUS.send(
      'lead',
      state.sender,
      message,
      'plan_approval_response',
      {
         'request_id': request_id,
         'approve': approve,
      },
   )

   return (
      f'计划已批准：{request_id}'
      if approve
      else f'计划已拒绝：{request_id}'
   )


TOOL_HANDLERS.update({
   'request_shutdown': run_request_shutdown,
   'request_plan': run_request_plan,
   'review_plan': run_review_plan,
})

TOOLS.extend([
   {
      'name': 'request_shutdown',
      'description': (
         '请求指定队友完成收尾并安全退出'
      ),
      'input_schema': {
         'type': 'object',
         'properties': {
            'teammate': {
               'type': 'string',
               'description': '需要关闭的队友名字',
            },
         },
         'required': ['teammate'],
      },
   },
   {
      'name': 'request_plan',
      'description': '要求指定队友先提交执行计划',
      'input_schema': {
         'type': 'object',
         'properties': {
            'teammate': {
               'type': 'string',
            },
            'task': {
               'type': 'string',
            },
         },
         'required': [
            'teammate',
            'task',
         ],
      },
   },
   {
      'name': 'review_plan',
      'description': '批准或拒绝队友提交的计划',
      'input_schema': {
         'type': 'object',
         'properties': {
            'request_id': {
               'type': 'string',
            },
            'approve': {
               'type': 'boolean',
            },
            'feedback': {
               'type': 'string',
            },
         },
         'required': [
            'request_id',
            'approve',
         ],
      },
   },
])



#-----------------------多agent------------------------------
#-----------------------自动认领任务--------------------------
IDLE_POLL_INTERVAL = 5#每隔 5 秒检查一次。连续空闲 60 秒后自动退出。
IDLE_TIMEOUT = 60

def idle_poll(name,sub_messages,claim_handler=None,):
   poll_count = (
      IDLE_TIMEOUT
      // IDLE_POLL_INTERVAL
   )

   for poll_number in range(poll_count):
      time.sleep(IDLE_POLL_INTERVAL)

      inbox_messages = (BUS.read_inbox(name))

      if len(inbox_messages) > 0:
         normal_messages = []

         for message in inbox_messages:
            message_type = message.get('type','message',)

            if (message_type== 'shutdown_request'):
               metadata = message.get('metadata',{},)

               request_id = metadata.get('request_id','',)

               BUS.send(
                  name,
                  'lead',
                  f'{name} 已安全退出',
                  'shutdown_response',
                  {
                     'request_id': request_id,
                     'approve': True,
                  },
               )

               return 'shutdown'

            normal_messages.append(message)

         if len(normal_messages) > 0:
            inbox_text = json.dumps(
               normal_messages,
               ensure_ascii=False,
            )

            sub_messages.append({
               'role': 'user',
               'content': (
                  f'<inbox>{inbox_text}</inbox>'
               ),
            })

            return 'work'

      unclaimed_tasks = (scan_unclaimed_tasks())

      if len(unclaimed_tasks) > 0:
         task = unclaimed_tasks[0]

         if claim_handler is None:
            result = claim_task(task.id,owner=name,)
         else:
            result = claim_handler(task.id)

         if result.startswith('已认领任务'):
            sub_messages.append({
               'role': 'user',
               'content': (
                  '<auto-claimed>\n'
                  f'任务 ID：{task.id}\n'
                  f'任务标题：{task.subject}\n'
                  f'任务说明：{task.description}\n'
                  '</auto-claimed>'
               ),
            })

            print(
               f'[Autonomous] {name} '
               f'自动认领任务：'
               f'{task.subject}'
            )

            return 'work'

   print(
      f'[Autonomous] {name} '
      f'空闲超过 {IDLE_TIMEOUT} 秒'
   )

   return 'timeout'


#-----------------------自动认领任务--------------------------

#-----------------------MCP----------------------------

MCP_TOOL_ANNOTATIONS = {}

class MCPClient:
   def __init__(self,name,command,):
      self.name = name
      self.command = command
      self.process = None
      self.tools = []
      self.request_id = 0

   def start(self):
      self.process = subprocess.Popen(
         self.command,
         stdin=subprocess.PIPE,
         stdout=subprocess.PIPE,
         stderr=None,
         cwd=str(WORKDIR),
         text=True,
         encoding='utf-8',
         bufsize=1,
      )

      initialize_response = (
         self.send_request(
            'initialize',
            {
               'protocolVersion': (
                  '2025-06-18'
               ),
               'capabilities': {},
               'clientInfo': {
                  'name': (
                     'my-claude-code'
                  ),
                  'version': '1.0.0',
               },
            },
         )
      )

      if 'error' in initialize_response:
         raise RuntimeError(
            initialize_response[
               'error'
            ].get(
               'message',
               'MCP 初始化失败',
            )
         )

      tools_response = (self.send_request('tools/list',{},))

      self.tools = (tools_response.get('result', {}).get('tools', []))

      self.send_notification(
         'notifications/initialized',
         {},
      )

   def send_request(self,method,params,):
      if self.process is None:
         raise RuntimeError(
            'MCP Server 尚未启动'
         )

      if (
         self.process.stdin is None
         or self.process.stdout is None
      ):
         raise RuntimeError(
            'MCP 通信管道没有创建'
         )

      self.request_id += 1

      request_message = {
         'jsonrpc': '2.0',
         'id': self.request_id,
         'method': method,
         'params': params,
      }

      request_text = json.dumps(
         request_message,
         ensure_ascii=False,
      )

      self.process.stdin.write(
         request_text + '\n'
      )

      self.process.stdin.flush()

      response_text = (self.process.stdout.readline())

      if response_text == '':
         return_code = (self.process.poll())

         raise RuntimeError(
            'MCP Server 已停止，'
            f'退出码：{return_code}'
         )

      response = json.loads(
         response_text
      )

      return response

   def send_notification(self,method,params,):
      if (
         self.process is None
         or self.process.stdin is None
      ):
         return

      notification = {
         'jsonrpc': '2.0',
         'method': method,
         'params': params,
      }

      text = json.dumps(
         notification,
         ensure_ascii=False,
      )

      self.process.stdin.write(
         text + '\n'
      )

      self.process.stdin.flush()

   def call_tool(self,tool_name,arguments,):
      try:
         response = self.send_request(
            'tools/call',
            {
               'name': tool_name,
               'arguments': arguments,
            },
         )

         if 'error' in response:
            return (
               'MCP 调用错误：'
               + response['error'].get(
                  'message',
                  '未知错误',
               )
            )

         result = response.get('result',{},)

         content = result.get('content',[],)

         text_parts = []

         for block in content:
            if block.get('type') == 'text':
               text_parts.append(block.get('text', ''))

         if len(text_parts) == 0:
            return 'MCP 工具没有返回文本'

         return '\n'.join(text_parts)

      except Exception as error:
         return (
            f'MCP 工具执行失败：'
            f'{error}'
         )

   def close(self):
      if self.process is None:
         return

      if self.process.poll() is None:
         self.process.terminate()

      self.process = None


mcp_clients = {}

MCP_SERVERS = {
   'baidu': [
      sys.executable,
      str(
         WORKDIR
         / 'mcp_servers'
         / 'web_search_server.py'
      ),
   ],
}

def connect_mcp(name):
   if name in mcp_clients:
      return (
         f'MCP Server {name} '
         f'已经连接'
      )

   command = MCP_SERVERS.get(name)

   if command is None:
      available = ', '.join(MCP_SERVERS.keys())

      return (
         f'未知 MCP Server：{name}。'
         f'可用服务：{available}'
      )

   try:
      mcp_client = MCPClient(name,command,)

      mcp_client.start()

      mcp_clients[name] = mcp_client

      tool_names = []

      for tool in mcp_client.tools:
         tool_names.append(tool.get('name','unknown',))

      return (
         f'已连接 MCP Server：'
         f'{name}；发现工具：'
         + ', '.join(tool_names)
      )

   except Exception as error:
      return (
         f'MCP 连接失败：{error}'
      )

TOOLS.append({
   'name': 'connect_mcp',
   'description': (
      '连接指定的 MCP Server，'
      '并发现该 Server 提供的工具'
   ),
   'input_schema': {
      'type': 'object',
      'properties': {
         'name': {
            'type': 'string',
            'description': (
               '要连接的 MCP '
               'Server 名称'
            ),
         },
      },
      'required': [
         'name',
      ],
   },
})

TOOL_HANDLERS[
   'connect_mcp'
] = connect_mcp


def normalize_mcp_name(name):
   return re.sub(
      r'[^a-zA-Z0-9_-]',
      '_',
      name,
   )

def make_mcp_handler(mcp_client,tool_name,):
   def handler(**arguments):
      return mcp_client.call_tool(tool_name,arguments,)
   return handler

def assemble_tool_pool():
   tools = list(TOOLS)
   handlers = dict(TOOL_HANDLERS)

   for (server_name,mcp_client) in mcp_clients.items():
      safe_server = (normalize_mcp_name(server_name))

      for tool in mcp_client.tools:
         original_name = tool.get('name')

         if not original_name:
            continue

         safe_tool = (normalize_mcp_name(original_name))

         full_name = (
            f'mcp__{safe_server}'
            f'__{safe_tool}'
         )

         MCP_TOOL_ANNOTATIONS[full_name] = tool.get(
            'annotations',
            {},
         )

         tools.append({
            'name': full_name,
            'description': tool.get(
               'description',
               'MCP 外部工具',
            ),
            'input_schema': tool.get(
               'inputSchema',
               {
                  'type': 'object',
                  'properties': {},
               },
            ),
         })
         handlers[full_name] = (make_mcp_handler(mcp_client,original_name,))

   return tools, handlers
#-----------------------MCP----------------------------


def agent_loop(messages):
   
   rounds_since_todo = 0
   recovery_state = RecoveryState()

   max_tokens = DEFAULT_MAX_TOKENS
   while True:
      #--------mcp-------------
      current_tools, current_handlers = (assemble_tool_pool())
      #--------mcp-------------
      #--------多agent---------

      team_messages = consume_lead_inbox()
      if len(team_messages) > 0:
         inbox_lines = []

         for team_message in team_messages:
            sender = team_message.get('from','unknown',)

            content = team_message.get('content','',)

            inbox_lines.append(f'来自 {sender}：{content}')

         inbox_text = '\n'.join(inbox_lines)

         messages.append({
            'role': 'user',
            'content': (
               '[队友收件箱]\n'
               + inbox_text
            ),
         })

         print(
            f'[Team] Lead 收到 '
            f'{len(team_messages)} 条消息'
         )
      #--------多agent---------
      #--------后台------------
      background_notifications = (collect_background_results())
      if len(background_notifications) > 0:
         notification_text = (
            "\n\n".join(
               background_notifications
            )
         )

         messages.append({
            "role": "user",
            "content": notification_text,
         })
         print(
            f"[Background] 收到 "
            f"{len(background_notifications)} "
            "条完成通知"
         )
      #--------后台------------
      context = update_context()
      context['enabled_tools'] = list(current_handlers.keys())
      system_prompt = (assemble_system_prompt(context))
      messages[:] = tool_result_budget(messages)#l3
      # L1 已注释：snip_compact 会在 L4 摘要前删掉中间消息，
      # 导致 compact_history 基于残缺历史生成摘要。
      # messages[:] = snip_compact(messages)#l1

      messages[:] = micro_compact(messages)#l2
      if estimate_size(messages) > CONTEXT_LIMIT:
         print("[Auto Compact] 上下文超过阈值")
         messages[:] = compact_history(messages)
      if rounds_since_todo >= 3 and messages:
         messages.append({
            "role":"user",
            "content":(
               "<reminder>"
               "请更新 Todo 列表和任务状态。"
               "</reminder>"
            )
         })
         rounds_since_todo = 0
      try:
         response = call_with_retry(
            lambda: client.messages.create(
               model=recovery_state.current_model,
               system=system_prompt,
               tools=current_tools,
               messages=messages,
               max_tokens=max_tokens,
            ),
            recovery_state,
         )
      except Exception as error:
         if is_prompt_too_long(error):
            if not recovery_state.has_attempted_compact:
               print(
                  "[Recovery] 上下文过长，"
                  "执行应急压缩后重试"
               )

               messages[:] = reactive_compact(messages)
               recovery_state.has_attempted_compact = True
               continue

            print(
               "[Recovery] 应急压缩后"
               "上下文仍然过长，停止本轮"
            )
            return

         print(
            f"[Recovery] 无法恢复的错误："
            f"{error}"
         )
         return

      if response.stop_reason == "max_tokens":
         if not recovery_state.has_escalated:
            print(
               "[Recovery] 输出达到限制，"
               "提高 max_tokens 后重试"
            )

            max_tokens = (
               ESCALATED_MAX_TOKENS
            )

            recovery_state.has_escalated = True

            continue

         for block in response.content:
            if block.type == "text":
               print(block.text,end="")
         
         messages.append({
            "role": "assistant",
            "content": response.content,
         })

         truncated_results = []

         for block in response.content:
            if block.type == 'tool_use':
               truncated_results.append({
                  'type': 'tool_result',
                  'tool_use_id': block.id,
                  'content': (
                     '模型输出被截断，'
                     '本次工具调用未执行'
                  ),
               })

         if (
            recovery_state.recovery_count
            < MAX_RECOVERY_RETRIES
         ):
            if len(truncated_results) > 0:
               truncated_results.append({
                  'type': 'text',
                  'text': CONTINUATION_PROMPT,
               })

               continuation_content = truncated_results

            else:
               continuation_content = CONTINUATION_PROMPT

            messages.append({
               'role': 'user',
               'content': continuation_content,
            })

            recovery_state.recovery_count += 1

            print(
               "[Recovery] 输出再次被截断，"
               f"开始第 "
               f"{recovery_state.recovery_count} "
               "次续写"
            )

            continue

         print(
            "[Recovery] 已达到最大续写次数"
         )

         if len(truncated_results) > 0:
            messages.append({
               'role': 'user',
               'content': truncated_results,
            })

         return

      max_tokens = DEFAULT_MAX_TOKENS
      recovery_state.has_escalated = False

      messages.append({
         "role":"assistant",
         "content":response.content
      })

      for block in response.content:
          if block.type == "text":
            print(block.text)

      tool_blocks = [
         block
         for block in response.content
         if block.type == "tool_use"
      ]

      if len(tool_blocks) == 0:
         force_continue = trigger_hooks("Stop", messages)

         if force_continue is not None:
            messages.append({
               "role": "user",
               "content": force_continue,
            })
            continue

         return
      rounds_since_todo+=1
      result = []
      compact_requested = False

      for block in tool_blocks:
         tool_name = block.name
         tool_input = block.input
         print("模型想调用工具：", tool_name)
         print("工具参数：", tool_input)
         blocked = trigger_hooks(
            "PreToolUse",
            block
         )
         if blocked is not None:
            output = blocked
         else:
            handler = current_handlers.get(tool_name)

            if compact_requested:
               output = (
                  '上下文压缩已经安排，'
                  '本工具暂未执行'
               )

            elif tool_name == 'compact':
               compact_requested = True
               output = '即将压缩较早的对话历史'

            elif handler is None:
               output = f"错误：未知工具 {tool_name}"

            elif should_run_background(tool_name,tool_input):
               background_id = (start_background_task(block))

               output = (
                  f"[后台任务 {background_id} "
                  "已启动]"
                  "任务完成后会发送通知。"
               )

               print(
                  f"[Background] 已启动 "
                  f"{background_id}"
               )
            else:
               try:
                  output = handler(**tool_input)

               except Exception as error:
                  output = (
                     f'工具执行失败：'
                     f'{type(error).__name__}: '
                     f'{error}'
                  )

               trigger_hooks(
                  "PostToolUse",
                  block,
                  output
               )
               if block.name == "todo_write":
                  rounds_since_todo = 0
         result.append({
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": output,
         })

      # tool_result 必须紧跟对应的 tool_use，
      # 并且排在这条 user 消息的最前面。
      user_content = list(result)

      ready_notifications = (collect_background_results())

      for notification in (ready_notifications):
         user_content.append({
            "type": "text",
            "text": notification,
         })

      messages.append({
         "role": "user",
         "content": user_content,
      })

      if compact_requested:
         messages[:] = compact_history(messages)
         messages.append({
            'role': 'user',
            'content': (
               '[上下文已压缩，请根据摘要继续任务]'
            ),
         })

register_hook(
    "PreToolUse",
    log_hook
)
register_hook(
    "PreToolUse",
    permission_hook
)
register_hook(
    "PostToolUse",
    post_tool_log_hook
)
register_hook(
    "UserPromptSubmit",
    user_prompt_hook
)
register_hook(
    "Stop",
    stop_summary_hook
)

messages = []

load_durable_jobs()

threading.Thread(target=cron_scheduler_loop,daemon=True,).start()

threading.Thread(
   target=queue_processor_loop,
   daemon=True,
).start()

print('[Cron] 定时任务调度器已启动')

while True:
   query = input("my-claude-code >")

   if query.strip().lower() in ("q", "exit", ""):
      break

   with agent_lock:
      trigger_hooks("UserPromptSubmit", query)

      messages.append({
         "role": "user",
         "content": query
      })

      memory_context = load_memories(messages)

      if memory_context != "":
         messages[-1]["content"] = (
            query
            + "\n\n"
            + memory_context
         )
         print("[Memory] 已加载相关记忆")

      agent_loop(messages)

      new_memory_count = extract_memories(messages)

      if new_memory_count > 0:
         consolidate_memories()
