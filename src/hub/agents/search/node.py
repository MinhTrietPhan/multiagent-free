"""Deterministic, read-only project search subagent."""
from pathlib import Path
from langchain_core.messages import AIMessage
from ...agents.qa.tools import find_project_root_from_context
from ...agents.qa.node import _last_user_text, _load_context_with_path, _extract_md_path
from ...tools.builtin import register_builtin_tools
from ...tools.registry import get_tool_registry


def search_node(state):
    question = _last_user_text(state)
    mentioned = _extract_md_path(question)
    found = None
    if mentioned:
        path = Path(mentioned[1]).expanduser()
        if path.is_file():
            found = (path.read_text(encoding="utf-8", errors="ignore"), path)
    if found is None:
        found = _load_context_with_path()
    if not found:
        return {"messages": [AIMessage("Chưa tìm thấy PROJECT_CONTEXT*.md để xác định project root.", name="search")]}
    context, _ = found
    root = find_project_root_from_context(context)
    if not root:
        return {"messages": [AIMessage("Không xác định được project root từ file context.", name="search")]}
    register_builtin_tools()
    result = get_tool_registry().execute("search_project", project_root=root, query=question, max_results=30)
    return {"messages": [AIMessage(f"Project search trong `{Path(root).resolve()}`:\n\n{result}", name="search")]}

