"""Supervisor: picks which agent handles the latest user message.

Free-tier friendly: with a single registered agent it makes NO LLM call; with several it makes one
small structured-output call per user message (only the last message is sent, never the whole history).
"""
from pydantic import BaseModel, Field

from .agents.base import AgentSpec
from .llm import get_llm

PROMPT = """Bạn là bộ điều phối. Chọn đúng MỘT agent phù hợp nhất để xử lý yêu cầu của người dùng.

Các agent:
{catalog}

Yêu cầu của người dùng:
{request}

Trả về tên agent. Nếu không agent nào phù hợp, chọn "{default}"."""


class Route(BaseModel):
    agent: str = Field(description="Tên agent được chọn")


def _last_user_text(state) -> str:
    for m in reversed(state.get("messages", [])):
        if getattr(m, "type", "") == "human":
            return m.content if isinstance(m.content, str) else str(m.content)
    return ""


def make_supervisor(agents: list[AgentSpec], llm_factory=get_llm):
    names = [a.name for a in agents]
    catalog = "\n".join(f"- {a.name}: {a.description}" for a in agents)

    def supervisor(state) -> dict:
        if len(agents) == 1:
            return {"next": names[0]}
        prompt = PROMPT.format(catalog=catalog, request=_last_user_text(state), default=names[0])
        route = llm_factory().with_structured_output(Route).invoke(prompt)
        return {"next": route.agent if route.agent in names else names[0]}

    return supervisor
