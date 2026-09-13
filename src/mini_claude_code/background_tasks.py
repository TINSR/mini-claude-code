import threading

from .security import execution_context
from .tool_executor import invoke_handler

background_tasks = {}
background_results = {}
background_lock = threading.Lock()
background_counter = 0
_tool_handlers = {}


def configure_background_runtime(tool_handlers):
   global _tool_handlers
   _tool_handlers = tool_handlers


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


class BackgroundBlock:
   """The original tool call with the scheduling flag removed."""

   type = 'tool_use'

   def __init__(self, block):
      self.id = block.id
      self.name = block.name
      self.input = {
         key: value
         for key, value in block.input.items()
         if key != 'run_in_background'
      }


def execute_background_tool(block):
   # PreToolUse 已经在主线程对这次调用放行；这里只负责执行和 PostToolUse。
   return invoke_handler(BackgroundBlock(block), _tool_handlers)


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
      # 这个工具调用已经在主线程通过 PreToolUse 审批；工具内部若再触发
      # 别的工具，也不能从后台线程占用 stdin 等待审批。
      try:
         with execution_context(
            interactive=False,
            label=f'background:{background_id}',
         ):
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
