"""Builds the LangGraph from the agent registry. v1 is a linear chain; for branching/conditional
edges later, replace the loop below with explicit add_edge / add_conditional_edges calls."""
from langgraph.graph import END, START, StateGraph

from .nodes import PIPELINE
from .config import DEFAULT_MODE, MODES
from .state import State


def build_graph():
    g = StateGraph(State)
    prev = START
    for name, node in PIPELINE:
        g.add_node(name, node)
        g.add_edge(prev, name)
        prev = name
    g.add_edge(prev, END)
    return g.compile()


def build_context(root: str, mode: str = DEFAULT_MODE, budget: int | None = ..., pin=(), exclude=()) -> str:
    """Single entry point: used by the CLI now, and can be wrapped as a tool/MCP endpoint later."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {list(MODES)}")
    if budget is ...:
        budget = MODES[mode]
    result = build_graph().invoke(
        {"root": root, "mode": mode, "budget": budget, "pin": list(pin), "exclude": list(exclude)}
    )
    return result["output"]
