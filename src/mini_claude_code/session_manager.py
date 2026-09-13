import json
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .atomic_io import atomic_write_text

WORKDIR = Path.cwd().resolve()
SESSIONS_DIR = WORKDIR / '.sessions'
CURRENT_SESSION_FILE = SESSIONS_DIR / 'current'
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

# 主线程、Cron 交付线程和 finally 收尾都可能保存会话。
save_lock = threading.Lock()


@dataclass
class Session:
   id: str
   title: str
   messages: list = field(default_factory=list)
   created_at: float = field(default_factory=time.time)
   updated_at: float = field(default_factory=time.time)


def new_session_id():
   timestamp = time.strftime('%Y%m%d-%H%M%S')
   suffix = uuid.uuid4().hex[:6]
   return f'session-{timestamp}-{suffix}'


def validate_session_id(session_id):
   return bool(re.fullmatch(r'session-[A-Za-z0-9-]+', session_id))


def session_path(session_id):
   if not validate_session_id(session_id):
      raise ValueError('会话 ID 不合法')

   return SESSIONS_DIR / f'{session_id}.json'


def serialize_value(value):
   if hasattr(value, 'model_dump'):
      return serialize_value(value.model_dump())

   if isinstance(value, dict):
      return {
         str(key): serialize_value(item)
         for key, item in value.items()
      }

   if isinstance(value, (list, tuple)):
      return [serialize_value(item) for item in value]

   if value is None or isinstance(value, (str, int, float, bool)):
      return value

   return str(value)


def save_session(session, mark_current=True):
   """Persist one session; an interrupted write keeps the previous file."""
   path = session_path(session.id)

   with save_lock:
      session.updated_at = time.time()
      data = asdict(session)
      data['messages'] = serialize_value(session.messages)
      atomic_write_text(
         path,
         json.dumps(data, ensure_ascii=False, indent=2),
      )

      if mark_current:
         atomic_write_text(CURRENT_SESSION_FILE, session.id)

   return session


def load_session(session_id):
   data = json.loads(session_path(session_id).read_text(encoding='utf-8'))
   return Session(**data)


def create_session(title='新会话'):
   session = Session(id=new_session_id(), title=title or '新会话')
   return save_session(session)


def load_current_session():
   if CURRENT_SESSION_FILE.exists():
      session_id = CURRENT_SESSION_FILE.read_text(encoding='utf-8').strip()

      try:
         return load_session(session_id)
      except (FileNotFoundError, ValueError, json.JSONDecodeError, TypeError):
         pass

   sessions = list_sessions()
   if len(sessions) > 0:
      return load_session(sessions[0].id)

   return create_session()


def list_sessions():
   sessions = []

   for path in SESSIONS_DIR.glob('session-*.json'):
      try:
         sessions.append(load_session(path.stem))
      except (ValueError, json.JSONDecodeError, TypeError):
         continue

   return sorted(sessions, key=lambda item: item.updated_at, reverse=True)


def rename_session(session, title):
   if title.strip() == '':
      raise ValueError('会话标题不能为空')

   session.title = title.strip()
   return save_session(session)


def delete_session(session_id):
   path = session_path(session_id)
   if not path.exists():
      return False

   path.unlink()

   if CURRENT_SESSION_FILE.exists():
      current_id = CURRENT_SESSION_FILE.read_text(encoding='utf-8').strip()
      if current_id == session_id:
         CURRENT_SESSION_FILE.unlink()

   return True


def content_text(content):
   if isinstance(content, str):
      memory_marker = '\n\n<relevant_memories>'
      if memory_marker in content:
         return content.split(memory_marker, 1)[0]
      return content

   parts = []
   if not isinstance(content, list):
      return str(content)

   for block in content:
      if not isinstance(block, dict):
         block = serialize_value(block)

      block_kind = block.get('type')
      if block_kind == 'text':
         parts.append(block.get('text', ''))
      elif block_kind == 'tool_use':
         parts.append(f"[调用工具 {block.get('name', 'unknown')}]")
      elif block_kind == 'tool_result':
         result = str(block.get('content', ''))
         parts.append(f'[工具结果] {result[:500]}')

   return '\n'.join(part for part in parts if part != '')


def render_history(session):
   lines = [f'\n===== {session.title} ({session.id}) =====']

   if len(session.messages) == 0:
      lines.append('(暂无对话记录)')
   else:
      for message in session.messages:
         role = message.get('role', 'unknown')
         label = '你' if role == 'user' else 'Agent'
         text = content_text(message.get('content', ''))
         lines.append(f'\n{label}> {text}')

   lines.append('\n========================================')
   return '\n'.join(lines)


def render_session_list(current_session):
   sessions = list_sessions()
   lines = ['\n会话列表：']

   for session in sessions:
      marker = '>' if session.id == current_session.id else ' '
      message_count = len(session.messages)
      lines.append(
         f'{marker} {session.id}  {session.title}  '
         f'({message_count} 条消息)'
      )

   return '\n'.join(lines)


def handle_session_command(query, current_session):
   text = query.strip()

   if text == '/help':
      return current_session, (
         '会话命令：\n'
         '/new [标题]       新建会话\n'
         '/session          查看当前会话\n'
         '/sessions         查看会话列表\n'
         '/switch <会话ID>  切换并显示历史\n'
         '/rename <标题>    重命名当前会话\n'
         '/history          显示当前会话历史\n'
         '/delete <会话ID>  删除非当前会话'
      )

   if text == '/sessions':
      return current_session, render_session_list(current_session)

   if text == '/session':
      return current_session, (
         f'当前会话：{current_session.title}\n'
         f'会话 ID：{current_session.id}\n'
         f'消息数量：{len(current_session.messages)}'
      )

   if text == '/history':
      return current_session, render_history(current_session)

   if text == '/new' or text.startswith('/new '):
      save_session(current_session)
      title = text[4:].strip() or '新会话'
      session = create_session(title)
      return session, render_history(session)

   if text.startswith('/switch '):
      session_id = text[len('/switch '):].strip()
      save_session(current_session)
      session = load_session(session_id)
      save_session(session)
      return session, render_history(session)

   if text.startswith('/rename '):
      title = text[len('/rename '):].strip()
      rename_session(current_session, title)
      return current_session, f'当前会话已重命名为：{current_session.title}'

   if text.startswith('/delete '):
      session_id = text[len('/delete '):].strip()
      if session_id == current_session.id:
         return current_session, '不能删除当前会话，请先切换到其他会话'

      if delete_session(session_id):
         return current_session, f'已删除会话：{session_id}'

      return current_session, f'找不到会话：{session_id}'

   if text.startswith('/'):
      return current_session, (
         f'未知命令：{text.split()[0]}\n'
         '输入 /help 查看可用命令'
      )

   return None
