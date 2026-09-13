import argparse
import json
import os
import threading
from pathlib import Path

from anthropic import (
   Anthropic,
)
from dotenv import load_dotenv

from .background_tasks import (
   collect_background_results,
   configure_background_runtime,
   should_run_background,
   start_background_task,
)
from .context_manager import (
   compact_history,
   configure_context_runtime,
   fit_context,
   is_prompt_too_long,
   persist_large_output,
   prepare_tool_results,
   reactive_compact,
   run_compact,
)
from .cron_scheduler import (
   agent_lock,
   configure_cron_runtime,
   cron_scheduler_loop,
   load_durable_jobs,
   queue_processor_loop,
   run_cancel_cron,
   run_list_crons,
   run_schedule_cron,
   turn_session,
)
from .hooks import register_hook, trigger_hooks
from .mcp_client import (
   assemble_tool_pool,
   close_mcp_clients,
   configure_mcp_runtime,
   connect_mcp,
)
from .memory_manager import (
   configure_memory_runtime,
   consolidate_memories,
   extract_memories,
   load_memories,
)
from .model_gateway import call_model
from .prompt_manager import (
   configure_prompt_runtime,
   get_system_prompt,
   update_context,
)
from .recovery import (
   CONTINUATION_PROMPT,
   DEFAULT_MAX_TOKENS,
   ESCALATED_MAX_TOKENS,
   MAX_RECOVERY_RETRIES,
   RecoveryState,
   call_with_retry,
   configure_recovery,
)
from .security import (
   MCP_TOOL_ANNOTATIONS,
   load_approved_operations,
   permission_hook,
   run_clear_permissions,
   run_list_permissions,
   run_revoke_permission,
)
from .session_manager import (
   handle_session_command,
   load_current_session,
   load_session,
   save_session,
)
from .skill_loader import list_skills, load_skill
from .task_manager import (
   run_claim_task,
   run_complete_task,
   run_create_task,
   run_get_task,
   run_list_tasks,
)
from .team_runtime import (
   configure_team_runtime,
   consume_lead_inbox,
   run_check_inbox,
   run_request_plan,
   run_request_shutdown,
   run_review_plan,
   run_send_message,
   run_spawn_teammate,
)
from .tool_executor import authorize_tool, execute_tool, invoke_handler
from .tools import run_edit, run_glob, run_powershell, run_read, run_write
from .worktree_manager import (
   run_create_worktree,
   run_keep_worktree,
   run_remove_worktree,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(
   dotenv_path=Path.cwd() / '.env',
   override=False,
)

client = None

MODEL = os.getenv("MODEL_ID")
configure_context_runtime(client, MODEL)
configure_recovery(MODEL)
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
      "description": "Read UTF-8 lines, default 200; offset is zero-based.",
      "input_schema":{
         "type": "object",
         "properties": {
            "path": {
               "type": "string"
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 2000},
            "offset": {"type": "integer", "minimum": 0}
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
      'name': 'list_permissions',
      'description': '列出当前项目已保存的长期工具许可。',
      'input_schema': {
         'type': 'object',
         'properties': {},
      },
   },
   {
      'name': 'revoke_permission',
      'description': '撤销指定 ID 的一条长期工具许可。',
      'input_schema': {
         'type': 'object',
         'properties': {
            'fingerprint': {
               'type': 'string',
               'description': 'list_permissions 返回的许可 ID。',
            },
         },
         'required': ['fingerprint'],
      },
   },
   {
      'name': 'clear_permissions',
      'description': '清空当前项目全部长期工具许可。',
      'input_schema': {
         'type': 'object',
         'properties': {},
      },
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




def spawn_subagent(description):
   print("\n[Subagent spawned]")
   print("子任务：", description)

   sub_messages = [
        {
            "role": "user",
            "content": description
        }
    ]
   for _round_number in range(30):
      try:
         sub_messages[:] = fit_context(sub_messages, SUB_SYSTEM, SUB_TOOLS, 3000)
      except ValueError as error:
         return f'子 Agent 已停止：{error}'
      response = call_model(client, "subagent",
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

         output = execute_tool(block, SUB_HANDLERS)

         result.append({
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": str(output),
         })
      sub_messages.append({
         "role":"user",
         "content":prepare_tool_results(result)
      })
   return "子 Agent 达到最大运行轮数，未能完成任务"


def extract_text(content):
   text_parts = []

   if isinstance(content,str):
      return content

   for block in content:
      if isinstance(block, dict):
         if block.get('type') == 'text':
            text_parts.append(str(block.get('text', '')))
      elif getattr(block,"type",None)=="text":
         text_parts.append(block.text)

   if len(text_parts)==0:
      return "子 Agent 没有返回文本结论"

   return "\n".join(text_parts)


configure_memory_runtime(client, MODEL, extract_text)
configure_team_runtime(client, MODEL, TOOLS, extract_text)


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


def log_hook(block):
    print(f"[HOOK] 即将调用工具：{block.name}")

    return None

def user_prompt_hook(query):
    print(f"[HOOK] 收到用户输入，当前工作目录：{WORKDIR}")
    return None

def post_tool_log_hook(block, output):
    output_length = len(str(output))
    print(f"[HOOK] 工具执行完成：{block.name}，输出长度：{output_length}")
    return None

def stop_summary_hook(messages, turn_tool_requests):
    print(
        '[HOOK] Agent 即将停止，'
        f'本轮请求工具：{turn_tool_requests} 次'
    )
    return None

TOOL_HANDLERS = {
   "powershell":run_powershell,
   "read_file":run_read,
   "write_file": run_write,
   "edit_file":run_edit,
   "glob":run_glob,
   'list_permissions': run_list_permissions,
   'revoke_permission': run_revoke_permission,
   'clear_permissions': run_clear_permissions,
   "todo_write": run_todo_write,
   "task": spawn_subagent
}

configure_prompt_runtime(TOOL_HANDLERS)
configure_mcp_runtime(TOOLS, TOOL_HANDLERS, MCP_TOOL_ANNOTATIONS)
configure_background_runtime(TOOL_HANDLERS)

SUB_HANDLERS = {
    name: TOOL_HANDLERS[name]
    for name in SUB_TOOL_NAMES
}


#-----------------------skill-------------------------------

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

TOOL_HANDLERS["load_skill"] = load_skill


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


#-----------------------上下文管理---------------------------

#-----------------------记忆管理-----------------------------

#-----------------------记忆管理-----------------------------
#-----------------------错误处理-----------------------------



#-----------------------错误处理-----------------------------

#-----------------------长任务-------------------------------















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






#-----------------------后台任务-----------------------------
#-----------------------定时任务-----------------------------
#以下三个函数都是校验表达式的，无关紧要











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



#-----------------------定时任务-----------------------------
#-----------------------多agent------------------------------



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


#-----------------------自动认领任务--------------------------

#-----------------------MCP----------------------------




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




#-----------------------MCP----------------------------


def agent_loop(messages):
   turn_tool_requests = 0
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
            "content": persist_large_output("background", notification_text),
         })
         print(
            f"[Background] 收到 "
            f"{len(background_notifications)} "
            "条完成通知"
         )
      #--------后台------------
      context = update_context()
      context['enabled_tools'] = list(current_handlers.keys())
      system_prompt = (get_system_prompt(context))
      try:
         messages[:] = fit_context(messages, system_prompt, current_tools, max_tokens)
      except ValueError as error:
         print(f'[Context] {error}')
         return
      if rounds_since_todo >= 3 and messages and any(
         todo.get('status') != 'completed' for todo in CURRENT_TODOS
      ):
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
            lambda system_prompt=system_prompt,
            current_tools=current_tools,
            max_tokens=max_tokens: call_model(client, "main",
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

               compacted = reactive_compact(messages)
               recovery_state.has_attempted_compact = True
               if compacted is messages:
                  print('[Recovery] 压缩失败，保留历史并停止本轮')
                  return
               messages[:] = compacted
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
         turn_tool_requests += sum(
            1
            for block in response.content
            if block.type == 'tool_use'
         )

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

      turn_tool_requests += len(tool_blocks)

      if len(tool_blocks) == 0:
         force_continue = trigger_hooks(
            'Stop',
            messages,
            turn_tool_requests,
         )

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

         if compact_requested:
            output = (
               '上下文压缩已经安排，'
               '本工具暂未执行'
            )

         else:
            # 后台派发和压缩也必须先过同一个授权入口。
            denial = authorize_tool(block)

            if denial is not None:
               output = denial

            elif tool_name == 'compact':
               compact_requested = True
               output = '即将压缩较早的对话历史'

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
               output = invoke_handler(block, current_handlers)

               if tool_name == "todo_write":
                  rounds_since_todo = 0
         result.append({
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": output,
         })

      # tool_result 必须紧跟对应的 tool_use，
      # 并且排在这条 user 消息的最前面。
      user_content = prepare_tool_results(result)

      ready_notifications = (collect_background_results())

      for notification in (ready_notifications):
         user_content.append({
            "type": "text",
            "text": persist_large_output("background", notification),
         })

      messages.append({
         "role": "user",
         "content": user_content,
      })

      if compact_requested:
         compacted = compact_history(messages)
         if compacted is messages:
            print('[Compact] 未完成压缩，保留历史并停止本轮')
            return
         messages[:] = compacted
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

# 当前会话只在这里换绑，并且只在持有 agent_lock 时换绑。
SESSION_STATE = {'session': None}


def run_session_agent(session):
   """Run one turn against ``session`` and save that exact session.

   A task keeps its own session object, so switching sessions while the turn
   is running cannot redirect the result to the newly selected session.
   """
   try:
      # 轮次内新建的 Cron 任务归属这个会话，而不是界面上的当前会话。
      with turn_session(session):
         agent_loop(session.messages)
   finally:
      current = SESSION_STATE['session']

      save_session(
         session,
         mark_current=(
            current is not None
            and session.id == current.id
         ),
      )


def resolve_session(session_id):
   """Return the session a Cron job belongs to, or ``None`` if it is gone."""
   current = SESSION_STATE['session']

   if session_id == '':
      return current

   if current is not None and session_id == current.id:
      return current

   try:
      return load_session(session_id)
   except (FileNotFoundError, ValueError, json.JSONDecodeError, TypeError):
      return None


def main():
   global client
   parser = argparse.ArgumentParser(description='Mini Claude Code: terminal coding agent')
   parser.add_argument('--version', action='version', version='mini-claude-code 0.1.0')
   parser.parse_args()
   if not os.getenv('ANTHROPIC_API_KEY') or not MODEL:
      parser.error('请在当前工作目录 .env 或环境变量中设置 ANTHROPIC_API_KEY 和 MODEL_ID')
   client = Anthropic(
      api_key=os.getenv('ANTHROPIC_API_KEY'), base_url=os.getenv('ANTHROPIC_BASE_URL'),
   )
   configure_context_runtime(client, MODEL)
   configure_memory_runtime(client, MODEL, extract_text)
   configure_team_runtime(client, MODEL, TOOLS, extract_text)
   load_approved_operations()

   SESSION_STATE['session'] = load_current_session()

   configure_cron_runtime(run_session_agent, resolve_session)

   load_durable_jobs()

   threading.Thread(target=cron_scheduler_loop,daemon=True,).start()

   threading.Thread(
      target=queue_processor_loop,
      daemon=True,
   ).start()

   print('[Cron] 定时任务调度器已启动')
   print(
      f'[Session] 当前会话：'
      f'{SESSION_STATE["session"].title} '
      f'({SESSION_STATE["session"].id})'
   )
   print('输入 /help 查看会话命令')

   while True:
      # 读取输入不持锁，Cron 交付才能在用户思考时进行。
      query = input("mycc >")

      if query.strip().lower() in ("q", "exit", ""):
         with agent_lock:
            save_session(SESSION_STATE['session'])
            close_mcp_clients()
         break

      # 切换会话必须和交付、保存互斥，否则运行中的任务会存到新会话。
      with agent_lock:
         try:
            command_result = handle_session_command(
               query,
               SESSION_STATE['session'],
            )
         except (FileNotFoundError, ValueError, json.JSONDecodeError) as error:
            print(f'[Session] {error}')
            continue

         if command_result is not None:
            session, output = command_result
            SESSION_STATE['session'] = session
            print(output)
            continue

         session = SESSION_STATE['session']
         messages = session.messages

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

         run_session_agent(session)

         new_memory_count = extract_memories(messages)

         if new_memory_count > 0:
            consolidate_memories()


if __name__ == "__main__":
   main()
