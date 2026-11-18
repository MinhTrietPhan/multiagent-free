"""Deterministic tools of the code agent: context, patch build/apply, git, checks. No LLM in here."""
from __future__ import annotations

import ast
import difflib
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ...project_files import iter_project_files
from ..context.tools.pyparse import analyze
from .security import PathDenied, check_read, check_write, is_allowed_command, is_secret_path, normalize_rel

MAX_FILE_BYTES = 400_000
MAX_EDITS = 40
MAX_FILES = 15
TEXT_EXTS = {
    ".py", ".md", ".txt", ".toml", ".json", ".yaml", ".yml", ".ini", ".cfg", ".js", ".ts", ".tsx",
    ".jsx", ".html", ".css", ".sh", ".bat", ".ps1", ".sql", ".rst",
}


# --------------------------------------------------------------------------- budget
class ToolBudget:
    """Counts logical operations (file reads, searches, LLM calls, commands), not internal scans."""

    def __init__(self, limit: int, used: int = 0):
        self.limit, self.used = max(1, int(limit)), int(used)

    def try_spend(self, n: int = 1) -> bool:
        if self.used + n > self.limit:
            return False
        self.used += n
        return True


# --------------------------------------------------------------------------- file io
def read_text_file(path: Path) -> tuple[str, str, str]:
    """Return (text with \\n newlines, original newline style, sha256 of the raw bytes)."""
    raw = Path(path).read_bytes()
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError(f"file quá lớn (> {MAX_FILE_BYTES // 1000} KB)")
    if b"\x00" in raw[:4096]:
        raise ValueError("file nhị phân")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("file không phải UTF-8") from exc
    newline = "\r\n" if "\r\n" in text else "\n"
    return text.replace("\r\n", "\n"), newline, hashlib.sha256(raw).hexdigest()


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# --------------------------------------------------------------------------- request analysis
_KIND_RULES = (
    ("test", ("pytest", "unit test", "viết test", "thêm test", "kiểm thử")),
    ("refactor", ("refactor", "tái cấu trúc", "đổi tên", "rename")),
    ("debug", ("bug", "lỗi", "traceback", "debug", "fix", "sửa lỗi")),
    ("create", ("tạo file", "tạo mới", "file mới", "new file", "create file")),
)
_PATH_RE = re.compile(r"[\w][\w./\\-]*\.[A-Za-z0-9]{1,6}")
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")
_STOPWORDS = {
    "this", "that", "with", "from", "into", "file", "files", "code", "class", "python", "please",
    "make", "need", "want", "should", "have", "and", "for", "the", "add", "gồm", "giữ",
}


def classify_request(text: str) -> str:
    lowered = (text or "").casefold()
    for kind, words in _KIND_RULES:
        if any(word in lowered for word in words):
            return kind
    return "modify"


def extract_paths(root: Path, request: str, limit: int = 6) -> list[str]:
    root = Path(root).resolve()
    found: list[str] = []
    index: dict[str, list[str]] | None = None
    for token in _PATH_RE.findall(request or ""):
        rel = normalize_rel(token.strip(".,;:()\"'`"))
        if not rel or rel in found:
            continue
        try:
            path = check_read(root, rel)
        except PathDenied:
            path = None
        if path is not None and path.is_file():
            found.append(path.relative_to(root).as_posix())
            continue
        if "/" not in rel:  # bare file name such as model.py -> accept only if unique in the project
            if index is None:
                index = {}
                for p in iter_project_files(root, max_files=5000):
                    r = p.relative_to(root).as_posix()
                    if not is_secret_path(r):
                        index.setdefault(p.name.casefold(), []).append(r)
            hits = index.get(rel.casefold(), [])
            if len(hits) == 1 and hits[0] not in found:
                found.append(hits[0])
    return found[:limit]


def keywords(request: str, limit: int = 8) -> list[str]:
    seen: list[str] = []
    for word in _WORD_RE.findall(request or ""):
        low = word.casefold()
        if low not in _STOPWORDS and low not in seen:
            seen.append(low)
    return seen[:limit]


