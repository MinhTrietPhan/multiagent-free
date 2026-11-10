"""Helpers for the QA agent: read specific files or functions from a project on disk."""
import ast
import re
from pathlib import Path


def read_file_content(project_root: str, rel_path: str, max_lines: int = 500) -> str | None:
    """Read a file from the project.  Returns at most *max_lines* lines, or None if not found."""
    fp = Path(project_root) / rel_path
    if not fp.is_file():
        return None
    try:
        lines = fp.read_text(encoding="utf-8", errors="ignore").splitlines()
        numbered = [f"L{i+1}: {line}" for i, line in enumerate(lines[:max_lines])]
        if len(lines) > max_lines:
            numbered.append(f"... ({len(lines) - max_lines} dòng bị cắt)")
        return "\n".join(numbered)
    except Exception as e:
        return f"# Lỗi đọc file: {e}"


def read_function(project_root: str, rel_path: str, func_name: str) -> str | None:
    """Extract a single function/method from a Python file, with line numbers."""
    fp = Path(project_root) / rel_path
    if not fp.is_file():
        return None
    try:
        source = fp.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(source)
    except Exception:
        return None

    lines = source.splitlines()

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            start = node.lineno - 1  # 0-based
            end = node.end_lineno or (start + 1)
            snippet = lines[start:end]
            numbered = [f"L{start + i + 1}: {line}" for i, line in enumerate(snippet)]
            return "\n".join(numbered)

    return None


def find_project_root_from_context(context_text: str) -> str | None:
    """Try to extract the project root path from a PROJECT_CONTEXT.md header.

    The renderer writes a line like:  > Root: `D:\\path\\to\\project`
    """
    # Primary: look for the explicit Root line added by the renderer
    m = re.search(r"^>\s*Root:\s*`?([^`\n]+)`?", context_text, re.M)
    if m:
        p = Path(m.group(1).strip())
        if p.is_dir():
            return str(p)

    # Fallback: look for any path-like string near "Project", "Root", "Path", "Dự án"
    m = re.search(r"(?:Project|Root|Path|Dự án)[:\s]+[`\"']?([A-Za-z]:\\[^\s`\"']+|/[^\s`\"']+)", context_text, re.I)
    if m:
        p = Path(m.group(1))
        if p.is_dir():
            return str(p)

    # Last resort: try project name from header
    m = re.search(r"^#\s+(?:Project context:\s*)?(.+)", context_text, re.M)
    if m:
        name = m.group(1).strip().strip("`")
        for parent in [Path(".")]:
            candidate = parent / name
            if candidate.is_dir():
                return str(candidate.resolve())
    return None
