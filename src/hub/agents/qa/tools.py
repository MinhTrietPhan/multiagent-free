"""Helpers for the QA agent: read specific files / functions from a project on disk (any language).

Mọi đường dẫn do model yêu cầu đều bị chặn nếu trỏ ra ngoài thư mục dự án.
"""
import ast
import re
from pathlib import Path

DEFAULT_WINDOW = 500   # số dòng đọc mỗi lần khi không chỉ định khoảng
MAX_CHARS = 60_000     # giới hạn mềm mỗi lần đọc; vượt thì báo dòng bắt đầu để đọc tiếp
MAX_MATCHES = 3        # tối đa số định nghĩa trùng tên trả về cùng lúc

_BRACE_EXT = {'.js', '.jsx', '.mjs', '.cjs', '.ts', '.tsx', '.java', '.kt', '.kts', '.scala', '.c', '.h', '.cc', '.cpp', '.hpp', '.cs', '.go', '.rs', '.php', '.swift', '.dart', '.sh', '.bash', '.zsh'}
_CONTROL = {'if', 'else', 'elif', 'for', 'while', 'switch', 'catch', 'return', 'new', 'throw', 'await', 'case', 'do', 'try', 'with', 'assert', 'yield', 'typeof', 'delete', 'foreach', 'when', 'match'}


def _safe_path(project_root: str, rel_path: str) -> Path | None:
    """File tồn tại VÀ nằm trong project_root (chặn ../ và đường dẫn tuyệt đối ra ngoài)."""
    try:
        root = Path(project_root).resolve()
        fp = (root / rel_path).resolve()
        fp.relative_to(root)
    except (ValueError, OSError):
        return None
    return fp if fp.is_file() else None


def _emit(lines: list[str], start0: int, end0: int) -> str:
    """Lines [start0, end0) có đánh số dòng, cắt mềm theo MAX_CHARS."""
    out, used = ([], 0)
    for i in range(start0, min(end0, len(lines))):
        row = f'L{i + 1}: {lines[i]}'
        used += len(row) + 1
        if used > MAX_CHARS and out:
            break
        out.append(row)
    last = start0 + len(out)
    if last < min(end0, len(lines)):
        out.append(f'... (đã hiển thị L{start0 + 1}-L{last}; còn tiếp, đọc tiếp bằng "start": {last + 1})')
    return '\n'.join(out)


def read_file_content(project_root: str, rel_path: str, start: int=1, end: int | None=None, max_lines: int=DEFAULT_WINDOW) -> str | None:
    """Đọc file (bất kỳ ngôn ngữ). Không chỉ định `end` -> đọc `max_lines` dòng từ `start`.

    Chỉ định start/end -> đọc đúng khoảng đó (cắt mềm theo MAX_CHARS). Luôn báo còn bao nhiêu dòng để đọc tiếp.
    Trả None nếu không tìm thấy / ngoài thư mục dự án.
    """
    fp = _safe_path(project_root, rel_path)
    if fp is None:
        return None
    try:
        lines = fp.read_text(encoding='utf-8', errors='ignore').splitlines()
    except Exception as e:
        return f'# Lỗi đọc file: {e}'
    total = len(lines)
    start = max(1, int(start or 1))
    if start > total:
        return f'# File chỉ có {total} dòng.'
    stop = min(total, max(start, int(end))) if end else min(total, start - 1 + max_lines)
    body = _emit(lines, start - 1, stop)
    if stop < total and 'đọc tiếp' not in body:
        body += f'\n... (hiển thị L{start}-L{stop} / {total} dòng; còn {total - stop} dòng, đọc tiếp bằng "start": {stop + 1})'
    return body


