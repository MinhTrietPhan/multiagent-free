"""Resolve the project QA should inspect without guessing across the whole disk."""
from __future__ import annotations

import os
import re
from pathlib import Path

from langgraph.types import interrupt
from ...project_files import is_ignored_path


_IGNORE_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules",
    "__pycache__", "site-packages", "dist", "build", ".idea", ".vscode",
    ".mypy_cache", ".pytest_cache", ".next", "target",
}
_PROJECT_MARKERS = {
    "pyproject.toml", "setup.py", "setup.cfg", "package.json", "Cargo.toml",
    "go.mod", "pom.xml", "build.gradle", "requirements.txt", "composer.json",
    "Gemfile", "mix.exs", "*.sln",
}
_MAX_PROJECTS = 300


def _is_project_dir(path: Path) -> bool:
    try:
        if any(path.glob("PROJECT_CONTEXT*.md")):
            return True
        for marker in _PROJECT_MARKERS:
            if marker.startswith("*"):
                if any(path.glob(marker)):
                    return True
            elif (path / marker).is_file():
                return True
        return (path / ".git").exists()
    except OSError:
        return False


def workspace_roots() -> list[Path]:
    """Read explicitly configured workspace roots; default to cwd only."""
    raw = os.getenv("HUB_WORKSPACE_ROOTS", "").strip()
    if not raw:
        values = [str(Path.cwd())]
    else:
        # Semicolon is the documented separator and works for Windows paths.
        values = [item.strip().strip('"') for item in raw.split(";") if item.strip()]
        if len(values) == 1 and os.pathsep in values[0] and os.pathsep != ";":
            values = [item.strip() for item in values[0].split(os.pathsep) if item.strip()]

    roots: list[Path] = []
    seen: set[str] = set()
    for value in values:
        path = Path(value).expanduser()
        try:
            resolved = path.resolve()
            key = str(resolved).casefold()
            if resolved.is_dir() and key not in seen:
                roots.append(resolved)
                seen.add(key)
        except OSError:
            continue
    return roots


def discover_projects(roots: list[Path] | None = None) -> list[Path]:
    """Find project directories only beneath configured roots (bounded depth/count)."""
    roots = roots if roots is not None else workspace_roots()
    found: dict[str, Path] = {}
    try:
        max_depth = max(1, int(os.getenv("HUB_PROJECT_SCAN_DEPTH", "4")))
    except ValueError:
        max_depth = 4

    for root in roots:
        if _is_project_dir(root):
            found[str(root).casefold()] = root
        root_depth = len(root.parts)
        for current, dirs, _files in os.walk(root):
            current_path = Path(current)
            depth = len(current_path.parts) - root_depth
            dirs[:] = sorted(
                d for d in dirs
                if d not in _IGNORE_DIRS
                and not d.startswith(".")
                and not is_ignored_path((current_path / d).relative_to(root).as_posix(), root)
            )
            if depth >= max_depth:
                dirs[:] = []
            if current_path != root and _is_project_dir(current_path):
                found[str(current_path.resolve()).casefold()] = current_path.resolve()
                if len(found) >= _MAX_PROJECTS:
                    break
        if len(found) >= _MAX_PROJECTS:
            break

    return sorted(found.values(), key=lambda p: str(p).casefold())


def _extract_explicit_path(text: str) -> tuple[str, Path] | None:
    candidates: list[str] = []
    quoted = re.findall(r'["\']([^"\']+)["\']', text)
    candidates.extend(
        item for item in quoted
        if "/" in item or "\\" in item or item.startswith(("~", "."))
    )
    candidates.extend(re.findall(r"[A-Za-z]:[\\/][^\s\"'`<>|]+", text))
    candidates.extend(re.findall(r"(?<![\w:])/(?:[^\s\"'`<>|]+)", text))

    for raw in candidates:
        cleaned = raw.strip().rstrip(".,;:)")
        if not cleaned:
            continue
        path = Path(cleaned).expanduser()
        try:
            if path.is_dir():
                return raw, path.resolve()
        except OSError:
            continue
    return None


