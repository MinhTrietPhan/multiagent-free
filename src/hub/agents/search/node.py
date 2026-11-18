"""Deterministic, read-only project search subagent."""
from langchain_core.messages import AIMessage
from ..qa.project import resolve_project_root
from ...tools.builtin import register_builtin_tools
from ...tools.registry import get_tool_registry


def _last_user_text(state):
    for message in reversed(state.get("messages", [])):
        if getattr(message, "type", "") == "human":
            content = getattr(message, "content", "")
            return content if isinstance(content, str) else str(content)
    return ""


def search_node(state):
    question = _last_user_text(state)
    try:
        root = resolve_project_root(state, question, allow_interrupt=not state.get("_parallel_execution", False))
    except InterruptedError:
        return {"messages": [AIMessage("Đã huỷ chọn dự án.", name="search")]}
    except FileNotFoundError as exc:
        return {"messages": [AIMessage(f"⚠️ {exc}", name="search")]}

    register_builtin_tools()
    result = get_tool_registry().execute(
        "search_project", project_root=str(root), query=question, max_results=30
    )
    return {
        "messages": [AIMessage(f"Project search trong `{root}`:\n\n{result}", name="search")],
        "active_project_root": str(root),
    }

