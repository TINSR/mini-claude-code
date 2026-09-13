import hashlib
import json
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .atomic_io import atomic_write_text
from .tools import safe_path

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

MCP_TOOL_ANNOTATIONS = {}

SENSITIVE_FILE_NAMES = {
    '.env',
    'id_rsa',
    'id_ed25519',
    'credentials.json',
}

# 指纹 -> 供用户查看的脱敏元信息。
APPROVED_OPERATIONS = {}

APPROVALS_DIR_NAME = '.mini_claude_code'
APPROVALS_FILE_NAME = 'approvals.json'

# 每个线程独立的执行上下文。主线程可以交互审批；队友、后台任务和
# Cron 调度线程不能占用 stdin，因此它们必须依赖已保存的长期许可。
_execution = threading.local()

MAIN_EXECUTION_CONTEXT = {
   'interactive': True,
   'base_dir': None,
   'label': 'main',
}

NON_INTERACTIVE_DENIAL = (
   '权限系统拒绝执行 {tool}：该操作需要人工审批，'
   '但当前执行路径（{label}）不能占用终端输入。'
   '请让主 Agent 在交互模式下批准同样的操作（选择 a 保存长期许可），'
   '或改用只读工具。'
)


def current_execution_context():
   return getattr(_execution, 'value', MAIN_EXECUTION_CONTEXT)


def set_execution_context(interactive=True, base_dir=None, label='main'):
   """Declare how the calling thread is allowed to resolve approvals."""
   _execution.value = {
      'interactive': interactive,
      'base_dir': base_dir,
      'label': label,
   }


@contextmanager
def execution_context(interactive=True, base_dir=None, label='main'):
   previous = getattr(_execution, 'value', None)
   set_execution_context(interactive, base_dir, label)

   try:
      yield
   finally:
      if previous is None:
         _execution.__dict__.pop('value', None)
      else:
         _execution.value = previous


def execution_base_dir():
   return current_execution_context().get('base_dir')


def operation_key(tool_name, tool_input):
   base_dir = execution_base_dir()
   workspace = (
      Path.cwd().resolve()
      if base_dir is None
      else Path(base_dir).resolve()
   )

   operation = {
      # 队友 Worktree 与主工作区的同名路径必须是不同的许可。
      'workspace': str(workspace),
      'tool': tool_name,
      'arguments': tool_input,
   }

   return json.dumps(
      operation,
      sort_keys=True,
      ensure_ascii=False,
   )


def operation_fingerprint(tool_name, tool_input):
   key = operation_key(tool_name, tool_input)

   return hashlib.sha256(
      key.encode('utf-8')
   ).hexdigest()


def describe_operation(tool_name, tool_input):
   path = tool_input.get('path')

   if tool_name == 'read_file' and isinstance(path, str):
      return f'读取文件：{path}'

   if tool_name == 'write_file' and isinstance(path, str):
      return f'写入文件：{path}'

   if tool_name == 'edit_file' and isinstance(path, str):
      return f'修改文件：{path}'

   if tool_name == 'powershell':
      return '已批准的 PowerShell 操作'

   return f'已批准工具：{tool_name}'


def approval_store_path():
   return (
      Path.cwd().resolve()
      / APPROVALS_DIR_NAME
      / APPROVALS_FILE_NAME
   )


def approval_store_dirs():
   """Every workspace whose approval store must stay out of tool reach."""
   dirs = [approval_store_path().parent]

   base_dir = execution_base_dir()
   if base_dir is not None:
      dirs.append(Path(base_dir).resolve() / APPROVALS_DIR_NAME)

   return dirs


def load_approved_operations():
   path = approval_store_path()

   if not path.exists():
      return

   try:
      data = json.loads(
         path.read_text(encoding='utf-8')
      )
   except (OSError, json.JSONDecodeError) as error:
      print(f'[权限] 无法读取长期许可：{error}')
      return

   records = data.get('approved_operations', {})

   if not isinstance(records, dict):
      print('[权限] 长期许可文件格式错误')
      return

   APPROVED_OPERATIONS.clear()

   for fingerprint, record in records.items():
      if isinstance(fingerprint, str) and isinstance(record, dict):
         APPROVED_OPERATIONS[fingerprint] = record


def save_approved_operations():
   path = approval_store_path()

   data = {
      'version': 1,
      'approved_operations': APPROVED_OPERATIONS,
   }

   atomic_write_text(
      path,
      json.dumps(
         data,
         ensure_ascii=False,
         indent=2,
      ),
   )


