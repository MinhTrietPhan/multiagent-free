"""File discovery and directory tree rendering."""
import subprocess
from fnmatch import fnmatch
from pathlib import Path
from ..config import CODE_EXT, CONFIG_EXT, CONFIG_NAMES, IGNORE_DIRS, MANIFESTS, SKIP_NAMES
from ....project_files import is_ignored_path, iter_project_files

def _git_files(root: Path) -> list[str] | None:
    try:
        r = subprocess.run(['git', '-C', str(root), 'ls-files', '--cached', '--others', '--exclude-standard'], capture_output=True, text=True, check=True)
        return [l for l in r.stdout.splitlines() if l]
    except Exception:
        return None

def _walk(root: Path) -> list[str]:
    limit = int(__import__('os').getenv('HUB_CONTEXT_MAX_FILES', '5000'))
    return [p.relative_to(root).as_posix() for p in iter_project_files(root, max_files=limit)]

def discover(root: str, exclude: list[str] | tuple=()) -> tuple[list[str], list[str]]:
    """Return (code_files, manifest_files) as sorted posix paths relative to root."""
    r = Path(root)
    code, manifests = ([], [])
    max_files = max(1, int(__import__('os').getenv('HUB_CONTEXT_MAX_FILES', '5000')))
    for p in sorted(_git_files(r) or _walk(r)):
        if len(code) + len(manifests) >= max_files:
            break
        pp = Path(p)
        if any((part in IGNORE_DIRS for part in pp.parts)) or is_ignored_path(p, r):
            continue
        if any((fnmatch(p, pat) for pat in exclude)):
            continue
        try:
            if (r / p).stat().st_size > int(__import__('os').getenv('HUB_CONTEXT_MAX_FILE_BYTES', '2000000')):
                continue
        except OSError:
            continue
        if pp.suffix in CODE_EXT:
            code.append(p)
        elif pp.name in SKIP_NAMES:
            continue
        elif pp.name in MANIFESTS or pp.suffix in CONFIG_EXT or pp.name in CONFIG_NAMES:
            manifests.append(p)
    return (code, manifests)

def git_head(root: str) -> str | None:
    try:
        r = subprocess.run(['git', '-C', root, 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True, check=True)
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

    def walk(node: dict, prefix: str=''):
        items = sorted(node.items(), key=lambda kv: (not kv[1], kv[0]))
        for i, (name, child) in enumerate(items):
            last = i == len(items) - 1
            lines.append(f"{prefix}{('└── ' if last else '├── ')}{name}{('/' if child else '')}")
            if child:
                walk(child, prefix + ('    ' if last else '│   '))
    walk(tree)
    return '\n'.join(lines)
