"""Builds the hub graph from the registry:  START -> supervisor -> <chosen agent> -> END.
(To let agents hand off to each other later, add edges agent -> supervisor and a FINISH route.)"""
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from .agents.base import AgentSpec
from .model_router import get_llm
from .registry import AGENTS
from .state import HubState
from .supervisor import make_supervisor


def build_hub(agents: list[AgentSpec] | None = None, llm_factory=get_llm, checkpointer=None):
    agents = agents or AGENTS
    g = StateGraph(HubState)
    g.add_node("supervisor", make_supervisor(agents, llm_factory))
    g.add_edge(START, "supervisor")
    for a in agents:
        g.add_node(a.name, a.node)
        g.add_edge(a.name, END)
    g.add_conditional_edges("supervisor", lambda s: s["next"], {a.name: a.name for a in agents})
    # checkpointer is required for interrupt() (agents asking the user follow-up questions)
    return g.compile(checkpointer=checkpointer or MemorySaver())
