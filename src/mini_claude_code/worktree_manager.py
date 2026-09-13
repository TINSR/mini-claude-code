import json
import re
import subprocess
import time
from pathlib import Path

from .task_manager import load_task, save_task

WORKDIR = Path.cwd().resolve()
WORKTREES_DIR = WORKDIR / '.worktrees'
WORKTREES_DIR.mkdir(exist_ok=True)


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
         # Git 可能提示凭据或打开编辑器；不给它终端 stdin。
         stdin=subprocess.DEVNULL,
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
         stdin=subprocess.DEVNULL,
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
         stdin=subprocess.DEVNULL,
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
         stdin=subprocess.DEVNULL,
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
