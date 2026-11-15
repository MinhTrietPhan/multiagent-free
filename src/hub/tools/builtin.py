"""Safe built-in tools shared by agents."""
from pathlib import Path
from ..project_files import iter_project_files
import re
from .registry import ToolSpec, get_tool_registry


def search_project(project_root: str, query: str, max_results: int = 20) -> str:
    """Search source/text files while pruning virtualenvs, bundles and build output."""
    root = Path(project_root).resolve()
    phrase = query.lower().strip()
    if not phrase:
        return "Query rỗng."
    terms = [t for t in re.findall(r"[\w.-]{3,}", phrase) if t not in {"của", "trong", "nào", "được", "cho", "the", "and", "with", "from"}]
    hits = []
    max_file_bytes = int(__import__("os").getenv("HUB_SEARCH_MAX_FILE_BYTES", "1000000"))
    max_scan_files = int(__import__("os").getenv("HUB_SEARCH_MAX_FILES", "12000"))
    for path in iter_project_files(root, max_files=max_scan_files):
        try:
            if path.stat().st_size > max_file_bytes:
                continue
            # Ignore obvious binary/media/archive files; source and text remain searchable.
            if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".mp3", ".wav", ".zip", ".7z", ".pdf", ".pt", ".pth", ".onnx", ".sqlite", ".db"}:
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                low = line.lower()
                if phrase in low or any(term in low for term in terms):
                    hits.append(f"{path.relative_to(root).as_posix()}:L{number}: {line.strip()[:300]}")
                    if len(hits) >= max_results:
                        return "\n".join(hits)
        except (OSError, UnicodeError):
            continue
    return "\n".join(hits) if hits else "Không tìm thấy kết quả."


def _read_file_tool(**kwargs):
    from ..agents.qa.tools import read_file_content
    return read_file_content(**kwargs)


def _read_function_tool(project_root: str, rel_path: str, function_name: str):
    from ..agents.qa.tools import read_function
    return read_function(project_root, rel_path, function_name)


def register_builtin_tools():
    registry = get_tool_registry()
    specs = [
        ToolSpec("read_file", "Đọc file nằm trong project root, có kiểm tra path traversal.", _read_file_tool),
        ToolSpec("read_function", "Trích xuất function/class từ file trong project root.", _read_function_tool),
        ToolSpec("search_project", "Tìm chuỗi trong các file text của dự án (giới hạn kết quả).", search_project),
    ]
    for spec in specs:
        try:
            registry.register(spec)
        except ValueError:
            pass
    return registry