def search_files(root: Path, kws: list[str], exclude: set[str], limit: int = 4) -> list[str]:
    root = Path(root).resolve()
    scored: list[tuple[int, str]] = []
    for path in iter_project_files(root, max_files=3000):
        rel = path.relative_to(root).as_posix()
        if rel in exclude or is_secret_path(rel) or path.suffix.lower() not in TEXT_EXTS:
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore").casefold()
        except OSError:
            continue
        score = sum(min(text.count(k), 10) for k in kws) + sum(5 for k in kws if k in rel.casefold())
        if score:
            scored.append((score, rel))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [rel for _, rel in scored[:limit]]


def _file_section(root: Path, rel: str, allowed: int) -> str | None:
    try:
        text, _newline, _digest = read_text_file(check_read(root, rel))
    except (PathDenied, ValueError, OSError):
        return None
    if allowed < 800:
        return None
    if len(text) <= allowed - 80:
        return f'<file path="{rel}">\n{text}\n</file>'
    lines = text.split("\n")
    head, tail, count = [], [], 0
    for line in lines:
        if count + len(line) + 1 > int(allowed * 0.4):
            break
        head.append(line)
        count += len(line) + 1
    count = 0
    for line in reversed(lines):
        if count + len(line) + 1 > int(allowed * 0.5):
            break
        tail.append(line)
        count += len(line) + 1
    tail.reverse()
    marker = f"[... bỏ qua dòng {len(head) + 1}-{len(lines) - len(tail)} ...]"
    body = "\n".join([*head, marker, *tail])
    return f'<file path="{rel}" partial="true">\n{body}\n</file>'


def _related_section(root: Path, rel: str, cap: int) -> str | None:
    try:
        text, _newline, _digest = read_text_file(check_read(root, rel))
    except (PathDenied, ValueError, OSError):
        return None
    if rel.endswith(".py"):
        body = "\n".join(analyze(text)["skeleton"]) or text[:cap]
    else:
        body = text[:cap]
    return f'<related path="{rel}">\n{body[:cap]}\n</related>'


RULE_FILES = ("CONTRIBUTING.md", "AGENTS.md", "CLAUDE.md")


def project_rules(root: Path, cap: int = 3000) -> str:
    """Project conventions (CONTRIBUTING.md / AGENTS.md / CLAUDE.md) so the agent follows them.

    Read leniently: these files are only advice for Gemini, so a legacy Windows encoding must not hide them.
    """
    for name in RULE_FILES:
        try:
            raw = check_read(root, name).read_bytes()[: MAX_FILE_BYTES]
        except (PathDenied, OSError):
            continue
        text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n")
        return f'<project_rules path="{name}">\n{text[:cap]}\n</project_rules>'
    return ""


def project_map(root: Path, cap: int) -> str:
    """Repo map: signature skeleton of every Python file (cheap overview, like an index of the project)."""
    root = Path(root).resolve()
    blocks: list[str] = []
    size = 0
    count = 0
    for path in iter_project_files(root, max_files=400):
        rel = path.relative_to(root).as_posix()
        if is_secret_path(rel) or path.suffix.lower() != ".py":
            continue
        try:
            text, _newline, _digest = read_text_file(path)
        except (ValueError, OSError):
            continue
        skeleton = "\n".join(analyze(text)["skeleton"]) or "(không có định nghĩa cấp cao)"
        block = f"# {rel}\n{skeleton}"[:1800]
        if size + len(block) + 1 > cap:
            blocks.append("... (còn nhiều file Python khác, bản đồ đã đầy)")
            break
        blocks.append(block)
        size += len(block) + 1
        count += 1
    return "<repo_map>\n" + "\n".join(blocks) + "\n</repo_map>" if blocks else ""


def read_more(root: Path, paths: list[str], room: int, budget: ToolBudget) -> tuple[str, list[str]]:
    """Read files requested by the explore/review step (same secret/ignore rules as everything else)."""
    root = Path(root).resolve()
    parts: list[str] = []
    got: list[str] = []
    for rel in paths:
        if room < 900 or not budget.try_spend():
            break
        try:
            check_read(root, normalize_rel(rel))
        except PathDenied:
            continue
        section = _file_section(root, normalize_rel(rel), min(room - 100, 14000))
        if section:
            parts.append(section)
            got.append(normalize_rel(rel))
            room -= len(section) + 2
    return "\n\n".join(parts), got


