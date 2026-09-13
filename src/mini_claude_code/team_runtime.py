import json
import random
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .context_manager import fit_context, prepare_tool_results
from .model_gateway import call_model
from .security import set_execution_context
from .task_manager import (
   claim_task,
   complete_task,
   load_task,
   run_list_tasks,
   scan_unclaimed_tasks,
)
from .tool_executor import ToolCall, execute_tool
from .tools import run_glob, run_powershell, run_read, run_write
from .worktree_manager import WORKTREES_DIR

WORKDIR = Path.cwd().resolve()
MAILBOX_DIR = WORKDIR / '.mailboxes'
MAILBOX_DIR.mkdir(exist_ok=True)
IDLE_POLL_INTERVAL = 5
IDLE_TIMEOUT = 60

_client = None
_model = None
_tools = []
_extract_text = None


def configure_team_runtime(client, model, tools, extract_text):
   global _client
   global _model
   global _tools
   global _extract_text

   _client = client
   _model = model
   _tools = tools
   _extract_text = extract_text


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


def spawn_teammate_thread(name,role,prompt,):
   if name in active_teammates:
      return (f'队友 {name} 已经存在')

   system_prompt = (
      f'你是队友 Agent：{name}。'
      f'你的角色是：{role}。'
      '请使用工具完成分配给你的任务。'
      '完成后，将结果发送给 lead。'
      '注意：你运行在后台线程，无法弹出审批对话框。'
      'write_file 和 powershell 只有在 Lead 已经为完全相同的操作'
      '保存过长期许可时才会执行，否则会被权限系统拒绝。'
      '请优先用 read_file、glob 调查，并把需要写入或执行命令的部分'
      '通过 send_message 交给 Lead。'
   )

   wt_context = {
      'path': None,
   }

   def bind_execution_context():
      set_execution_context(
         interactive=False,
         base_dir=wt_context['path'],
         label=f'teammate:{name}',
      )

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
      for tool in _tools
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
               bind_execution_context()
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

   def teammate_read(path,limit=200,offset=0):
      return run_read(
         path,
         limit,
         base_dir=wt_context['path'],
         offset=offset,
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
      bind_execution_context()

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
            sub_messages[:] = fit_context(
               sub_messages, system_prompt, teammate_tools, 8000, base_dir=wt_context['path']
            )
            response = call_model(_client, "teammate",
               model=_model,
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
                  output = execute_tool(
                     block,
                     teammate_handlers,
                  )

               tool_results.append({
                  'type': 'tool_result',
                  'tool_use_id': block.id,
                  'content': str(output),
               })

               if block.name == 'submit_plan':
                  plan_submitted = True

            sub_messages.append({
               'role': 'user',
               'content': prepare_tool_results(tool_results, base_dir=wt_context['path']),
            })
            continue

         summary = _extract_text(response.content)

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


def idle_poll(name,sub_messages,claim_handler=None,):
   poll_count = (
      IDLE_TIMEOUT
      // IDLE_POLL_INTERVAL
   )

   for _poll_number in range(poll_count):
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
            # 自动认领会切换队友的工作区，也必须经过同一个授权入口。
            result = execute_tool(
               ToolCall(
                  'claim_task',
                  {'task_id': task.id},
                  f'auto-claim-{task.id}',
               ),
               {'claim_task': claim_handler},
            )

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


BUS = MessageBus(MAILBOX_DIR)
active_teammates = {}
pending_requests = {}
