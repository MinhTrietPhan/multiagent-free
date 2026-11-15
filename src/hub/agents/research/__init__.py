from ..base import AgentSpec
from .node import research_node

SPEC = AgentSpec(
    name="research",
    description=(
        "Research Web Agent: tìm kiếm, thu thập, lọc trùng, kiểm chứng và xếp hạng "
        "thông tin từ nhiều website; dùng Laya làm decision model, không dùng Gemini trong research loop."
    ),
    node=research_node,
    parallel_safe=False,
)

__all__ = ["SPEC", "research_node"]