def build_context(root: Path, request: str, touched: list[str], max_chars: int,
                  budget: ToolBudget, extra: list[str] | tuple = ()) -> tuple[str, list[str], list[str]]:
    """Return (context, files shown, notes). Rules + relevant files + file tree + repo map, bounded by max_chars."""
    root = Path(root).resolve()
    notes: list[str] = []
    parts: list[str] = []
    shown: list[str] = []
    used = 0

    rules = project_rules(root, min(3000, max_chars // 8)) if budget.try_spend() else ""
    if rules:
        parts.append(rules)
        used += len(rules)

    reserve_tree = min(2500, max_chars // 6)
    reserve_map = min(6000, max_chars // 4)
    explicit = list(dict.fromkeys([*touched, *[normalize_rel(e) for e in extra], *extract_paths(root, request)]))
    for rel in explicit:
        if not budget.try_spend():
            notes.append("Đạt giới hạn tool calls khi đọc file; context có thể thiếu.")
            break
        allowed = min(int(max_chars * 0.6), max_chars - used - reserve_tree - reserve_map)
        section = _file_section(root, rel, allowed)
        if section:
            parts.append(section)
            shown.append(rel)
            used += len(section)

    if budget.try_spend():
        rels = [p.relative_to(root).as_posix() for p in iter_project_files(root, max_files=400)]
        rels = [r for r in rels if not is_secret_path(r)]
        lines, size = [], 0
        for rel in rels:
            if size + len(rel) + 1 > reserve_tree:
                lines.append(f"... (+{len(rels) - len(lines)} file khác)")
                break
            lines.append(rel)
            size += len(rel) + 1
        tree = "<project_tree>\n" + "\n".join(lines) + "\n</project_tree>"
        if used + len(tree) <= max_chars:
            parts.append(tree)
            used += len(tree)

    if budget.try_spend():
        repo_map = project_map(root, min(reserve_map, max(0, max_chars - used - 200)))
        if repo_map and used + len(repo_map) <= max_chars:
            parts.append(repo_map)
            used += len(repo_map)

    kws = keywords(request)
    if kws and budget.try_spend():
        for rel in search_files(root, kws, set(shown)):
            remaining = max_chars - used
            if remaining < 600:
                break
            section = _related_section(root, rel, min(1500, remaining - 100))
            if section and used + len(section) <= max_chars:
                parts.append(section)
                used += len(section)
    return "\n\n".join(parts), shown, notes


# --------------------------------------------------------------------------- patch building
def _compiles(text: str, name: str) -> bool:
    try:
        compile(text, name, "exec", dont_inherit=True)
        return True
    except (SyntaxError, ValueError):
        return False


def _diff(rel: str, old: str | None, new: str) -> tuple[str, int, int]:
    a = [] if old is None else old.splitlines()
    b = new.splitlines()
    lines = list(difflib.unified_diff(
        a, b, fromfile="/dev/null" if old is None else f"a/{rel}", tofile=f"b/{rel}", lineterm="", n=3))
    added = sum(1 for l in lines if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in lines if l.startswith("-") and not l.startswith("---"))
    return "\n".join(lines), added, removed


def _main_guard_start(text: str) -> int | None:
    """0-based line index where the top-level `if __name__ == ...` block starts (comments above it included)."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    for node in tree.body:
        test = getattr(node, "test", None)
        if (isinstance(node, ast.If) and isinstance(test, ast.Compare)
                and isinstance(test.left, ast.Name) and test.left.id == "__name__"):
            lines = text.split("\n")
            start = node.lineno - 1
            while start > 0 and lines[start - 1].lstrip().startswith("#"):
                start -= 1
            return start
    return None


def _dedupe_imports(text: str, content: str) -> tuple[str, list[str]]:
    """Drop single-line imports from appended code that the file already imports at top level."""
    existing = {l.strip() for l in text.split("\n") if l.startswith(("import ", "from "))}
    kept, dropped = [], []
    for line in content.split("\n"):
        if line.startswith(("import ", "from ")) and line.strip() in existing:
            dropped.append(line)
        else:
            kept.append(line)
    return "\n".join(kept).strip("\n"), dropped


def _closest_excerpt(text: str, old: str, context: int = 6) -> str:
    """Real lines of the file nearest to the first line Gemini tried to anchor on (so a retry can copy them)."""
    first = next((l.strip() for l in old.split("\n") if l.strip()), "")
    lines = text.split("\n")
    if not first:
        return ""
    close = difflib.get_close_matches(first, [l.strip() for l in lines], n=1, cutoff=0.6)
    if not close:
        return ""
    index = next(i for i, l in enumerate(lines) if l.strip() == close[0])
    excerpt = "\n".join(lines[max(0, index - 1): index + context])[:700]
    return f"\nĐoạn gần nhất THỰC TẾ trong file (copy chính xác từ đây, giữ thụt lề):\n{excerpt}"


def _fuzzy_replace(text: str, old: str, new: str) -> str | None:
    """Fallback when `old` is not verbatim: match whole lines ignoring indentation/trailing whitespace.

    Accepted only if exactly one window of the file matches; `new` is re-indented by the indentation
    difference of the first line. The user still reviews the resulting diff before anything is written.
    """
    lines = text.split("\n")
    given = old.split("\n")
    while given and not given[0].strip():
        given.pop(0)
    while given and not given[-1].strip():
        given.pop()
    if not given:
        return None
    key = [l.strip() for l in given]
    hits = [i for i in range(len(lines) - len(key) + 1) if all(lines[i + j].strip() == key[j] for j in range(len(key)))]
    if len(hits) != 1:
        return None
    start_line = hits[0]
    actual_first = lines[start_line]
    delta = (len(actual_first) - len(actual_first.lstrip())) - (len(given[0]) - len(given[0].lstrip()))
    new_lines = new.split("\n")
    if delta and "\t" not in actual_first[: len(actual_first) - len(actual_first.lstrip())]:
        shifted = []
        for line in new_lines:
            if not line.strip():
                shifted.append(line)
            elif delta > 0:
                shifted.append(" " * delta + line)
            else:
                drop = min(-delta, len(line) - len(line.lstrip()))
                shifted.append(line[drop:])
        new_lines = shifted
    offset = sum(len(l) + 1 for l in lines[:start_line])
    length = len("\n".join(lines[start_line:start_line + len(key)]))
    return text[:offset] + "\n".join(new_lines) + text[offset + length:]


def build_changes(root: Path, edits: list[dict]) -> tuple[list[dict], list[str], list[str]]:
    """Apply edits in memory. Returns (changes, errors, warnings); nothing touches the disk."""
    root = Path(root).resolve()
    if not edits:
        return [], ["Không có chỉnh sửa nào được đề xuất."], []
    if len(edits) > MAX_EDITS:
        return [], [f"Quá nhiều chỉnh sửa ({len(edits)} > {MAX_EDITS})."], []

    groups: dict[str, list[dict]] = {}
    names: dict[str, str] = {}
    for edit in edits:
        rel = normalize_rel(edit.get("path", ""))
        key = rel.casefold()
        names.setdefault(key, rel)
        groups.setdefault(key, []).append(edit)
    if len(groups) > MAX_FILES:
        return [], [f"Chạm quá nhiều file ({len(groups)} > {MAX_FILES})."], []

    changes, errors, warnings = [], [], []
    for key, group in groups.items():
        rel = names[key]
        try:
            target = check_write(root, rel)
        except PathDenied as exc:
            errors.append(f"{rel or '(rỗng)'}: {exc}")
            continue
        rel = target.relative_to(root).as_posix()
        if target.suffix.lower() == ".ipynb":
            errors.append(f"{rel}: chưa hỗ trợ sửa notebook .ipynb.")
            continue
        if target.exists() and not target.is_file():
            errors.append(f"{rel}: không phải file thường.")
            continue

        original = digest = None
        newline = "\n"
        if target.exists():
            try:
                original, newline, digest = read_text_file(target)
            except (ValueError, OSError) as exc:
                errors.append(f"{rel}: không đọc được ({exc}).")
                continue
            raw = target.read_bytes()
            if raw.count(b"\r\n") and raw.count(b"\n") > raw.count(b"\r\n"):
                warnings.append(f"{rel}: newline hỗn hợp (CRLF và LF), sẽ chuẩn hoá về CRLF.")
        text = original
        failed = False
        for number, edit in enumerate(group, 1):
            action = edit.get("action")
            if action == "create":
                content = (edit.get("content") or "").replace("\r\n", "\n")
                if text is not None:
                    errors.append(f"{rel}: action create nhưng file đã tồn tại; dùng modify, không ghi đè.")
                    failed = True
                elif not content.strip():
                    errors.append(f"{rel}: nội dung file mới rỗng.")
                    failed = True
                else:
                    text = content if content.endswith("\n") else content + "\n"
            elif action == "modify":
                old = (edit.get("old_str") or "").replace("\r\n", "\n")
                new = (edit.get("new_str") or "").replace("\r\n", "\n")
                if text is None:
                    errors.append(f"{rel}: action modify nhưng file chưa tồn tại.")
                    failed = True
                elif not old:
                    errors.append(f"{rel}: old_str #{number} rỗng.")
                    failed = True
                else:
                    count = text.count(old)
                    if count == 0:
                        fuzzy = _fuzzy_replace(text, old, new)
                        if fuzzy is not None:
                            text = fuzzy
                            warnings.append(f"{rel}: old_str #{number} chỉ khớp sau khi bỏ qua khác biệt khoảng trắng/thụt lề; hãy xem kỹ diff.")
                        else:
                            errors.append(f"{rel}: old_str #{number} không khớp nguyên văn với file "
                                          f"(kiểm tra thụt lề, khoảng trắng): {old[:80]!r}" + _closest_excerpt(text, old))
                            failed = True
                    elif count > 1:
                        errors.append(f"{rel}: old_str #{number} xuất hiện {count} lần; cần đoạn dài hơn để duy nhất.")
                        failed = True
                    else:
                        text = text.replace(old, new, 1)
            elif action == "append":
                content = (edit.get("content") or "").replace("\r\n", "\n").strip("\n")
                if text is None:
                    errors.append(f"{rel}: action append nhưng file chưa tồn tại.")
                    failed = True
                elif not content.strip():
                    errors.append(f"{rel}: nội dung append rỗng.")
                    failed = True
                else:
                    if rel.endswith(".py"):
                        content, dropped = _dedupe_imports(text, content)
                        if dropped:
                            warnings.append(f"{rel}: bỏ {len(dropped)} dòng import đã có sẵn ở đầu file.")
                        leftover = [l for l in content.split("\n") if l.startswith(("import ", "from "))]
                        if leftover:
                            warnings.append(f"{rel}: code mới có {len(leftover)} dòng import ở giữa file "
                                            "(ruff có thể báo E402); nên đưa lên đầu file.")
                    if not content.strip():
                        errors.append(f"{rel}: nội dung append rỗng sau khi bỏ import trùng.")
                        failed = True
                        break
                    guard = _main_guard_start(text) if rel.endswith(".py") else None
                    if guard is not None:  # keep `if __name__ == "__main__":` last: insert before it
                        lines = text.split("\n")
                        head = "\n".join(lines[:guard]).rstrip("\n")
                        text = head + "\n\n\n" + content + "\n\n\n" + "\n".join(lines[guard:])
                        warnings.append(f"{rel}: code mới được chèn TRƯỚC khối `if __name__ == ...`.")
                    else:
                        base = text.rstrip("\n")
                        separator = "\n\n\n" if rel.endswith(".py") else "\n\n"
                        text = (base + separator if base else "") + content + "\n"
            else:
                errors.append(f"{rel}: action không hợp lệ: {action!r}.")
                failed = True
            if failed:
                break
        if failed:
            continue
        if text == original:
            continue
        if len(text.encode("utf-8")) > MAX_FILE_BYTES:
            errors.append(f"{rel}: file sau khi sửa quá lớn.")
            continue
        if rel.endswith(".py") and not _compiles(text, rel) and (original is None or _compiles(original, rel)):
            errors.append(f"{rel}: cú pháp Python lỗi sau khi sửa.")
            continue
        diff, added, removed = _diff(rel, original, text)
        if removed > 40:
            warnings.append(f"{rel}: patch xoá {removed} dòng; hãy kiểm tra kỹ.")
        changes.append({
            "path": rel, "action": "create" if original is None else "modify", "new_text": text,
            "newline": newline, "orig_hash": digest, "diff": diff, "added": added, "removed": removed,
        })
    if not changes and not errors:
        errors.append("Các chỉnh sửa không tạo ra thay đổi thực sự.")
    return changes, errors, warnings


# --------------------------------------------------------------------------- applying
@dataclass
class ApplyResult:
    ok: bool
    error: str = ""
    applied: list[dict] = field(default_factory=list)
    backup_dir: str = ""


def _atomic_write(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".code-agent.tmp")
    tmp.write_bytes(data)
    if target.exists():
        shutil.copymode(target, tmp)
    os.replace(tmp, target)


def apply_changes(root: Path, changes: list[dict], backup_root: Path) -> ApplyResult:
    """All-or-nothing: verify nothing changed since validation, back up originals, write, roll back on error."""
    root = Path(root).resolve()
    targets: list[tuple[dict, Path]] = []
    for change in changes:
        try:
            target = check_write(root, change["path"])
        except PathDenied as exc:
            return ApplyResult(False, f"{change['path']}: {exc}")
        if change["action"] == "modify":
            if not target.is_file() or _sha256(target) != change["orig_hash"]:
                return ApplyResult(False, f"{change['path']}: file đã thay đổi từ lúc tạo patch; không áp dụng. Hãy chạy lại /code.")
        elif target.exists():
            return ApplyResult(False, f"{change['path']}: file đã tồn tại từ lúc tạo patch; không ghi đè.")
        targets.append((change, target))

    backup_dir = Path(backup_root) / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    try:
        for change, target in targets:
            if change["action"] == "modify":
                dest = backup_dir / change["path"]
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, dest)
    except OSError as exc:
        return ApplyResult(False, f"Không tạo được bản backup: {exc}")

    written: list[tuple[dict, Path]] = []
    try:
        for change, target in targets:
            _atomic_write(target, change["new_text"].replace("\n", change["newline"]).encode("utf-8"))
            written.append((change, target))
    except Exception as exc:  # roll back everything written so far
        for change, target in written:
            try:
                if change["action"] == "modify":
                    shutil.copy2(backup_dir / change["path"], target)
                else:
                    target.unlink(missing_ok=True)
            except OSError:
                pass
        return ApplyResult(False, f"Ghi file thất bại, đã hoàn tác: {type(exc).__name__}: {exc}")
    applied = [{"path": c["path"], "action": c["action"], "added": c["added"], "removed": c["removed"]}
               for c, _ in written]
    return ApplyResult(True, applied=applied, backup_dir=str(backup_dir) if backup_dir.exists() else "")


# --------------------------------------------------------------------------- git (read-only)
def _git(root: Path, args: list[str], timeout: int = 20) -> str | None:
    if not args or args[0] not in {"diff", "rev-parse"}:
        raise ValueError(f"git subcommand không nằm trong allowlist: {args[:1]}")
    try:
        done = subprocess.run(["git", "-C", str(root), "--no-pager", *args], capture_output=True, text=True,
                              timeout=timeout, encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None


def git_dirty_paths(root: Path) -> set[str] | None:
    """Tracked files with uncommitted changes (relative to root). None if not a git work tree."""
    if (_git(root, ["rev-parse", "--is-inside-work-tree"]) or "").strip() != "true":
        return None
    out = set()
    for extra in ([], ["--cached"]):
        text = _git(root, ["diff", "--name-only", "--relative", *extra])
        out.update(line.strip() for line in (text or "").splitlines() if line.strip())
    return out


def git_stat(root: Path, paths: list[str]) -> str | None:
    if not paths or (_git(root, ["rev-parse", "--is-inside-work-tree"]) or "").strip() != "true":
        return None
    text = _git(root, ["diff", "--stat", "--relative", "HEAD", "--", *paths])
    return text.strip() if text and text.strip() else None


# --------------------------------------------------------------------------- checks
def project_python(root: Path, configured: str = "") -> str:
    if configured and Path(configured).is_file():
        return str(configured)
    for candidate in (".venv/Scripts/python.exe", ".venv/bin/python", "venv/Scripts/python.exe", "venv/bin/python"):
        path = Path(root) / candidate
        if path.is_file():
            return str(path)
    return sys.executable


def _pyproject_text(root: Path) -> str:
    try:
        return (Path(root) / "pyproject.toml").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def has_pytest(root: Path) -> bool:
    root = Path(root)
    return ((root / "tests").is_dir() or (root / "pytest.ini").is_file()
            or "[tool.pytest" in _pyproject_text(root))


def has_ruff(root: Path) -> bool:
    root = Path(root)
    return (root / "ruff.toml").is_file() or (root / ".ruff.toml").is_file() or "[tool.ruff" in _pyproject_text(root)


def _is_test_file(rel: str) -> bool:
    name = Path(rel).name
    return rel.endswith(".py") and (rel.startswith("tests/") or "/tests/" in rel
                                    or name.startswith("test_") or name.endswith("_test.py"))


def plan_checks(root: Path, changed: list[str], python: str = "") -> list[list[str]]:
    """Only run tools the project actually uses (plus the tests the agent itself just wrote)."""
    py = project_python(root, python)
    pyfiles = [p for p in changed if p.endswith(".py")]
    commands: list[list[str]] = []
    if pyfiles:
        prefix = "pycache_prefix=" + os.path.join(tempfile.gettempdir(), "code-agent-pyc")
        commands.append([py, "-X", prefix, "-m", "compileall", "-q", *pyfiles])
        if has_ruff(root):
            commands.append([py, "-m", "ruff", "check", *pyfiles])
    if has_pytest(root):
        commands.append([py, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"])
    else:
        tests = [p for p in pyfiles if _is_test_file(p)]
        if tests:
            commands.append([py, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *tests])
    return commands


def display_command(argv: list[str]) -> str:
    rest = list(argv[1:])
    if len(rest) >= 2 and rest[0] == "-X":
        rest = rest[2:]
    for i in range(len(rest) - 1):                      # cosmetic flag, not worth showing
        if rest[i:i + 2] == ["-p", "no:cacheprovider"]:
            del rest[i:i + 2]
            break
    first = next((i for i, a in enumerate(rest) if a.endswith(".py")), len(rest))
    files = rest[first:]
    if len(files) > 3:
        files = [*files[:3], f"... (+{len(files) - 3} file)"]
    return " ".join(["python", *rest[:first], *files])


_SECRET_ENV = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", re.IGNORECASE)


def _clean_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not _SECRET_ENV.search(k)}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _is_project_module(root: Path, name: str) -> bool:
    top = name.split(".")[0]
    root = Path(root)
    return any(p.exists() for p in (root / top, root / f"{top}.py", root / "src" / top, root / "src" / f"{top}.py"))


def run_checks(root: Path, commands: list[list[str]], timeout: int, budget: ToolBudget, python: str = "") -> list[dict]:
    root = Path(root).resolve()
    py = project_python(root, python)
    results: list[dict] = []
    for argv in commands:
        label = display_command(argv)
        if not is_allowed_command(argv, py):
            results.append({"cmd": label, "ok": False, "skipped": False, "output": "Lệnh không nằm trong allowlist."})
            continue
        if not budget.try_spend():
            results.append({"cmd": label, "ok": True, "skipped": True, "output": "Bỏ qua: đạt giới hạn tool calls."})
            break
        try:
            done = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=timeout,
                                  env=_clean_env(), encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            results.append({"cmd": label, "ok": False, "skipped": False, "output": f"Quá thời gian {timeout}s."})
            continue
        except OSError as exc:
            results.append({"cmd": label, "ok": False, "skipped": False, "output": f"Không chạy được: {exc}"})
            continue
        output = ((done.stdout or "") + (done.stderr or "")).strip()
        output = output[-4000:]
        if re.search(r"No module named '?(pytest|ruff)", output):
            results.append({"cmd": label, "ok": True, "skipped": True, "output": "Bỏ qua: công cụ chưa được cài trong môi trường project."})
        elif (done.returncode != 0 and (m := re.search(r"No module named '([\w.]+)'", output))
              and not _is_project_module(root, m.group(1))):
            results.append({"cmd": label, "ok": True, "skipped": True,
                            "output": f"Bỏ qua: Python đang dùng ({py}) thiếu module '{m.group(1)}', không phải lỗi patch. "
                                      "Đặt interpreter của dự án bằng: /code config python_path <đường dẫn python.exe>"})
        elif done.returncode == 5 and "pytest" in label:
            results.append({"cmd": label, "ok": True, "skipped": True, "output": "pytest không tìm thấy test nào."})
        else:
            results.append({"cmd": label, "ok": done.returncode == 0, "skipped": False, "output": output})
    return results


def summarize_failures(results: list[dict], limit: int = 3000) -> str:
    chunks = [f"$ {r['cmd']}\n{r['output']}" for r in results if not r["ok"]]
    return "\n\n".join(chunks)[-limit:]

