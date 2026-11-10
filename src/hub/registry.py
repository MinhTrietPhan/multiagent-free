"""THE place to register agents. To add one: create hub/agents/<name>/ exposing SPEC, import it, append."""
from .agents.base import AgentSpec
from .agents.context import SPEC as context_spec
from .agents.qa import SPEC as qa_spec

AGENTS: list[AgentSpec] = [
    context_spec,
    qa_spec,
]
