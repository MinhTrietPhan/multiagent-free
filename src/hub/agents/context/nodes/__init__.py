"""Context-pipeline nodes. To add a node: write `nodes/my_node.py` with `my_node(state) -> dict`,
import it here, and put it in PIPELINE where it should run. Nothing else changes."""
from .base import AgentNode
from .discovery import discovery_node
from .parser import parser_node
from .ranker import ranker_node
from .renderer import renderer_node

PIPELINE: list[tuple[str, AgentNode]] = [
    ("discovery", discovery_node),
    ("parser", parser_node),
    ("ranker", ranker_node),
    # ("summarizer", summarizer_node),   # future: optional LLM agent (langchain), cached by file hash
    ("renderer", renderer_node),
]
