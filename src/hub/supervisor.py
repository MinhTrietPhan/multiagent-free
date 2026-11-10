"""Supervisor: picks which agent handles the latest user message.

Free-tier friendly: with a single registered agent it makes NO LLM call; with several it makes one
small structured-output call per user message (only the last message is sent, never the whole history).
If the user picked an agent explicitly (`/qa ...`, `/context ...`), `state['forced']` is set and NO LLM call is made.
"""
from pydantic import BaseModel, Field
from .agents.base import AgentSpec
from .model_router import get_llm, TRACKER
PROMPT = 'Bạn là bộ điều phối. Chọn đúng MỘT agent phù hợp nhất để xử lý yêu cầu của người dùng.\n\nCác agent:\n{catalog}\n\nYêu cầu của người dùng:\n{request}\n\nTrả về tên agent. Nếu không agent nào phù hợp, chọn "{default}".'

class Route(BaseModel):
    agent: str = Field(description='Tên agent được chọn')

def _last_user_text(state) -> str:
    for m in reversed(state.get('messages', [])):
        if getattr(m, 'type', '') == 'human':
            return m.content if isinstance(m.content, str) else str(m.content)
    return ''

def make_supervisor(agents: list[AgentSpec], llm_factory=get_llm):
    names = [a.name for a in agents]
    catalog = '\n'.join((f'- {a.name}: {a.description}' for a in agents))

    def supervisor(state) -> dict:
        forced = state.get('forced')
        if forced in names:
            return {'next': forced}
        if len(agents) == 1:
            return {'next': names[0]}
        prompt = PROMPT.format(catalog=catalog, request=_last_user_text(state), default=names[0])
        route = llm_factory().with_structured_output(Route).invoke(prompt)
        TRACKER.mark_routing()
        return {'next': route.agent if route.agent in names else names[0]}
    return supervisor
