from pathlib import Path

WORKDIR = Path.cwd().resolve()


def safe_path(path, base_dir=None):
   if base_dir is None:
      base = WORKDIR
   else:
      base = Path(base_dir).resolve()

   target = (base / path).resolve()

   if target != base and base not in target.parents:
      raise ValueError("不允许访问当前工作目录之外的文件")

   return target


def run_read(path, limit=200, base_dir=None, offset=0):
   target = safe_path(path, base_dir)
   if not target.exists():
      return f"错误：文件不存在：{path}"
   if not target.is_file():
      return f"错误：这不是文件：{path}"
   limit = 200 if limit is None else limit
   if (type(offset) is not int or offset < 0
       or type(limit) is not int or not 1 <= limit <= 2000):
      return "错误：offset 必须是非负整数，limit 必须在 1 到 2000 之间"
   lines = target.read_text(encoding="utf-8").splitlines()
   if offset >= len(lines):
      return f"[文件共 {len(lines)} 行，offset={offset} 已到文件末尾]"
   selected = lines[offset:offset + limit]
   end = offset + len(selected)
   suffix = f"；继续读取请设置 offset={end}" if end < len(lines) else "；已到文件末尾"
   return f"[第 {offset + 1}-{end} 行 / 共 {len(lines)} 行{suffix}]\n" + "\n".join(selected)



def run_write(path,content,base_dir=None):
   target = safe_path(path,base_dir)

   target.parent.mkdir(
      parents=True,
      exist_ok=True
   )
   target.write_text(
    content,
    encoding="utf-8"
   )
   return f"成功写入文件：{path}"


def run_edit(path,old_text,new_text):
   target = safe_path(path)

   if not target.exists():
      return f"错误：文件不存在：{path}"
   if not target.is_file():
      return f"错误：这不是文件：{path}"

   text = target.read_text(encoding="utf-8")

   if old_text not in text:
      return "错误：没有找到需要替换的内容"

   new_content = text.replace(old_text,new_text,1)

   target.write_text(new_content,encoding="utf-8")

   return f"成功编辑文件：{path}"


def run_glob(pattern,base_dir=None):
   pattern_path = Path(pattern)

   if pattern_path.is_absolute():
      return "错误：不允许使用绝对路径查找"
   if ".." in pattern_path.parts:
      return "错误：不允许查找工作目录之外的文件"
   if base_dir is None:
      base = WORKDIR
   else:
      base = Path(base_dir).resolve()

   matches = []

   for path in base.glob(pattern):
      relative_path = path.relative_to(base)
      matches.append(str(relative_path))

   if len(matches) == 0:
      return "没有找到匹配的文件"

   return "\n".join(matches[:200])

