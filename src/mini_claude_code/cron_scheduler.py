import json
import random
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from .atomic_io import atomic_write_text
from .security import set_execution_context

WORKDIR = Path.cwd().resolve()
SCHEDULED_TASKS_FILE = WORKDIR / '.scheduled_tasks.json'
MAX_DELIVERY_ATTEMPTS = 3
scheduled_jobs = {}
cron_queue = []
cron_lock = threading.Lock()
last_fired = {}
delivery_failures = {}
agent_lock = threading.Lock()
_agent_runner = None
_session_resolver = None

# 当前这一轮属于哪个会话。Cron 交付可能运行在非当前会话上，此时
# 轮次内新建的定时任务必须归属正在运行的会话，而不是界面上的当前会话。
_turn = threading.local()


def configure_cron_runtime(agent_runner, session_resolver):
   """Wire Cron delivery to the session it must run against.

   ``agent_runner(session)`` runs one turn and saves that exact session.
   ``session_resolver(session_id)`` returns the owning Session; an empty id
   means "whatever session is current right now".
   """
   global _agent_runner
   global _session_resolver

   _agent_runner = agent_runner
   _session_resolver = session_resolver


@contextmanager
def turn_session(session):
   """Bind the calling thread's turn to ``session`` for ownership decisions."""
   previous = getattr(_turn, 'session', None)
   _turn.session = session

   try:
      yield
   finally:
      _turn.session = previous


def owning_session_id():
   """The session a newly created job belongs to."""
   bound = getattr(_turn, 'session', None)

   if bound is not None:
      return getattr(bound, 'id', '')

   if _session_resolver is None:
      return ''

   try:
      session = _session_resolver('')
   except Exception as error:
      # 找不到归属会话不该让创建定时任务的工具调用直接失败。
      print(f'[Cron] 无法确定当前会话：{error}')
      return ''

   return '' if session is None else getattr(session, 'id', '')


@dataclass
class CronJob:
   id: str
   cron: str
   prompt: str
   recurring: bool
   durable: bool
   created_at: float = field(default_factory=time.time)
   run_at: float | None = None
   # 创建这个任务的会话；到期后结果必须回到同一个会话。
   session_id: str = ''


def find_next_run(cron_expression, after):
   """Return the first matching minute at or after ``after``."""
   candidate = after.replace(second=0, microsecond=0)
   search_limit = candidate + timedelta(days=366)

   while candidate <= search_limit:
      if cron_matches(cron_expression, candidate):
         return candidate

      candidate += timedelta(minutes=1)

   return None


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

   for cron_field, bound, name in zip(
      fields,
      bounds,
      names,
      strict=True,
   ):
      minimum, maximum = bound

      error = validate_cron_field(
         cron_field,
         minimum,
         maximum,
      )

      if error is not None:
         return (
            f"{name}字段错误："
            f"{error}"
         )

   return None


def durable_job_snapshot():
   """Serializable copies of the durable jobs; caller must hold ``cron_lock``."""
   return [
      asdict(job)
      for job in scheduled_jobs.values()
      if job.durable
   ]


def write_durable_jobs(snapshot):
   json_text = json.dumps(
      snapshot,
      ensure_ascii=False,
      indent=2,
   )

   atomic_write_text(SCHEDULED_TASKS_FILE, json_text)


def save_durable_jobs(snapshot=None):
   """Persist the durable jobs.

   Snapshot and write both happen under ``cron_lock`` so a job expiring in the
   scheduler thread cannot break iteration or overwrite a just-created job.
   Callers that already hold the lock pass their own snapshot.
   """
   if snapshot is not None:
      write_durable_jobs(snapshot)
      return

   with cron_lock:
      write_durable_jobs(durable_job_snapshot())


def load_durable_jobs():
   if not SCHEDULED_TASKS_FILE.exists():
      return

   try:
      json_text = (SCHEDULED_TASKS_FILE.read_text(encoding="utf-8"))

      job_data_list = json.loads(json_text)

      storage_created_at = SCHEDULED_TASKS_FILE.stat().st_mtime

      for job_data in job_data_list:
         job_data.setdefault('created_at', storage_created_at)
         job_data.setdefault('run_at', None)
         job_data.setdefault('session_id', '')
         job = CronJob(**job_data)
         error = validate_cron(job.cron)

         if error is not None:
            print(
               f"[Cron] 跳过无效任务 "
               f"{job.id}：{error}"
            )
            continue

         if not job.recurring and job.run_at is None:
            next_run = find_next_run(
               job.cron,
               datetime.fromtimestamp(job.created_at),
            )

            if next_run is not None:
               job.run_at = next_run.timestamp()

         scheduled_jobs[job.id] = job

      save_durable_jobs()

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
      session_id=owning_session_id(),
   )

   if not recurring:
      next_run = find_next_run(
         cron,
         datetime.fromtimestamp(job.created_at),
      )

      if next_run is None:
         return '错误：无法计算一次性任务的执行时间'

      job.run_at = next_run.timestamp()

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


