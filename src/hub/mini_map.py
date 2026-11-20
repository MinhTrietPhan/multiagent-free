"""On-the-fly project index for the QA agent.

The map is a navigation aid only: directory tree with file counts, entry points and
project scripts. It never contains source code, never reads/creates
``PROJECT_CONTEXT*.md`` and writes nothing to disk. Details are read on demand by the
QA tools (``search_project`` / ``read_file`` / ``read_function``).

Budget: the rendered text is capped (default 3000 chars, ``HUB_QA_MINIMAP_CHARS``).
When the cap is reached the least important branches are dropped first, so tests/,
docs/ and examples/ disappear before src/.
"""
from __future__ import annotations

import fnmatch
import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .project_files import iter_project_files

DEFAULT_MAX_CHARS = 3000
MIN_MAX_CHARS = 500
MAX_MAX_CHARS = 20000
MAX_DEPTH = 4            # directory levels rendered below the project root
MAX_FILES_SCAN = 5000    # stop the walk here so huge repos stay cheap
MAX_ENTRIES = 160        # hard cap of rendered lines
MAX_ENTRY_POINTS = 8
INLINE_FILES = 6         # small dirs list their file names inline

ENTRY_NAMES = {"main.py", "cli.py", "app.py", "run.py", "__main__.py",
               "manage.py", "wsgi.py", "asgi.py", "server.py", "index.js", "index.ts"}

# Directory importance: lower bucket renders first, so it survives truncation.
_HIGH_DIRS = {"src", "app", "lib", "pkg", "packages", "core", "agents", "hub",
              "backend", "frontend", "server", "api", "cmd", "internal"}
_LOW_DIRS = {"tests", "test", "docs", "doc", "examples", "example", "samples",
             "benchmarks", "fixtures", "migrations", "assets", "static", "public",
             "vendor", "third_party", "scripts"}

# Only names are filtered (contents are never read by this module).
_SECRET_PATTERNS = (".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.jks",
                    "*.keystore", "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*",
                    ".netrc", ".npmrc", ".pypirc", ".htpasswd", "credentials*",
                    "*credentials*.json", "secrets*", "secret.*", "*secret*",
                    "service-account*.json", "service_account*.json", "token.json",
                    "*.kdbx", "*.tfvars", "*.tfstate")
# Generated metadata that is never useful for navigation.
_NOISE_DIR_SUFFIXES = (".egg-info", ".dist-info", ".ipynb_checkpoints")
_NOISE_DIR_NAMES = {"__pycache__", "node_modules", "site-packages"}


@dataclass
class _Dir:
    files: list[str] = field(default_factory=list)
    dirs: dict[str, "_Dir"] = field(default_factory=dict)
    total: int = 0


def _budget(max_chars: int | None) -> int:
    if max_chars is None:
        try:
            max_chars = int(os.getenv("HUB_QA_MINIMAP_CHARS", str(DEFAULT_MAX_CHARS)))
        except (TypeError, ValueError):
            max_chars = DEFAULT_MAX_CHARS
    return max(MIN_MAX_CHARS, min(MAX_MAX_CHARS, int(max_chars)))


def _is_noise(name: str) -> bool:
    folded = name.casefold()
    if folded.startswith("."):
        return True
    return any(fnmatch.fnmatchcase(folded, pattern) for pattern in _SECRET_PATTERNS)


def _bucket(name: str) -> int:
    folded = name.casefold()
    if folded in _HIGH_DIRS:
        return 0
    if folded in _LOW_DIRS:
        return 2
    return 1


def _scan(root: Path) -> tuple[list[str], bool]:
    """Return non-ignored, non-secret relative paths plus a "scan capped" flag."""
    paths: list[str] = []
    for path in iter_project_files(root, max_files=MAX_FILES_SCAN):
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if _is_noise(path.name):
            continue
        if any(part.endswith(_NOISE_DIR_SUFFIXES) or part in _NOISE_DIR_NAMES
               for part in Path(rel).parts[:-1]):
            continue
        paths.append(rel)
    return paths, len(paths) >= MAX_FILES_SCAN


