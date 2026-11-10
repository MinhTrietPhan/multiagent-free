"""Hub-facing wrapper of the context agent: parse the request, ask the fixed menu if needed, run, reply."""
import os
import re
from pathlib import Path
from langchain_core.messages import AIMessage
from langgraph.types import interrupt
from .config import output_filename
from .graph import build_context
from .tools.tokens import count_tokens
MENU = 'Bạn muốn mức ngữ cảnh nào?\n  1. map       Bản đồ nhanh (cây thư mục + skeleton)\n  2. balanced  Cân bằng (bản đồ + full code vài file chính)\n  3. full      Đầy đủ (toàn bộ code, đã bỏ comment/output notebook)\nNhập 1/2/3 (mặc định 2):'
_MODE_KEYWORDS = {'map': ('map', 'tổng quan', 'bản đồ', 'skeleton'), 'full': ('full', 'đầy đủ', 'toàn bộ'), 'balanced': ('balanced', 'cân bằng')}


def _last_user_text(state) -> str:
    for m in reversed(state.get('messages', [])):
        if getattr(m, 'type', '') == 'human':
            return m.content if isinstance(m.content, str) else str(m.content)
    return ''


def _find_dir(text: str) -> str | None:
    for tok in re.findall('[\\w./~\\\\:-]+', text):
        cleaned = tok.rstrip(',:;')
        if cleaned and Path(cleaned).expanduser().is_dir():
            return str(Path(cleaned).expanduser().resolve())
    return None


def _find_mode(text: str) -> str | None:
    low = text.lower()
    for mode, kws in _MODE_KEYWORDS.items():
        if any((re.search(f'\\b{re.escape(k)}\\b', low) for k in kws)):
            return mode
    return None
CANCEL = {'0', 'q', 'exit', 'huỷ', 'huy', 'cancel'}


def context_node(state) -> dict:
    text = _last_user_text(state)
    root = _find_dir(text)
    if root is None:
        ans = str(interrupt('Dự án nằm ở đâu? Nhập đường dẫn thư mục (gõ . cho thư mục hiện tại, 0 để huỷ):')).strip().strip('"')
        if ans.lower() in CANCEL:
            return {'messages': [AIMessage('Đã huỷ, không tạo file.')]}
        if not Path(ans).expanduser().is_dir():
            return {'messages': [AIMessage(f'Không tìm thấy thư mục: {ans}')]}
        root = str(Path(ans).expanduser())
    mode = _find_mode(text)
    if mode is None:
        choice = str(interrupt(MENU)).strip().lower()
        if choice in CANCEL:
            return {'messages': [AIMessage('Đã huỷ, không tạo file.')]}
        mode = {'1': 'map', '2': 'balanced', '3': 'full'}.get(choice, choice if choice in _MODE_KEYWORDS else 'balanced')
    out = build_context(root, mode)
    out_file = os.getenv('HUB_CONTEXT_FILE') or output_filename(mode)
    Path(out_file).parent.mkdir(parents=True, exist_ok=True)
    Path(out_file).write_text(out, encoding='utf-8')
    msg = f'Đã tạo `{out_file}` từ `{Path(root).resolve()}` (mode={mode}, ~{count_tokens(out):,} token). Bạn đưa file này cho chatbot khác là dùng được.'
    return {'messages': [AIMessage(msg)]}
