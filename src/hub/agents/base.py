"""Agent contract.

An agent = AgentSpec(name, description, node)
  - name:        unique id, used as the graph node name
  - description: ONE clear sentence; the supervisor reads it to decide when to route here
  - node:        (HubState) -> dict, normally {"messages": [AIMessage(...)]}

Inside, an agent can be anything: plain code (like `context`), a LangChain agent, or its own LangGraph
subgraph. Use `langgraph.types.interrupt(...)` in a node when it needs to ask the user something.
"""
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class AgentSpec:
    name: str
    description: str
    node: Callable[[dict], dict]
