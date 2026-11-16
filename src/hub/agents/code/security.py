"""Path, secret and command policy. Every file read/write and every subprocess goes through here.

Rules are deterministic Python; Gemini output is never trusted to respect them.
"""
from __future__ import annotations

import fnmatch
from pathlib import Path, PurePosixPath, PureWindowsPath

from ...project_files import is_ignored_path


class PathDenied(Exception):
    """A path was rejected; the message says why (shown to the user / fed back to Gemini)."""


SECRET_NAMES = (
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore",
    "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*", ".netrc", ".npmrc", ".pypirc", ".htpasswd",
    "credentials", "credentials.*", "*credentials*.json", "secrets", "secrets.*", "secret.*",
    "*.secret", "*.secrets", "service-account*.json", "service_account*.json",
    "client_secret*.json", "token.json", "*.tfvars", "*.tfstate", "*.kdbx",
)
SECRET_DIRS = {".ssh", ".aws", ".gnupg", ".kube"}
TEMPLATE_SUFFIXES = (".example", ".sample", ".template", ".dist")  # .env.example holds no secrets
PROTECTED_DIRS = {".git", ".hg", ".svn"}
# The agent must not be able to edit its own guard rails when it works on agent-hub itself.
SELF_PROTECTED = ("src/hub/agents/code/*",)
WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def is_secret_path(rel: str | Path) -> bool:
    parts = [p for p in PurePosixPath(str(rel).replace("\\", "/")).parts if p not in ("/", "")]
    if any(p.casefold() in SECRET_DIRS for p in parts[:-1]):
        return True
    if not parts:
        return False
    name = parts[-1].casefold()
    if name.startswith(".env") and name.endswith(TEMPLATE_SUFFIXES):
        return False
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in SECRET_NAMES)


def normalize_rel(rel: str) -> str:
    value = str(rel or "").strip().replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return value


def _check_syntax(rel: str) -> str:
    if not rel:
        raise PathDenied("đường dẫn rỗng")
    if "\x00" in rel or ":" in rel:
        raise PathDenied(f"đường dẫn không hợp lệ: {rel!r}")
    if rel.startswith("/") or Path(rel).is_absolute():
        raise PathDenied(f"không nhận đường dẫn tuyệt đối: {rel}")
    parts = rel.split("/")
    if any(p == ".." for p in parts):
        raise PathDenied(f"không được dùng '..': {rel}")
    for part in parts:
        if part in ("", "."):
            raise PathDenied(f"thành phần đường dẫn rỗng: {rel}")
        if part != part.rstrip(" ."):
            raise PathDenied(f"tên có dấu chấm/khoảng trắng ở cuối: {rel}")
        if part.split(".")[0].casefold() in WINDOWS_RESERVED:
            raise PathDenied(f"tên thiết bị Windows bị cấm: {rel}")
    return rel


def resolve_inside(root: Path, rel: str) -> Path:
    """Resolve `rel` under `root` (following symlinks) and prove it stays inside."""
    root = Path(root).resolve()
    rel = _check_syntax(normalize_rel(rel))
    try:
        target = (root / rel).resolve()
        target.relative_to(root)
    except (ValueError, OSError):
        raise PathDenied(f"đường dẫn nằm ngoài project root: {rel}") from None
    return target


def check_read(root: Path, rel: str) -> Path:
    root = Path(root).resolve()
    target = resolve_inside(root, rel)
    posix = target.relative_to(root).as_posix()
    if is_secret_path(posix):
        raise PathDenied(f"file nhạy cảm (secret/credentials) bị chặn: {posix}")
    if is_ignored_path(posix, root):
        raise PathDenied(f"đường dẫn bị bỏ qua (.git, venv, build, .agentignore...): {posix}")
    return target


def check_write(root: Path, rel: str) -> Path:
    root = Path(root).resolve()
    target = check_read(root, rel)
    posix = target.relative_to(root).as_posix()
    if any(p.casefold() in PROTECTED_DIRS for p in posix.split("/")):
        raise PathDenied(f"không được sửa thư mục điều khiển phiên bản: {posix}")
    if any(fnmatch.fnmatchcase(posix, pattern) for pattern in SELF_PROTECTED):
        raise PathDenied(f"file thuộc cơ chế bảo vệ của Code Agent, không tự sửa: {posix}")
    return target


def in_workspace(root: Path) -> bool:
    """The project must live under HUB_WORKSPACE_ROOTS (default: the current directory)."""
    from ..qa.project import workspace_roots

    resolved = Path(root).resolve()
    if resolved.parent == resolved:
        return False
    for workspace in workspace_roots():
        try:
            resolved.relative_to(workspace)
            return True
        except ValueError:
            continue
    return False


def _safe_arg(arg: str) -> bool:
    """Relative, inside the project, no option flags. Checked with POSIX *and* Windows rules so the
    result does not depend on the OS the tests happen to run on ('/etc' has no drive on Windows)."""
    if not arg or arg.startswith(("-", "/", "\\")) or ":" in arg:
        return False
    return not any(".." in PurePath.parts for PurePath in (PurePosixPath(arg), PureWindowsPath(arg)))


def is_allowed_command(argv: list[str], python: str) -> bool:
    """Allowlist: only the project's Python running compileall / ruff check / pytest."""
    if not argv or argv[0] != python:
        return False
    rest = list(argv[1:])
    if len(rest) >= 2 and rest[0] == "-X" and rest[1].startswith("pycache_prefix="):
        rest = rest[2:]
    if rest[:2] == ["-m", "compileall"]:
        args = rest[2:]
        return len(args) > 1 and args[0] == "-q" and all(_safe_arg(a) for a in args[1:])
    if rest[:2] == ["-m", "ruff"]:
        return len(rest) > 3 and rest[2] == "check" and all(_safe_arg(a) for a in rest[3:])
    if rest[:2] == ["-m", "pytest"]:
        args = rest[2:]
        if args[:2] != ["-q", "-x"]:
            return False
        args = args[2:]
        if args[:2] == ["-p", "no:cacheprovider"]:
            args = args[2:]
        return all(a.endswith(".py") and _safe_arg(a) for a in args)  # whole suite, or specific test files
    return False