def _extract_project_hint(text: str) -> str | None:
    match = re.search(
        r"\b(?:project|repo(?:sitory)?|dự\s+án)\s+(?:là\s+)?[\"'`]?([\w .-]{2,80})",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    hint = match.group(1).strip().strip(" .,:;!?\"'`")
    hint = re.split(
        r"\s+(?:của tôi|này|kia|hiện tại|đó|trong|với|thì|là|đang)\b",
        hint,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip()
    if hint.casefold() in {"của tôi", "này", "kia", "hiện tại", "đó", "tôi", "root", "code", "project", "repo"}:
        return None
    return hint or None


def _match_projects(hint: str, projects: list[Path]) -> list[Path]:
    normalized = re.sub(r"\W+", "", hint.casefold(), flags=re.UNICODE)
    if not normalized:
        return []
    exact = [p for p in projects if re.sub(r"\W+", "", p.name.casefold(), flags=re.UNICODE) == normalized]
    if exact:
        return exact
    return [
        p for p in projects
        if normalized in re.sub(r"\W+", "", p.name.casefold(), flags=re.UNICODE)
        or re.sub(r"\W+", "", p.name.casefold(), flags=re.UNICODE) in normalized
    ]


def _ask_for_path(reason: str = "", *, allow_interrupt: bool = True) -> Path:
    if not allow_interrupt:
        raise FileNotFoundError(
            reason or "Chưa xác định được project root; cần chọn dự án trước khi chạy song song."
        )
    prompt = (
        (reason + "\n\n") if reason else ""
    ) + "Không xác định được dự án trong HUB_WORKSPACE_ROOTS. Nhập đường dẫn thư mục dự án (0 để huỷ):"
    answer = str(interrupt(prompt)).strip().strip('"').strip("'")
    if answer.casefold() in {"0", "q", "quit", "cancel", "hủy", "huỷ"}:
        raise InterruptedError("Người dùng đã huỷ chọn dự án.")
    path = Path(answer).expanduser()
    if not path.is_dir():
        raise FileNotFoundError(f"Không tìm thấy thư mục dự án: {answer}")
    return path.resolve()


def _choose_project(candidates: list[Path], *, allow_interrupt: bool = True) -> Path:
    lines = ["Tìm thấy nhiều dự án phù hợp. Chọn số dự án cần phân tích:"]
    lines.extend(f"{i}. {path}" for i, path in enumerate(candidates, 1))
    lines.append("Hoặc nhập đường dẫn khác; 0 để huỷ.")
    if not allow_interrupt:
        raise FileNotFoundError("Có nhiều dự án phù hợp; hãy chọn project root trước khi yêu cầu phối hợp agent.")
    answer = str(interrupt("\n".join(lines))).strip().strip('"').strip("'")
    if answer.casefold() in {"0", "q", "quit", "cancel", "hủy", "huỷ"}:
        raise InterruptedError("Người dùng đã huỷ chọn dự án.")
    if answer.isdigit() and 1 <= int(answer) <= len(candidates):
        return candidates[int(answer) - 1]
    path = Path(answer).expanduser()
    if path.is_dir():
        return path.resolve()
    matches = _match_projects(answer, candidates)
    if len(matches) == 1:
        return matches[0]
    raise FileNotFoundError(f"Lựa chọn không hợp lệ: {answer}")


def resolve_project_root(state: dict, question: str, *, allow_interrupt: bool = True) -> Path:
    """Resolve explicit path/name first, then remembered project, then unique workspace match."""
    explicit = _extract_explicit_path(question)
    if explicit:
        return explicit[1]

    hint = _extract_project_hint(question)
    if hint:
        projects = discover_projects()
        matches = _match_projects(hint, projects)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return _choose_project(matches, allow_interrupt=allow_interrupt)
        return _ask_for_path(
            f"Không tìm thấy dự án `{hint}` trong các workspace đã cấu hình.",
            allow_interrupt=allow_interrupt,
        )

    active = state.get("active_project_root")
    if active:
        active_path = Path(active).expanduser()
        if active_path.is_dir():
            return active_path.resolve()

    projects = discover_projects()
    if len(projects) == 1:
        return projects[0]
    if len(projects) > 1:
        return _choose_project(projects, allow_interrupt=allow_interrupt)
    return _ask_for_path(allow_interrupt=allow_interrupt)


def load_project_context(project_root: str | Path) -> tuple[str | None, Path | None]:
    """Load the newest context file directly inside the selected project root only."""
    root = Path(project_root).resolve()
    try:
        files = sorted(
            (p for p in root.glob("PROJECT_CONTEXT*.md") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        files = []
    if not files:
        return None, None
    path = files[0]
    return path.read_text(encoding="utf-8", errors="ignore"), path
