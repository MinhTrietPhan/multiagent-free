"""QA agent node – answers questions about a project using PROJECT_CONTEXT.md + Gemini.

Flow:
1. Load the latest PROJECT_CONTEXT.md (written by the context agent).
2. Send the question + context to Gemini.
3. If Gemini says it needs more code (JSON {"need_file": ...}), fetch that file/function
   from disk and retry ONCE with the extra code appended.
4. Return the final answer as an AIMessage with file:line citations.
"""
import json
import re
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.types import interrupt

from ...llm import get_llm
from .prompts import SYSTEM_PROMPT, CONTEXT_HEADER, EXTRA_CODE_HEADER, NEED_MORE_CODE_INSTRUCTION
from .tools import read_file_content, read_function, find_project_root_from_context

# Where the context agent writes its output
CONTEXT_FILE = "PROJECT_CONTEXT.md"
MAX_RETRIES = 2  # at most fetch 2 extra files before giving up


def _last_user_text(state) -> str:
    for m in reversed(state.get("messages", [])):
        if getattr(m, "type", "") == "human":
            return m.content if isinstance(m.content, str) else str(m.content)
    return ""


def _load_context() -> str | None:
    """Load the most recent PROJECT_CONTEXT.md."""
    p = Path(CONTEXT_FILE)
    if p.is_file():
        return p.read_text(encoding="utf-8", errors="ignore")
    # Also check outputs/ in case there are multiple
    for f in sorted(Path(".").glob("**/PROJECT_CONTEXT*.md"), key=lambda x: x.stat().st_mtime, reverse=True):
        return f.read_text(encoding="utf-8", errors="ignore")
    return None


def _parse_need_file(response_text: str) -> dict | None:
    """Check if the LLM response is a JSON request for more code."""
    text = response_text.strip()
    # Try to extract JSON from the response
    # Could be raw JSON or wrapped in ```json ... ```
    json_match = re.search(r'```json\s*(\{.*?\})\s*```', text, re.DOTALL)
    if json_match:
        text = json_match.group(1)
    elif not text.startswith("{"):
        return None

    try:
        data = json.loads(text)
        if "need_file" in data:
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    return None


def qa_node(state) -> dict:
    """Main QA node: reads context, asks Gemini, fetches extra code if needed."""
    question = _last_user_text(state)

    # --- Step 1: Load context ---
    context_text = _load_context()
    if context_text is None:
        return {"messages": [AIMessage(
            "⚠️ Chưa có PROJECT_CONTEXT.md. Hãy tạo context trước bằng lệnh:\n"
            "`tạo context cho dự án <đường_dẫn>`\n\n"
            "Sau đó hỏi lại câu hỏi của bạn."
        )]}

    # --- Step 2: Find project root for fetching extra code ---
    project_root = find_project_root_from_context(context_text)

    # --- Step 3: Build messages and call Gemini ---
    llm = get_llm()

    system_msg = SystemMessage(content=SYSTEM_PROMPT)
    context_msg = HumanMessage(content=(
        CONTEXT_HEADER.format(context=context_text) + "\n" +
        NEED_MORE_CODE_INSTRUCTION
    ))
    question_msg = HumanMessage(content=f"Câu hỏi: {question}")

    messages = [system_msg, context_msg, question_msg]
    extra_context_parts: list[str] = []

    for attempt in range(MAX_RETRIES + 1):
        response = llm.invoke(messages)
        answer = response.content if isinstance(response.content, str) else str(response.content)

        # Check if Gemini needs more code
        need = _parse_need_file(answer)
        if need is None or project_root is None:
            break  # Got a real answer (or can't fetch more code)

        rel_path = need["need_file"]
        func_name = need.get("function")

        if func_name:
            extra = read_function(project_root, rel_path, func_name)
            label = f"{rel_path}::{func_name}"
        else:
            extra = read_file_content(project_root, rel_path)
            label = rel_path

        if extra is None:
            # File not found, tell Gemini and let it answer with what it has
            messages.append(AIMessage(content=answer))
            messages.append(HumanMessage(content=f"File `{label}` không tìm thấy. Hãy trả lời với thông tin hiện có."))
            continue

        extra_block = EXTRA_CODE_HEADER.format(path=label, code=extra)
        extra_context_parts.append(extra_block)
        messages.append(AIMessage(content=answer))
        messages.append(HumanMessage(content=extra_block + "\nBây giờ hãy trả lời câu hỏi gốc với thông tin bổ sung."))

    # --- Step 4: Format the answer ---
    # If the last response was a need_file request, something went wrong
    if _parse_need_file(answer):
        answer = ("Không thể lấy thêm code để trả lời đầy đủ. "
                  "Dựa trên context hiện có:\n\n" + answer)

    # Add a note about which context was used
    sources = "📄 Nguồn: `PROJECT_CONTEXT.md`"
    if extra_context_parts:
        sources += " + code bổ sung"

    final = f"{answer}\n\n---\n{sources}"
    return {"messages": [AIMessage(content=final)]}
