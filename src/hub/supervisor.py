"""Supervisor planning: route to one agent or delegate independent work to safe subagents."""
from pydantic import BaseModel, Field
from .agents.base import AgentSpec
from .model_router import get_llm, TRACKER

PROMPT = """Bạn là bộ điều phối của Agent Hub.
Chọn agent phù hợp nhất. Chỉ giao nhiều agent khi yêu cầu thực sự có các phần việc độc lập;
chỉ chọn nhiều agent trong danh sách parallel-safe. Không chọn context cùng QA cho một yêu cầu thông thường.

Danh sách agent:
{catalog}

Yêu cầu của người dùng:
{request}

Trả về agent chính và có thể thêm delegate_agents (ít nhất 2 agent parallel-safe) nếu cần phối hợp.
Nếu không cần phối hợp, để delegate_agents là danh sách rỗng. Mặc định: {default}.
"""

class Route(BaseModel):
    agent: str = Field(description="Agent chính")
    delegate_agents: list[str] = Field(default_factory=list, description="Các agent độc lập có thể chạy song song")


def _last_user_text(state) -> str:
    for m in reversed(state.get("messages", [])):
        if getattr(m, "type", "") == "human":
            return m.content if isinstance(m.content, str) else str(m.content)
    return ""


def make_supervisor(agents: list[AgentSpec], llm_factory=get_llm):
    names = [a.name for a in agents]
    safe_names = {a.name for a in agents if a.parallel_safe}
    catalog = "\n".join(f"- {a.name} (parallel-safe={a.parallel_safe}): {a.description}" for a in agents)

    def supervisor(state) -> dict:
        forced = state.get("forced")
        if forced in names:
            return {"next": forced, "delegated": []}
        request = _last_user_text(state).strip().lower()
        # Deterministic shortcuts for explicit utility requests avoid spending a routing call.
        if "context" in names and request.startswith(("context ", "tạo context", "tạo project context", "generate context")):
            return {"next": "context", "delegated": []}
        if "search" in names and request.startswith(("search ", "tìm trong source", "tìm kiếm trong code", "grep ")):
            return {"next": "search", "delegated": []}
        if len(agents) == 1:
            return {"next": names[0], "delegated": []}
        prompt = PROMPT.format(catalog=catalog, request=request, default=names[0])
        try:
            router = llm_factory("supervisor")
        except TypeError:  # backward compatibility for injected test/custom factories
            router = llm_factory()
        route = router.with_structured_output(Route).invoke(prompt)
        TRACKER.mark_routing()
        selected = list(dict.fromkeys(n for n in getattr(route, "delegate_agents", []) if n in safe_names))
        if len(selected) >= 2:
            return {"next": "orchestrator", "delegated": selected}
        return {"next": route.agent if route.agent in names else names[0], "delegated": []}
    return supervisor
