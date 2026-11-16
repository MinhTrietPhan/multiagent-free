"""Code-agent settings: ~/.agent-hub/code-agent.json (or $HUB_DATA_DIR/code-agent.json).

Invalid values never crash the agent: they fall back to the default and produce a warning.
`approval_mode=auto` can only be enabled by editing the JSON file by hand, not from chat.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from ...memory.store import data_dir

APPROVAL_MODES = ("approve", "auto")
MODEL_STRATEGIES = ("cheap", "hybrid", "strong")
# Bounds keep one run inside LangGraph's default recursion limit (25 steps per invocation).
INT_BOUNDS = {
    "max_repair_attempts": (0, 3),
    "max_rounds": (1, 3),
    "explore_rounds": (0, 3),
    "max_tool_calls": (1, 200),
    "max_context_chars": (2_000, 200_000),
    "command_timeout": (10, 1800),
}
BOOL_FIELDS = {"auto_run_tests"}


@dataclass(frozen=True)
class CodeSettings:
    approval_mode: str = "approve"
    model_strategy: str = "hybrid"
    max_repair_attempts: int = 2
    max_rounds: int = 3          # work rounds: after each applied patch the agent reviews and may continue/ask
    explore_rounds: int = 2      # extra "which files should I read?" LLM rounds before proposing (0 = off)
    max_tool_calls: int = 40
    max_context_chars: int = 24_000
    auto_run_tests: bool = True
    command_timeout: int = 300
    python_path: str = ""        # interpreter used for checks (empty = project .venv, else the hub's own Python)


def settings_path() -> Path:
    return data_dir() / "code-agent.json"


def _parse_bool(value):
    if isinstance(value, bool):
        return value
    text = str(value).strip().casefold()
    if text in {"true", "1", "yes", "y", "on", "bật", "bat"}:
        return True
    if text in {"false", "0", "no", "n", "off", "tắt", "tat"}:
        return False
    raise ValueError(f"không phải boolean: {value!r}")


def _coerce(key: str, value):
    if key == "approval_mode":
        text = str(value).strip().casefold()
        if text not in APPROVAL_MODES:
            raise ValueError(f"approval_mode phải là một trong {APPROVAL_MODES}")
        return text
    if key == "model_strategy":
        text = str(value).strip().casefold()
        if text not in MODEL_STRATEGIES:
            raise ValueError(f"model_strategy phải là một trong {MODEL_STRATEGIES}")
        return text
    if key in BOOL_FIELDS:
        return _parse_bool(value)
    if key == "python_path":
        text = str(value or "").strip().strip('"').strip("'")
        if text and not Path(text).expanduser().is_file():
            raise ValueError(f"không tìm thấy file Python: {text}")
        return str(Path(text).expanduser()) if text else ""
    if key in INT_BOUNDS:
        if isinstance(value, bool):
            raise ValueError(f"{key} phải là số nguyên")
        number = int(str(value).strip())
        low, high = INT_BOUNDS[key]
        if not low <= number <= high:
            raise ValueError(f"{key} phải nằm trong [{low}, {high}]")
        return number
    raise KeyError(key)


def sanitize(raw: dict) -> tuple[CodeSettings, list[str]]:
    defaults = asdict(CodeSettings())
    values, warnings = dict(defaults), []
    for key, value in (raw or {}).items():
        if key not in defaults:
            warnings.append(f"Bỏ qua khoá không biết trong code-agent.json: {key}")
            continue
        try:
            values[key] = _coerce(key, value)
        except (ValueError, KeyError) as exc:
            warnings.append(f"Giá trị không hợp lệ cho {key} ({exc}); dùng mặc định {defaults[key]!r}.")
    return CodeSettings(**values), warnings


def load_settings_checked(path: str | Path | None = None) -> tuple[CodeSettings, list[str]]:
    target = Path(path) if path else settings_path()
    if not target.is_file():
        return CodeSettings(), []
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("gốc JSON phải là object")
    except (OSError, ValueError) as exc:
        return CodeSettings(), [f"Không đọc được {target.name} ({exc}); dùng cấu hình mặc định."]
    return sanitize(raw)


def load_settings(path: str | Path | None = None) -> CodeSettings:
    return load_settings_checked(path)[0]


def save_settings(settings: CodeSettings, path: str | Path | None = None) -> Path:
    target = Path(path) if path else settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(asdict(settings), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


def describe_settings(settings: CodeSettings, path: str | Path | None = None) -> str:
    target = Path(path) if path else settings_path()
    lines = [f"Cấu hình Code Agent ({target}):"]
    lines += [f"  {f.name} = {getattr(settings, f.name)}" for f in fields(settings)]
    strong = os.getenv("HUB_MODEL_CODE_STRONG", "").strip() or "(chưa đặt: dùng MODEL_POOL mặc định)"
    lines += [
        f"  HUB_MODEL_CODE = {os.getenv('HUB_MODEL_CODE', '').strip() or '(chưa đặt)'}",
        f"  HUB_MODEL_CODE_STRONG = {strong}",
        "Đổi: /code config <khoá> <giá trị> · đặt lại: /code config reset",
        "approval_mode=auto chỉ bật được bằng cách sửa file JSON trực tiếp.",
        "python_path: đặt python.exe của môi trường chạy được dự án (vd có torch) để agent tự kiểm tra đúng.",
    ]
    return "\n".join(lines)


_PREFIXES = ("config", "settings", "cấu hình", "cau hinh")


def handle_config_command(text: str, path: str | Path | None = None) -> str | None:
    """Return a reply if `text` is a config command, otherwise None (a normal code request)."""
    value = (text or "").strip()
    folded = value.casefold()
    prefix = next((p for p in _PREFIXES if folded == p or folded.startswith(p + " ")), None)
    if prefix is None:
        return None
    rest = value[len(prefix):].strip()
    target = Path(path) if path else settings_path()
    current, _ = load_settings_checked(target)
    if not rest:
        return describe_settings(current, target)
    if rest.casefold() == "reset":
        save_settings(CodeSettings(), target)
        return "Đã đặt lại cấu hình Code Agent về mặc định.\n" + describe_settings(CodeSettings(), target)

    if "=" in rest.split()[0]:
        key, _, raw_value = rest.partition("=")
    else:
        key, _, raw_value = rest.partition(" ")
    key = key.strip().casefold().replace("-", "_")
    if key not in asdict(current):
        return None  # e.g. "config the logger ..." is an ordinary request
    raw_value = raw_value.strip().strip('"').strip("'")
    if key == "approval_mode" and raw_value.casefold() == "auto":
        return ("Không bật approval_mode=auto từ chat. Nếu thật sự muốn, sửa tay "
                f'"approval_mode": "auto" trong {target}.')
    try:
        new_value = _coerce(key, raw_value)
    except (ValueError, KeyError) as exc:
        return f"Giá trị không hợp lệ: {exc}"
    values = asdict(current)
    values[key] = new_value
    save_settings(CodeSettings(**values), target)
    return f"Đã đặt {key} = {new_value}."
