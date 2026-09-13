import json
import random
import time
from dataclasses import asdict, dataclass
from pathlib import Path

WORKDIR = Path.cwd().resolve()
TASKS_DIR = WORKDIR / '.tasks'
TASKS_DIR.mkdir(parents=True, exist_ok=True)


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
