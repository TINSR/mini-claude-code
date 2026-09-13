import json
import re
import time
from pathlib import Path

from .model_gateway import call_model
from .skill_loader import parse_frontmatter

WORKDIR = Path.cwd().resolve()
MEMORY_DIR = WORKDIR / '.memory'
MEMORY_INDEX = MEMORY_DIR / 'MEMORY.md'
MEMORY_DIR.mkdir(parents=True, exist_ok=True)

MEMORY_TYPES = [
    'user',
    'feedback',
    'project',
    'reference',
]
CONSOLIDATE_THRESHOLD = 10
CONSOLIDATE_INTERVAL = 24 * 60 * 60
CONSOLIDATE_MARKER = MEMORY_DIR / '.last_consolidated'

_client = None
_model = None
_extract_text = None


def configure_memory_runtime(client, model, extract_text):
   global _client
   global _model
   global _extract_text

   _client = client
   _model = model
   _extract_text = extract_text


def write_memory_file(name,memory_type,description,body):
   slug = (name.lower().replace(" ","-").replace("/","-"))

   file_path = MEMORY_DIR/f"{slug}.md"

   content = (
      "---\n"
      f"name: {name}\n"
      f"description: {description}\n"
      f"type: {memory_type}\n"
      "---\n\n"
      f"{body}\n"
   )

   file_path.write_text(
      content,
      encoding="utf-8"
   )
   rebuild_memory_index()
   return file_path


def rebuild_memory_index():
   lines = []

   for file_path in sorted(MEMORY_DIR.glob("*.md")):
      if file_path.name == "MEMORY.md":
         continue
      raw = file_path.read_text(encoding="utf8")

      meta,body=parse_frontmatter(raw)

      name = meta.get("name",file_path.stem)
      description = meta.get("description",body[:80])
      line = (
         f"- [{name}]({file_path.name})"
         f" — {description}"
      )
      lines.append(line)

   index_content = "\n".join(lines)

   MEMORY_INDEX.write_text(index_content,encoding="utf-8")


def list_memory_files():
   memories = []

   for file_path in sorted(MEMORY_DIR.glob("*.md")):
      if file_path.name == "MEMORY.md":
         continue

      raw = file_path.read_text(encoding="utf-8")

      meta, body = parse_frontmatter(raw)

      memories.append({
         "filename": file_path.name,
         "name": meta.get("name",file_path.stem),
         "description": meta.get("description",""),
         "type": meta.get("type","user"),
         "body": body,
      })

   return memories


def read_memory_file(filename):
   file_path = MEMORY_DIR / filename

   if not file_path.exists():
        return None

   return file_path.read_text(encoding="utf-8")


def select_relevant_memories(messages,max_items=5):
   memories = list_memory_files()

   if len(memories) == 0:
      return []

   recent_messages = messages[-3:]

   recent_text = str(recent_messages)

   catalog_lines = []

   for index, memory in enumerate(memories):
      catalog_lines.append(
         f"{index}: "
         f"{memory['name']} — "
         f"{memory['description']}"
      )

   catalog = "\n".join(catalog_lines)

   prompt = (
      "请根据最近对话，从记忆目录中选择相关记忆。\n"
      "只返回 JSON 数字列表，例如：[0, 2]。\n"
      "如果没有相关记忆，返回 []。\n\n"
      f"最近对话：\n{recent_text}\n\n"
      f"记忆目录：\n{catalog}"
   )

   try:
      response = call_model(_client, "memory_select",
         model=_model,
         messages=[
               {
                  "role": "user",
                  "content": prompt
               }
         ],
         max_tokens=500,
      )

      text = _extract_text(response.content).strip()

      match = re.search(r"\[.*?\]",text)

      if match is None:
         return []

      indices = json.loads(match.group())

      selected = []

      for index in indices:
         if (isinstance(index, int) and 0 <= index < len(memories)):
            selected.append(memories[index]["filename"])

         if len(selected) >= max_items:
                break

      return selected

   except Exception:
      return []


def load_memories(messages):
   filenames = select_relevant_memories(messages)

   if len(filenames) == 0:
        return ""

   parts = [
      "<relevant_memories>"
    ]

   for filename in filenames:
      content = read_memory_file(filename)

      if content is not None:
         parts.append(content)

   parts.append(
      "</relevant_memories>"
    )

   return "\n\n".join(parts)