def cron_scheduler_loop():
   while True:
      time.sleep(1)

      current_time = datetime.now()

      minute_marker = (current_time.strftime("%Y-%m-%d %H:%M"))

      with cron_lock:
         jobs = list(scheduled_jobs.values())

         for job in jobs:
            try:
               if job.recurring:
                  if not cron_matches(job.cron,current_time):
                     continue
               elif job.run_at is not None:
                  if current_time.timestamp() < job.run_at:
                     continue
               elif not cron_matches(job.cron,current_time):
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
                  # 已经持有 cron_lock，必须自己取快照。
                  save_durable_jobs(durable_job_snapshot())

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


def deliver_fired_jobs(fired_jobs):
   """Append each job to its owning session, then run one turn per session.

   The caller must hold ``agent_lock``, which is also required to switch
   sessions, so the session bound here cannot change mid-turn.

   An unconfigured runtime raises here on purpose: ``process_cron_batch`` puts
   the batch back on the queue, whereas returning early would drop jobs that
   have already been taken off it.
   """
   ordered_sessions = []
   sessions_by_id = {}

   for job in fired_jobs:
      session = _session_resolver(job.session_id)

      if session is None:
         print(
            f'[Cron] 找不到任务 {job.id} '
            f'所属的会话 {job.session_id}，已跳过'
         )
         continue

      if session.id not in sessions_by_id:
         sessions_by_id[session.id] = session
         ordered_sessions.append(session)

      sessions_by_id[session.id].messages.append({
         "role": "user",
         "content": (
            f"[定时任务 {job.id}]\n"
            f"{job.prompt}"
         ),
      })

      print(
         f"\n[Cron] 自动交付任务 "
         f"{job.id} 到会话 {session.id}"
      )

   for session in ordered_sessions:
      _agent_runner(session)

   return ordered_sessions


def requeue_failed_jobs(jobs, reason):
   """Put a failed batch back, giving up on a job after repeated failures.

   Delivery is at-least-once, not exactly-once: a batch that fails after some
   of its jobs already reached a session is retried whole, so those jobs can
   be delivered twice. Losing a job silently is the worse of the two.
   """
   retried = []

   for job in jobs:
      attempts = delivery_failures.get(job.id, 0) + 1

      if attempts >= MAX_DELIVERY_ATTEMPTS:
         delivery_failures.pop(job.id, None)
         print(
            f'[Cron] 任务 {job.id} 连续 {attempts} 次交付失败，'
            f'已放弃：{reason}'
         )
         continue

      delivery_failures[job.id] = attempts
      retried.append(job)

   if len(retried) == 0:
      return []

   with cron_lock:
      cron_queue[:0] = retried

   print(
      f'[Cron] {len(retried)} 个任务已退回队列等待重试：{reason}'
   )

   return retried


def process_cron_batch():
   """Deliver one batch of due jobs if the Agent Loop is free.

   Returns the sessions that ran. Every failure mode must leave the scheduler
   thread alive and must not silently drop jobs already taken off the queue.
   """
   if not has_cron_queue():
      return []

   if not agent_lock.acquire(blocking=False):
      return []

   try:
      fired_jobs = (consume_cron_queue())

      if len(fired_jobs) == 0:
         return []

      # 交付跑在后台线程，不能占用 stdin 等待审批。
      set_execution_context(interactive=False, label='cron')

      try:
         sessions = deliver_fired_jobs(fired_jobs)

      except Exception as error:
         requeue_failed_jobs(
            fired_jobs,
            f'{type(error).__name__}: {error}',
         )
         return []

      for job in fired_jobs:
         delivery_failures.pop(job.id, None)

      return sessions

   finally:
      agent_lock.release()


def queue_processor_loop():
   while True:
      time.sleep(0.2)

      try:
         process_cron_batch()

      except Exception as error:
         # 这个线程一旦结束，之后所有定时任务都会静默丢失。
         print(f'[Cron] 队列处理异常：{error}')