def _build_tree(paths: list[str]) -> _Dir:
    root = _Dir()
    for rel in paths:
        parts = rel.split("/")
        node = root
        for part in parts[:-1]:
            node = node.dirs.setdefault(part, _Dir())
        node.files.append(parts[-1])
    _count(root)
    return root


def _count(node: _Dir) -> int:
    node.total = len(node.files) + sum(_count(child) for child in node.dirs.values())
    return node.total


def _pyproject_scripts(root: Path) -> list[str]:
    try:
        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    except Exception:
        return []
    scripts = data.get("project", {}).get("scripts") or {}
    return [f"{name} = {target}" for name, target in list(scripts.items())[:4]]


def _package_entry(root: Path) -> list[str]:
    try:
        data = json.loads((root / "package.json").read_text(encoding="utf-8"))
    except Exception:
        return []
    found = []
    if isinstance(data.get("main"), str):
        found.append(f"main = {data['main']}")
    scripts = data.get("scripts")
    if isinstance(scripts, dict) and isinstance(scripts.get("start"), str):
        found.append(f"start = {scripts['start']}")
    return found[:2]


def _entry_points(paths: list[str], root: Path) -> list[str]:
    named = [rel for rel in paths if Path(rel).name in ENTRY_NAMES]
    named.sort(key=lambda rel: (rel.count("/"), rel.casefold()))
    entries = named[:MAX_ENTRY_POINTS]
    entries += _pyproject_scripts(root)
    entries += _package_entry(root)
    return entries


def _dir_lines(node: _Dir, depth: int, prefix: str) -> list[str]:
    if depth > MAX_DEPTH:
        return []
    children = sorted(
        node.dirs.items(),
        key=lambda item: (_bucket(item[0]), -item[1].total, item[0].casefold()),
    )
    lines: list[str] = []
    for name, child in children:
        indent = "  " * (depth - 1)
        rel = f"{prefix}{name}/"
        label = f"{indent}{rel} ({child.total} file)"
        if child.total <= INLINE_FILES and not child.dirs and child.files:
            names = ", ".join(sorted(child.files, key=str.casefold))
            label = f"{indent}{rel} ({child.total} file: {names})"
        lines.append(label)
        lines.extend(_dir_lines(child, depth + 1, f"{rel}"))
    return lines


def build_mini_map(root: str | Path, max_chars: int | None = None) -> str:
    """Render a compact, always-current index of ``root``. Never touches the disk."""
    base = Path(root).resolve()
    budget = _budget(max_chars)
    try:
        paths, capped = _scan(base)
    except OSError as exc:
        return f"Dự án: {base.name}\nKhông quét được thư mục: {type(exc).__name__}: {exc}"

    if not paths:
        return f"Dự án: {base.name}\n(chưa thấy file source nào; hãy kiểm tra đường dẫn dự án)"

    tree = _build_tree(paths)
    entries = _entry_points(paths, base)
    root_files = sorted(
        (name for name in tree.files if not _is_noise(name)),
        key=str.casefold,
    )

    header = f"Dự án: {base.name} — {len(paths)} file" + (" (đã chạm giới hạn quét)" if capped else "")
    body: list[str] = []
    if entries:
        body.append("Entry points:")
        body += [f"- {entry}" for entry in entries]
    if root_files:
        shown = ", ".join(root_files[:8])
        more = f", … +{len(root_files) - 8}" if len(root_files) > 8 else ""
        body.append(f"Tệp ở gốc: {shown}{more}")
    tree_lines = _dir_lines(tree, 1, "")
    if tree_lines:
        body.append("Cây thư mục:")
        body += tree_lines

    hint = ("Ghi chú: đây chỉ là chỉ mục điều hướng, không chứa nội dung code. "
            "Đọc chi tiết bằng search_project/read_file/read_function.")

    # Fit into the budget: keep the most important lines first, drop the tail.
    kept: list[str] = []
    dropped = 0
    reserve = len(hint) + len("\n... +999 mục khác")
    used = len(header)
    for index, line in enumerate(body):
        if len(kept) >= MAX_ENTRIES or used + len(line) + 1 + reserve > budget:
            dropped = len(body) - index
            break
        kept.append(line)
        used += len(line) + 1

    out = [header, *kept]
    if dropped:
        out.append(f"... +{dropped} mục khác")
    out.append(hint)
    return "\n".join(out)
