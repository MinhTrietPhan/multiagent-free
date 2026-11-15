"""Shared project-file filtering for context, QA and search agents."""
from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from functools import lru_cache

# Generic generated/dependency directories. Prefix/glob matching also catches
# versioned environments such as .venv-release and PyInstaller *_internal dirs.
DEFAULT_IGNORED_DIRS = (
    ".git", ".hg", ".svn", ".idea", ".vscode",
    ".venv", ".venv-*", "venv", "venv-*", "env", "env-*",
    "node_modules", "site-packages", "__pycache__", ".ipynb_checkpoints",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".nox",
    ".next", ".nuxt", ".cache", "build", "dist", "coverage",
    "target", "_internal", "*_internal",
)
DEFAULT_IGNORED_FILES = ("*.pyc", "*.pyo", "*.pyd", "*.so", "*.dll", "*.exe", "*.class")


@lru_cache(maxsize=128)
def _agentignore_patterns_cached(root_text: str, mtime_ns: int) -> tuple[str, ...]:
    path = Path(root_text) / ".agentignore"
    try:
        return tuple(
            line.strip().replace("\\", "/")
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
    except OSError:
        return ()


def _agentignore_patterns(root: Path) -> tuple[str, ...]:
    path = root / ".agentignore"
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        stamp = 0
    return _agentignore_patterns_cached(str(root.resolve()), stamp)


def is_ignored_path(relative_path: str | Path, root: str | Path, extra_patterns=()) -> bool:
    """Return True for generated/dependency paths or project-local .agentignore matches.

    .agentignore supports simple gitignore-style glob patterns. A pattern without
    '/' matches any path component; patterns containing '/' match the relative path.
    Negation is intentionally not supported in this first version.
    """
    rel = Path(relative_path).as_posix().strip("/")
    parts = [part for part in rel.split("/") if part]
    if not parts:
        return False
    root_path = Path(root)

    for part in parts:
        if any(fnmatch.fnmatchcase(part, pattern) for pattern in DEFAULT_IGNORED_DIRS):
            return True
    if any(fnmatch.fnmatchcase(parts[-1], pattern) for pattern in DEFAULT_IGNORED_FILES):
        return True

    patterns = [*_agentignore_patterns(root_path), *extra_patterns]
    for raw in patterns:
        pattern = str(raw).strip().replace("\\", "/").lstrip("/")
        if not pattern:
            continue
        if "/" in pattern:
            if fnmatch.fnmatchcase(rel, pattern) or rel == pattern.rstrip("/") or rel.startswith(pattern.rstrip("/") + "/" ):
                return True
        elif any(fnmatch.fnmatchcase(part, pattern) for part in parts):
            return True
    return False


def iter_project_files(root: str | Path, *, max_files: int = 20000):
    """Yield non-ignored files under root, pruning ignored directories during walk."""
    base = Path(root).resolve()
    yielded = 0
    for current, dirs, files in os.walk(base):
        current_path = Path(current)
        rel_dir = current_path.relative_to(base)
        dirs[:] = sorted(
            name for name in dirs
            if not is_ignored_path((rel_dir / name).as_posix(), base)
            and not name.startswith(".")
        )
        for name in sorted(files):
            path = current_path / name
            rel = path.relative_to(base).as_posix()
            if is_ignored_path(rel, base):
                continue
            yield path
            yielded += 1
            if yielded >= max_files:
                return