def approve_operation(tool_name, tool_input):
   fingerprint = operation_fingerprint(
      tool_name,
      tool_input,
   )

   APPROVED_OPERATIONS[fingerprint] = {
      'tool': tool_name,
      'summary': describe_operation(
         tool_name,
         tool_input,
      ),
      'created_at': datetime.now().isoformat(
         timespec='seconds'
      ),
   }

   save_approved_operations()


def is_operation_approved(tool_name, tool_input):
   fingerprint = operation_fingerprint(
      tool_name,
      tool_input,
   )

   return fingerprint in APPROVED_OPERATIONS


def is_approval_store_path(target):
   for approvals_dir in approval_store_dirs():
      if target == approvals_dir or approvals_dir in target.parents:
         return True

   return False


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

   if is_operation_approved(tool_name, tool_input):
      return 'allow'

   if tool_name in ('read_file', 'write_file', 'edit_file'):
      permission, reason = check_file_operation(
         tool_name,
         tool_input,
      )

      if permission != 'allow':
         print(f'[权限] {reason}')

      return permission

   reason=check_rules(tool_name,tool_input)

   if reason is not None:
      print("需要审批：",reason)
      return "ask"

   return "allow"


def check_file_operation(tool_name, tool_input):
   """根据文件工具和目标文件，给出 allow / ask / deny。"""
   path = tool_input.get('path')

   if not isinstance(path, str) or path.strip() == '':
      return 'deny', '缺少有效的文件路径'

   try:
      # 队友在自己的 Worktree 里工作，必须按同一个 base_dir 判断真实目标。
      target = safe_path(path, execution_base_dir())
   except (OSError, ValueError) as error:
      return 'deny', f'路径检查失败：{error}'

   if is_approval_store_path(target):
      return 'deny', '不允许工具直接访问长期许可文件'

   filename = target.name.lower()
   if (
      filename in SENSITIVE_FILE_NAMES
      or filename.startswith('.env.')
   ):
      return 'ask', f'操作涉及敏感文件：{target}'

   if tool_name == 'read_file':
      return 'allow', '读取工作目录内的普通文件'

   if tool_name == 'write_file':
      if target.exists():
         return 'ask', f'将完整覆盖已有文件：{target}'
      return 'ask', f'将创建新文件：{target}'

   return 'ask', f'将局部修改文件：{target}'


def permission_hook(block):
    permission = check_permission(block.name,block.input)
    if permission == "deny":
        return "权限系统拒绝执行该工具"

    if permission == "ask":
        context = current_execution_context()

        if not context.get('interactive', True):
            denial = NON_INTERACTIVE_DENIAL.format(
                tool=block.name,
                label=context.get('label', 'unknown'),
            )
            print(f'[权限] {denial}')
            return denial

        confirm = input(
            '允许执行吗？'
            '(y=本次允许 / a=以后允许相同操作 / n=拒绝) '
        ).lower()

        if confirm == 'a':
            approve_operation(
                block.name,
                block.input,
            )
            print('[权限] 已保存“以后允许相同操作”的许可')
            return None

        if confirm != "y":
            return "用户拒绝执行该工具"

    return None


def check_rules(tool_name, tool_input):
    if tool_name in ('revoke_permission', 'clear_permissions'):
        return '该工具会修改长期许可'

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


def run_list_permissions():
   if len(APPROVED_OPERATIONS) == 0:
      return '暂无长期许可'

   lines = [
      f'已保存 {len(APPROVED_OPERATIONS)} 条长期许可：'
   ]

   for fingerprint, record in APPROVED_OPERATIONS.items():
      summary = record.get('summary', '未知操作')
      created_at = record.get('created_at', '未知时间')

      lines.append(
         f'ID: {fingerprint}\n'
         f'操作: {summary}\n'
         f'创建时间: {created_at}'
      )

   return '\n\n'.join(lines)


def run_revoke_permission(fingerprint):
   if fingerprint not in APPROVED_OPERATIONS:
      return '错误：未找到该长期许可'

   record = APPROVED_OPERATIONS.pop(fingerprint)
   save_approved_operations()

   return (
      '已撤销长期许可：'
      + record.get('summary', '未知操作')
   )


def run_clear_permissions():
   count = len(APPROVED_OPERATIONS)
   APPROVED_OPERATIONS.clear()
   save_approved_operations()

   return f'已清空 {count} 条长期许可'
