import os
import random
import time

PRIMARY_MODEL = None
FALLBACK_MODEL = os.getenv('FALLBACK_MODEL_ID')
DEFAULT_MAX_TOKENS = 3000
ESCALATED_MAX_TOKENS = 8000
MAX_RECOVERY_RETRIES = 3
MAX_API_RETRIES = 10
BASE_RETRY_DELAY = 0.5
MAX_CONSECUTIVE_529 = 3
CONTINUATION_PROMPT = (
    '上一次回答因为输出长度限制而中断。'
    '请直接从中断处继续，不要道歉，'
    '也不要重复前面的内容。'
)


def configure_recovery(primary_model):
   global PRIMARY_MODEL
   PRIMARY_MODEL = primary_model


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
