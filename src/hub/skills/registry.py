"""Filesystem-based Agent Skills loader. Skills are plain SKILL.md directories."""
from dataclasses import dataclass
from pathlib import Path
import os
import re

@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: Path
    content: str

class SkillRegistry:
    def __init__(self, roots=None):
        defaults = [Path.cwd() / "skills", Path.home() / ".agent-hub" / "skills"]
        self.roots = [Path(p).expanduser() for p in (roots or defaults)]

    def load_all(self) -> list[Skill]:
        found = {}
        for root in self.roots:
            if not root.is_dir():
                continue
            for file in root.glob("*/SKILL.md"):
                try:
                    content = file.read_text(encoding="utf-8")
                except OSError:
                    continue
                name = file.parent.name
                desc = ""
                m = re.search(r"(?im)^description:\s*(.+)$", content)
                if m:
                    desc = m.group(1).strip().strip("\"'")
                found[name] = Skill(name, desc or content.splitlines()[0][:160], file, content)
        return sorted(found.values(), key=lambda s: s.name)

    def select(self, query: str, limit: int = 3) -> list[Skill]:
        terms = set(re.findall(r"[\w-]{3,}", query.lower()))
        scored = []
        for skill in self.load_all():
            haystack = f"{skill.name} {skill.description} {skill.content[:2500]}".lower()
            score = sum(1 for term in terms if term in haystack)
            if score:
                scored.append((score, skill))
        return [s for _, s in sorted(scored, key=lambda x: (-x[0], x[1].name))[:limit]]

