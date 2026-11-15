from .controller.pipeline import run_research


def _last_user_text(state):
    for message in reversed(state.get("messages", [])):
        if getattr(message, "type", "") == "human":
            content = getattr(message, "content", "")
            return content if isinstance(content, str) else str(content)
    return ""


def research_node(state):
    # Import message class only when the full Hub runtime is invoked.
    from langchain_core.messages import AIMessage
    query = _last_user_text(state).strip()
    if not query:
        return {"messages": [AIMessage("Bạn cần nhập chủ đề muốn research.", name="research")]}
    try:
        report = run_research(query)
    except Exception as exc:
        report = f"Research thất bại: {type(exc).__name__}: {exc}"
    return {"messages": [AIMessage(content=report, name="research")]}
