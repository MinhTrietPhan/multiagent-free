"""QA agent node – answers questions about a project using PROJECT_CONTEXT*.md + Gemini.

Flow:
1. Pick the context file: a path given in the question > $HUB_CONTEXT_FILE > newest PROJECT_CONTEXT*.md in cwd > newest in subfolders.
2. Send context + the last few Q&A turns (memory) + the question to Gemini.
3. If Gemini says it needs more code (JSON {"need_file": ...}), fetch that file / line range / function
   from disk and ask again, at most MAX_EXTRA_READS times.
4. Return the final answer as an AIMessage (name='qa') with file:line citations and the context file actually used.
"""
import json
import os
import re
from pathlib import Path
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.types import interrupt
from ...model_router import get_llm
from .prompts import SYSTEM_PROMPT, CONTEXT_HEADER, EXTRA_CODE_HEADER, NEED_MORE_CODE_INSTRUCTION
from .tools import read_file_content, read_function, find_project_root_from_context
CONTEXT_FILE = 'PROJECT_CONTEXT.md'
QA_NAME = 'qa'
MAX_EXTRA_READS = int(os.getenv('HUB_QA_MAX_READS', '2'))
HISTORY_TURNS = int(os.getenv('HUB_QA_HISTORY_TURNS', '3'))
HISTORY_CHARS = 1500
_PATH_RE = re.compile(r'"([^"]+\.md)"|\'([^\']+\.md)\'|([A-Za-z]:[\\/][^"\'`\n]*?\.md)|(\S+\.md)', re.I)