def extract_memories(messages):
   recent_messages = messages[-10:]

   dialogue = str(recent_messages)

   existing_memories = (list_memory_files())

   existing_lines = []

   for memory in existing_memories:
        existing_lines.append(
         f"- {memory['name']}: "
         f"{memory['description']}"
      )

   if len(existing_lines) == 0:
        existing_text = "暂无记忆"
   else:
        existing_text = "\n".join(
            existing_lines
        )

   prompt = (
        "请从对话中提取值得长期保存的信息。\n"
        "包括用户偏好、用户反馈、项目事实和重要参考信息。\n"
        "不要保存普通问题和临时信息。\n"
        "不要重复已有记忆。\n\n"
        "返回 JSON 数组，格式如下：\n"
        "[\n"
        "  {\n"
        '    "name": "记忆名称",\n'
        '    "type": "user",\n'
        '    "description": "一句话简介",\n'
        '    "body": "完整记忆内容"\n'
        "  }\n"
        "]\n"
        "如果没有新记忆，返回 []。\n\n"
        f"已有记忆：\n{existing_text}\n\n"
        f"最近对话：\n{dialogue[:4000]}"
   )

   try:
      response = call_model(_client, "memory_extract",
         model=_model,
         messages=[
            {
               "role": "user",
               "content": prompt
            }
         ],
         max_tokens=800,
      )

      text = _extract_text(
            response.content
      )

      match = re.search(
         r"\[.*\]",
         text,
         re.DOTALL
      )

      if match is None:
            return 0

      json_text = match.group()

      try:
         new_memories = json.loads(json_text)
      except json.JSONDecodeError:
         json_text = json_text.replace("\\'", "'")
         new_memories = json.loads(json_text)

   except Exception as error:
      print(f"[Memory 提取失败] {error}")
        
      return 0

   saved_count = 0
   for memory in new_memories:
      name = memory.get("name")

      memory_type = memory.get("type","user")

      description = memory.get("description","")

      body = memory.get("body","")

      if not name or not body:
            continue

      if memory_type not in MEMORY_TYPES:
         memory_type = "user"

      write_memory_file(
         name=name,
         memory_type=memory_type,
         description=description,
         body=body,
      )

      saved_count += 1

   if saved_count > 0:
      print(
         f"[Memory] 新增了 "
         f"{saved_count} 条记忆"
      )

   return saved_count


def consolidate_memories():
   memories = list_memory_files()

   if len(memories) < CONSOLIDATE_THRESHOLD:
        return

   if CONSOLIDATE_MARKER.exists():
      last_time = CONSOLIDATE_MARKER.stat().st_mtime
      elapsed = time.time() - last_time

      if elapsed < CONSOLIDATE_INTERVAL:
         return

   catalog_parts = []

   for memory in memories:
        catalog_parts.append(
            f"## {memory['filename']}\n"
            f"name: {memory['name']}\n"
            f"type: {memory['type']}\n"
            f"description: "
            f"{memory['description']}\n"
            f"{memory['body']}"
        )

   catalog = "\n\n".join(
        catalog_parts
   )

   prompt = (
        "请整理下面这些长期记忆。\n"
        "要求：\n"
        "1. 合并内容重复的记忆\n"
        "2. 删除过时或互相冲突的记忆\n"
        "3. 最多保留30条记忆\n"
        "4. 优先保留重要的用户偏好\n"
        "5. 返回JSON数组\n\n"
        "每一项格式为：\n"
        "{name, type, description, body}\n\n"
        f"现有记忆：\n{catalog[:16000]}"
   )

   try:
      response = call_model(_client, "memory_consolidate",
         model=_model,
         messages=[
               {
                  "role": "user",
                  "content": prompt
               }
         ],
         max_tokens=3000,
      )

      text = _extract_text(response.content).strip()

      match = re.search(
            r"\[.*\]",
            text,
            re.DOTALL
      )

      if match is None:
            return

      json_text = match.group()

      try:
         consolidated = json.loads(json_text)
      except json.JSONDecodeError:
         json_text = json_text.replace("\\'", "'")
         consolidated = json.loads(json_text)

      if not isinstance(consolidated, list) or len(consolidated) == 0:
         return

   except Exception as error:
      print(
         f"[Memory 整理失败] {error}"
      )
      return

   for file_path in MEMORY_DIR.glob("*.md"):
      if file_path.name != "MEMORY.md":
         file_path.unlink()

   saved_count = 0

   for memory in consolidated:
      name = memory.get("name")
      memory_type = memory.get(
         "type",
         "user"
      )
      description = memory.get(
            "description",
            ""
      )
      body = memory.get(
            "body",
            ""
      )

      if not name or not body:
            continue

      if memory_type not in MEMORY_TYPES:
            memory_type = "user"

      write_memory_file(
            name=name,
            memory_type=memory_type,
            description=description,
            body=body,
      )

      saved_count += 1

   rebuild_memory_index()
   CONSOLIDATE_MARKER.touch()

   print(
        f"[Memory] 已将 "
        f"{len(memories)} 条记忆"
        f"整理为 {saved_count} 条"
   )
