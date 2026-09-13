from pathlib import Path

import yaml

WORKDIR = Path.cwd().resolve()
SKILLS_DIR = WORKDIR / "skills"
SKILL_REGISTRY = {}


def parse_frontmatter(text):
   if not text.startswith("---"):
      return {},text

   parts = text.split("---",2)

   if len(parts)<3:
      return {},text

   try:
      meta = yaml.safe_load(parts[1]) or {}
   except yaml.YAMLError:
      meta = {}

   body = parts[2].strip()#strip() 删除正文开头和结尾多余的空格、空行

   return meta,body


def scan_skills():
   if not SKILLS_DIR.exists():
      return

   for skill_dir in sorted(SKILLS_DIR.iterdir()):
      if not skill_dir.is_dir():
         continue

      skill_file = skill_dir/"SKILL.md"

      if not skill_file.exists():
         continue

      raw = skill_file.read_text(encoding="utf-8")

      meta,body = parse_frontmatter(raw)

      name = meta.get("name",skill_dir.name)

      description = meta.get("description","没有描述")

      SKILL_REGISTRY[name] = {
         "name": name,
         "description": description,
         "content": raw,
      }


scan_skills()


def list_skills():
   if len(SKILL_REGISTRY)==0:
      return "- 暂无可用 Skill"

   lines=[]

   for skill in SKILL_REGISTRY.values():
      lines.append(
         f"- {skill['name']}: "
         f"{skill['description']}"
      )

   return "\n".join(lines)


def load_skill(name):
   skill = SKILL_REGISTRY.get(name)

   if skill is None:
      return f"未找到 Skill：{name}"

   return skill["content"]

