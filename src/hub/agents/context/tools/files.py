"""File discovery and directory tree rendering."""
import subprocess
from fnmatch import fnmatch
from pathlib import Path

from ..config import CODE_EXT, IGNORE_DIRS, MANIFESTS


def _git_files(root: Path) -> list[str] | None:
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard"],
            capture_output=True, text=True, check=True,
        )
        return [l for l in r.stdout.splitlines() if l]
    except Exception:
        return None


def _walk(root: Path) -> list[str]:
    out = []
    for p in root.rglob("*"):
        if p.is_file():
            out.append(p.relative_to(root).as_posix())
    return out


def discover(root: str, exclude: list[str] | tuple = ()) -> tuple[list[str], list[str]]:
    """Return (code_files, manifest_files) as sorted posix paths relative to root."""
    r = Path(root)
    code, manifests = [], []
    for p in sorted(_git_files(r) or _walk(r)):
        pp = Path(p)
        if any(part in IGNORE_DIRS for part in pp.parts):
            continue
        if any(fnmatch(p, pat) for pat in exclude):
            continue
        if pp.suffix in CODE_EXT:
            code.append(p)
        elif pp.name in MANIFESTS:
            manifests.append(p)
    return code, manifests


def git_head(root: str) -> str | None:
    try:
        r = subprocess.run(["git", "-C", root, "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, check=True)
        return r.stdout.strip() or None
    except Exception:
        return None


def render_tree(paths: list[str]) -> str:
    tree: dict = {}
    for p in paths:
        node = tree
        for part in Path(p).parts:
            node = node.setdefault(part, {})
    lines: list[str] = []

    def walk(node: dict, prefix: str = ""):
        items = sorted(node.items(), key=lambda kv: (not kv[1], kv[0]))  # dirs first
        for i, (name, child) in enumerate(items):
            last = i == len(items) - 1
            lines.append(f"{prefix}{'└── ' if last else '├── '}{name}{'/' if child else ''}")
            if child:
                walk(child, prefix + ("    " if last else "│   "))

    walk(tree)
    return "\n".join(lines)
