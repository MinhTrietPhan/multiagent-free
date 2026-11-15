"""Top-level graph: START -> supervisor -> agent/orchestrator -> END."""
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from .agents.base import AgentSpec
from .model_router import get_llm
from .registry import AGENTS
from .state import HubState
from .supervisor import make_supervisor
from .orchestrator import make_orchestrator


def build_hub(agents: list[AgentSpec] | None = None, llm_factory=get_llm, checkpointer=None):
    agents = agents or AGENTS
    g = StateGraph(HubState)
    g.add_node("supervisor", make_supervisor(agents, llm_factory))
    g.add_node("orchestrator", make_orchestrator(agents))
    g.add_edge(START, "supervisor")
    for agent in agents:
        g.add_node(agent.name, agent.node)
        g.add_edge(agent.name, END)
    g.add_edge("orchestrator", END)
    routes = {a.name: a.name for a in agents}
    routes["orchestrator"] = "orchestrator"
    g.add_conditional_edges("supervisor", lambda s: s["next"], routes)
    return g.compile(checkpointer=checkpointer or MemorySaver())
