import json
import re
import subprocess
import sys
import threading
from pathlib import Path

WORKDIR = Path.cwd().resolve()
mcp_clients = {}
MCP_SERVERS = {
   'baidu': [
      sys.executable,
      str(WORKDIR / 'mcp_servers' / 'web_search_server.py'),
   ],
}

# 一个卡住的 Server 不能无限期挂住调用它的 Agent 线程。
DEFAULT_REQUEST_TIMEOUT = 30.0
HANDSHAKE_TIMEOUT = 15.0

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


class MCPClient:
   """One stdio MCP connection, safe to share between agent threads.

   A single reader thread owns ``stdout`` and hands each response to the
   waiter that registered the matching request id. Notifications and replies
   for ids nobody is waiting on are dropped instead of being mistaken for the
   answer to the next request.
   """

   def __init__(self,name,command,timeout=DEFAULT_REQUEST_TIMEOUT,):
      self.name = name
      self.command = command
      self.timeout = timeout
      self.process = None
      self.tools = []
      self.request_id = 0
      self._state_lock = threading.Lock()
      self._write_lock = threading.Lock()
      self._pending = {}
      self._reader = None
      self._stopped_reason = None

   def start(self):
      with self._state_lock:
         self._stopped_reason = None
         self._pending.clear()

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

      self._reader = threading.Thread(
         target=self._read_loop,
         args=(self.process,),
         name=f'mcp-reader-{self.name}',
         daemon=True,
      )
      self._reader.start()

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
            timeout=HANDSHAKE_TIMEOUT,
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

      # 规范要求先确认初始化完成，之后才能发普通请求。
      self.send_notification(
         'notifications/initialized',
         {},
      )

      tools_response = (
         self.send_request(
            'tools/list',
            {},
            timeout=HANDSHAKE_TIMEOUT,
         )
      )

      if 'error' in tools_response:
         raise RuntimeError(
            tools_response['error'].get(
               'message',
               'MCP 工具发现失败',
            )
         )

      result = tools_response.get('result') or {}
      self.tools = result.get('tools', [])

   def _read_loop(self, process):
      reason = 'MCP Server 已停止'

      try:
         while True:
            line = process.stdout.readline()

            if line == '':
               break

            if line.strip() == '':
               continue

            try:
               message = json.loads(line)
            except json.JSONDecodeError:
               print(f'[MCP] {self.name} 返回了无法解析的数据，已忽略')
               continue

            if not isinstance(message, dict):
               continue

            message_id = message.get('id')

            if message_id is None:
               # 服务器通知没有对应请求，不能当成任何人的响应。
               continue

            self._resolve(message_id, message)

      except (OSError, ValueError) as error:
         reason = f'MCP 读取失败：{error}'

      finally:
         self._fail_pending(
            f'{reason}，退出码：{process.poll()}'
         )

   def _resolve(self, message_id, message):
      with self._state_lock:
         entry = self._pending.get(message_id)

         if entry is None:
            print(
               f'[MCP] {self.name} 返回了没有对应请求的 '
               f'id={message_id}，已忽略'
            )
            return

         entry['response'] = message
         entry['event'].set()

   def _fail_pending(self, reason):
      with self._state_lock:
         # 保留第一个原因：close() 会让读取线程随后也报一次 EOF，
         # 后到的那条是前一条的后果，不是根因。
         if self._stopped_reason is None:
            self._stopped_reason = reason

         reason = self._stopped_reason

         for entry in self._pending.values():
            if entry['response'] is None:
               entry['error'] = reason

            entry['event'].set()

   def _write_line(self, text):
      with self._write_lock:
         process = self.process

         if process is None or process.stdin is None:
            raise OSError('MCP 通信管道不可用')

         process.stdin.write(text + '\n')
         process.stdin.flush()

   def send_request(self,method,params,timeout=None,):
      # 管道可用性只在 _write_line 的写锁内检查一次：在这里先查一遍并不能
      # 阻止并发的 close()，只会让人以为查过就安全了。
      wait_timeout = (
         self.timeout if timeout is None else timeout
      )
      event = threading.Event()

      with self._state_lock:
         if self._stopped_reason is not None:
            raise RuntimeError(self._stopped_reason)

         self.request_id += 1
         message_id = self.request_id
         self._pending[message_id] = {
            'event': event,
            'response': None,
            'error': None,
         }

      request_message = {
         'jsonrpc': '2.0',
         'id': message_id,
         'method': method,
         'params': params,
      }

      request_text = json.dumps(
         request_message,
         ensure_ascii=False,
      )

      try:
         self._write_line(request_text)

      except (OSError, ValueError) as error:
         self._discard(message_id)

         raise RuntimeError(
            f'MCP 请求写入失败：{error}'
         ) from error

      if not event.wait(wait_timeout):
         self._discard(message_id)

         raise TimeoutError(
            f'MCP 请求超时：{method}，'
            f'等待 {wait_timeout} 秒没有响应'
         )

      entry = self._discard(message_id)

      if entry is None or entry['response'] is None:
         raise RuntimeError(
            (entry or {}).get('error')
            or self._stopped_reason
            or 'MCP 请求没有得到响应'
         )

      return entry['response']

   def _discard(self, message_id):
      with self._state_lock:
         return self._pending.pop(message_id, None)

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

      try:
         self._write_line(text)
      except (OSError, ValueError) as error:
         print(f'[MCP] {self.name} 通知发送失败：{error}')

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
      process = self.process

      if process is None:
         return

      self._fail_pending('MCP Server 已关闭')

      if process.poll() is None:
         process.terminate()

         try:
            process.wait(timeout=5)
         except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

      for stream in (process.stdin, process.stdout):
         if stream is None:
            continue

         try:
            stream.close()
         except OSError:
            pass

      reader = self._reader

      if (
         reader is not None
         and reader is not threading.current_thread()
      ):
         reader.join(timeout=2)

      self._reader = None
      self.process = None


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

   mcp_client = MCPClient(name,command,)

   try:
      mcp_client.start()

   except Exception as error:
      # 握手失败不能留下孤儿进程和读取线程。
      mcp_client.close()

      return (
         f'MCP 连接失败：{error}'
      )

   mcp_clients[name] = mcp_client

   tool_names = []

   for tool in mcp_client.tools:
      tool_names.append(tool.get('name','unknown',))

   return (
      f'已连接 MCP Server：'
      f'{name}；发现工具：'
      + ', '.join(tool_names)
   )


def close_mcp_clients():
   """Shut every connected Server down; used when the CLI exits."""
   for name in list(mcp_clients):
      mcp_client = mcp_clients.pop(name)

      try:
         mcp_client.close()
      except Exception as error:
         print(f'[MCP] 关闭 {name} 失败：{error}')


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
   tools = list(_tools)
   handlers = dict(_tool_handlers)

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

         _tool_annotations[full_name] = tool.get(
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