def _content_to_text(content) -> str:
    """Chuyển LangChain message content thành văn bản thuần."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, str):
                texts.append(block)
            elif isinstance(block, dict) and block.get('type') == 'text':
                texts.append(block.get('text', ''))
        return '\n'.join((text for text in texts if text))
    return str(content)


def _last_user_text(state) -> str:
    for m in reversed(state.get('messages', [])):
        if getattr(m, 'type', '') == 'human':
            return m.content if isinstance(m.content, str) else str(m.content)
    return ''


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + ' …'


def _history(state, max_turns: int=HISTORY_TURNS) -> list:
    """Vài lượt hỏi–đáp QA gần nhất (không gồm câu hỏi hiện tại), bỏ dòng nguồn để đỡ tốn token."""
    msgs = list(state.get('messages', []))
    last = max((i for i, m in enumerate(msgs) if getattr(m, 'type', '') == 'human'), default=None)
    if last is None or max_turns <= 0:
        return []
    prev = msgs[:last]
    pairs = [(a, b) for a, b in zip(prev, prev[1:]) if getattr(a, 'type', '') == 'human' and getattr(b, 'type', '') == 'ai' and (getattr(b, 'name', None) == QA_NAME)]
    out: list = []
    for human, ai in pairs[-max_turns:]:
        q = _clip(_content_to_text(human.content), HISTORY_CHARS)
        a = _clip(_content_to_text(ai.content).split('\n\n---\n')[0], HISTORY_CHARS)
        out += [HumanMessage(content=f'Câu hỏi: {q}'), AIMessage(content=a)]
    return out


def _extract_md_path(text: str) -> tuple[str, str] | None:
    """Tìm đường dẫn file .md trong câu hỏi. Trả (chuỗi gốc trong câu, đường dẫn đã làm sạch).

    Chỉ coi là "chủ đích" khi là đường dẫn tuyệt đối / có dấu / \\ , hoặc file tồn tại (tránh nhầm 'README.md' nhắc cho vui).
    """
    for m in _PATH_RE.finditer(text):
        raw = next((g for g in m.groups() if g))
        cleaned = raw.strip().strip('.,;:)')
        p = Path(cleaned).expanduser()
        if p.is_absolute() or '/' in cleaned or '\\' in cleaned or p.is_file():
            return (raw, cleaned)
    return None


def _load_context_with_path() -> tuple[str, Path] | None:
    """$HUB_CONTEXT_FILE > PROJECT_CONTEXT*.md mới nhất ở thư mục hiện tại > mới nhất ở thư mục con."""
    env = os.getenv('HUB_CONTEXT_FILE')
    if env and Path(env).is_file():
        p = Path(env)
        return (p.read_text(encoding='utf-8', errors='ignore'), p)
    for pattern in ('PROJECT_CONTEXT*.md', '**/PROJECT_CONTEXT*.md'):
        files = sorted(Path('.').glob(pattern), key=lambda x: x.stat().st_mtime, reverse=True)
        if files:
            return (files[0].read_text(encoding='utf-8', errors='ignore'), files[0])
    return None


def _load_context() -> str | None:
    """Load the most recent PROJECT_CONTEXT*.md."""
    found = _load_context_with_path()
    return found[0] if found else None


def _parse_need_file(response_text: str) -> dict | None:
    """Check if the LLM response is a JSON request for more code."""
    text = response_text.strip()
    json_match = re.search('```json\\s*(\\{.*?\\})\\s*```', text, re.DOTALL)
    if json_match:
        text = json_match.group(1)
    elif not text.startswith('{'):
        return None
    try:
        data = json.loads(text)
        if 'need_file' in data:
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    return None


def _as_int(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def qa_node(state) -> dict:
    """Main QA node: reads context, asks Gemini, fetches extra code if needed."""
    question = _last_user_text(state)
    context_text, context_path = (None, None)
    mentioned = _extract_md_path(question)
    if mentioned:
        raw, cleaned = mentioned
        p = Path(cleaned).expanduser()
        if p.is_file():
            context_text, context_path = (p.read_text(encoding='utf-8', errors='ignore'), p)
            question = question.replace(raw, '(file context đã được nạp sẵn)').strip()
        elif p.is_absolute() or '/' in cleaned or '\\' in cleaned:
            hint = '\nNếu file nằm trên Desktop, thử `...\\OneDrive\\Desktop\\...`.' if 'desktop' in cleaned.lower() else ''
            return {'messages': [AIMessage(f'⚠️ Không tìm thấy file: `{cleaned}`\nKiểm tra lại đường dẫn (Python không thấy file này).{hint}')]}
    if context_text is None:
        found = _load_context_with_path()
        if found:
            context_text, context_path = found
    if context_text is None:
        return {'messages': [AIMessage('⚠️ Chưa có PROJECT_CONTEXT.md (hoặc PROJECT_CONTEXT_map/balanced/full.md). Hãy tạo context trước bằng lệnh:\n`/context <đường_dẫn>`\n\nSau đó hỏi lại câu hỏi của bạn.')]}
    project_root = find_project_root_from_context(context_text)
    llm = get_llm()
    system_msg = SystemMessage(content=SYSTEM_PROMPT)
    context_msg = HumanMessage(content=CONTEXT_HEADER.format(context=context_text) + '\n' + NEED_MORE_CODE_INSTRUCTION)
    question_msg = HumanMessage(content=f'Câu hỏi: {question}')
    messages = [system_msg, context_msg, *_history(state), question_msg]
    extra_labels: list[str] = []
    reads = 0
    while True:
        response = llm.invoke(messages)
        answer = _content_to_text(response.content)
        need = _parse_need_file(answer)
        if need is None or project_root is None:
            break
        if reads >= MAX_EXTRA_READS:
            messages.append(AIMessage(content=answer))
            messages.append(HumanMessage(content='Đã hết lượt đọc thêm code. Hãy trả lời ngay với thông tin hiện có, KHÔNG trả JSON.'))
            response = llm.invoke(messages)
            answer = _content_to_text(response.content)
            break
        reads += 1
        rel_path = need['need_file']
        func_name = need.get('function')
        start, end = (_as_int(need.get('start')), _as_int(need.get('end')))
        if func_name:
            extra = read_function(project_root, rel_path, func_name)
            label = f'{rel_path}::{func_name}'
        else:
            extra = read_file_content(project_root, rel_path, start=start or 1, end=end)
            label = rel_path + (f':L{start or 1}-{end}' if end else f':L{start}-' if start else '')
        if extra is None:
            messages.append(AIMessage(content=answer))
            messages.append(HumanMessage(content=f'File `{label}` không tìm thấy (hoặc nằm ngoài thư mục dự án). Hãy trả lời với thông tin hiện có.'))
            continue
        extra_labels.append(label)
        extra_block = EXTRA_CODE_HEADER.format(path=label, code=extra)
        messages.append(AIMessage(content=answer))
        messages.append(HumanMessage(content=extra_block + '\nBây giờ hãy trả lời câu hỏi gốc với thông tin bổ sung.'))
    if _parse_need_file(answer):
        answer = 'Không thể lấy thêm code để trả lời đầy đủ. Dựa trên context hiện có:\n\n' + answer
    sources = f'📄 Nguồn: `{context_path}`'
    if extra_labels:
        sources += f" + code bổ sung ({', '.join(extra_labels)})"
    final = f'{answer}\n\n---\n{sources}'
    return {'messages': [AIMessage(content=final, name=QA_NAME)]}