def _python_ranges(source: str, name: str) -> list[tuple[int, int]] | None:
    """Định nghĩa hàm/class/method Python (hỗ trợ 'Class.method'). None nếu file lỗi cú pháp."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    owner_path, _, fn = name.rpartition('.')
    owner = owner_path.split('.')[-1] if owner_path else ''
    found: list[tuple[int, int]] = []

    def visit(node, stack):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if child.name == fn and (not owner or (stack and stack[-1] == owner)):
                    first = min([child.lineno] + [d.lineno for d in child.decorator_list])
                    found.append((first - 1, child.end_lineno or child.lineno))
                visit(child, stack + [child.name])
            else:
                visit(child, stack)
    visit(tree, [])
    return found


def _opens_block(lines: list[str], i: int, limit: int=8) -> bool:
    """Dòng i (có thể kéo dài nhiều dòng) có mở khối `{` trước khi gặp `;` không."""
    for j in range(i, min(len(lines), i + limit)):
        t = lines[j].rstrip()
        if j > i and t.lstrip().startswith('{'):
            return True
        if t.endswith('{'):
            return True
        if t.endswith(';'):
            return False
    return False


def _brace_end(lines: list[str], i: int) -> int | None:
    """Khớp ngoặc nhọn từ dòng i, bỏ qua chuỗi và comment (xấp xỉ). Trả chỉ số dòng kết thúc (exclusive)."""
    depth, started, in_comment = (0, False, False)
    for j in range(i, len(lines)):
        line, k, quote = (lines[j], 0, None)
        while k < len(line):
            ch, two = (line[k], line[k:k + 2])
            if in_comment:
                if two == '*/':
                    in_comment, k = (False, k + 2)
                else:
                    k += 1
                continue
            if quote:
                if ch == '\\':
                    k += 2
                    continue
                if ch == quote:
                    quote = None
                k += 1
                continue
            if two == '//':
                break
            if two == '/*':
                in_comment, k = (True, k + 2)
                continue
            if ch in '"\'`':
                quote = ch
            elif ch == '{':
                depth, started = (depth + 1, True)
            elif ch == '}':
                depth -= 1
                if started and depth <= 0:
                    return j + 1
            k += 1
        if j - i > 3000 and not started:
            return None
    return None


def _indent_end(lines: list[str], i: int) -> int:
    """Kết thúc khối theo thụt lề (Ruby/Lua/shell/...). Dòng `end`/`}`/`fi` cùng cấp được tính vào khối."""
    base = len(lines[i]) - len(lines[i].lstrip())
    last = i
    for j in range(i + 1, len(lines)):
        t = lines[j]
        if not t.strip():
            continue
        ind = len(t) - len(t.lstrip())
        if ind <= base:
            if ind == base and t.strip().split()[0] in {'end', '}', 'fi', 'done', 'esac', 'end;'}:
                last = j
            break
        last = j
    return last + 1


def _generic_ranges(lines: list[str], name: str, ext: str) -> list[tuple[int, int]]:
    """Tìm định nghĩa hàm/class theo heuristic (JS/TS/Java/Kotlin/C/C++/C#/Go/Rust/PHP/Swift/Ruby/shell/...)."""
    fn = name.rpartition('.')[2]
    n = re.escape(fn)
    kw = re.compile(rf'(?<![\w$.])(function|fn|func|def|fun|sub|class|struct|interface|enum|trait|impl|type|object|module|proc)\b[\w\s<>\[\],.*&()?:$]*?(?<![\w$]){n}(?![\w$])')
    assign = re.compile(rf'(?<![\w$.]){n}\s*[:=]\s*(async\s+)?(function\b|\([^)]*\)\s*(:\s*[^=]+)?=>|\w+\s*=>)')
    call_def = re.compile(rf'(?<![\w$.!(]){n}\s*(<[^>]*>)?\s*\(')
    ranges: list[tuple[int, int]] = []
    skip_until = -1
    for i, line in enumerate(lines):
        if i < skip_until:
            continue
        s = line.strip()
        if not s or s.startswith(('//', '#', '*', '/*', '--')):
            continue
        is_def = bool(kw.search(line) or assign.search(line))
        if not is_def:
            m = call_def.search(line)
            first_word = re.match('[A-Za-z_]+', s)
            if m and not s.endswith(';') and not (first_word and first_word.group(0) in _CONTROL):
                prefix = line[:m.start()]
                if '=' not in prefix and '(' not in prefix and _opens_block(lines, i):
                    is_def = True
        if not is_def:
            continue
        use_braces = ext in _BRACE_EXT or (ext not in {'.rb', '.lua', '.ex', '.exs', '.py'} and _opens_block(lines, i))
        end = _brace_end(lines, i) if use_braces else None
        if end is None:
            end = _indent_end(lines, i)
        ranges.append((i, end))
        skip_until = end
        if len(ranges) >= MAX_MATCHES:
            break
    return ranges


def read_function(project_root: str, rel_path: str, func_name: str) -> str | None:
    """Trích một hàm/method/class (có số dòng). Python dùng ast; ngôn ngữ khác dùng heuristic.

    `func_name` có thể là 'name' hoặc 'Class.method' (Python). Trả None nếu không tìm thấy.
    """
    fp = _safe_path(project_root, rel_path)
    if fp is None:
        return None
    try:
        source = fp.read_text(encoding='utf-8', errors='ignore')
    except Exception:
        return None
    lines = source.splitlines()
    ext = fp.suffix.lower()
    ranges = _python_ranges(source, func_name) if ext == '.py' else None
    if ranges is None:
        ranges = _generic_ranges(lines, func_name, ext)
    if not ranges:
        return None
    return '\n# ---\n'.join((_emit(lines, s, e) for s, e in ranges[:MAX_MATCHES]))


def find_project_root_from_context(context_text: str) -> str | None:
    """Try to extract the project root path from a PROJECT_CONTEXT.md header.

    The renderer writes a line like:  > Root: `D:\\path\\to\\project`
    """
    m = re.search('^>\\s*Root:\\s*`?([^`\\n]+)`?', context_text, re.M)
    if m:
        p = Path(m.group(1).strip())
        if p.is_dir():
            return str(p)
    m = re.search('(?:Project|Root|Path|Dự án)[:\\s]+[`\\"\']?([A-Za-z]:\\\\[^\\s`\\"\']+|/[^\\s`\\"\']+)', context_text, re.I)
    if m:
        p = Path(m.group(1))
        if p.is_dir():
            return str(p)
    m = re.search('^#\\s+(?:Project context:\\s*)?(.+)', context_text, re.M)
    if m:
        name = m.group(1).strip().strip('`')
        for parent in [Path('.')]:
            candidate = parent / name
            if candidate.is_dir():
                return str(candidate.resolve())
    return None
