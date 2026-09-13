import os
import subprocess


def decode_shell_output(data):
   if not data:
      return ""

   # PowerShell 使用 UTF-8；旧版 Windows 程序可能直接输出 GBK。
   # 先收集字节再解码，避免 subprocess 的读取线程因解码失败丢失输出。
   for encoding in ("utf-8-sig", "gb18030"):
      try:
         text = data.decode(encoding)
         return text.replace("\r\n", "\n").replace("\r", "\n")
      except UnicodeDecodeError:
         continue

   text = data.decode("utf-8", errors="replace")
   return (
      "[输出编码警告：存在无法解码的字节，已用替代字符保留其余输出]\n"
      + text.replace("\r\n", "\n").replace("\r", "\n")
   )


def run_powershell(command,run_in_background=False,cwd=None):
   utf8_command = (
      "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)\n"
      "$OutputEncoding = [Console]::OutputEncoding\n"
      + command
   )
   result = subprocess.run(
      ["powershell", "-NoProfile", "-Command", utf8_command],
      cwd=cwd if cwd is not None else os.getcwd(),
      capture_output=True,#表示不要让 PowerShell 直接把内容打印到屏幕，而是把结果交给Python保存。
      # 不继承终端 stdin：否则后台线程里的命令会抢走用户正在输入的按键，
      # 或者一直等输入直到超时。等待输入的命令应当立刻失败。
      stdin=subprocess.DEVNULL,
      text=False,
      timeout=60,
   )

   output = decode_shell_output(result.stdout) + decode_shell_output(result.stderr)

   if output.strip() == "":
      return "(没有输出)"
   else:
      return output.strip()


