"""THE place to register agents. To add one: create hub/agents/<name>/ exposing SPEC, import it, append."""
from .agents.base import AgentSpec
from .agents.context import SPEC as context_spec
from .agents.qa import SPEC as qa_spec
from .agents.search import SPEC as search_spec
from .agents.research import SPEC as research_spec
from .agents.code import SPEC as code_spec

AGENTS: list[AgentSpec] = [qa_spec, search_spec, context_spec, research_spec, code_spec]

