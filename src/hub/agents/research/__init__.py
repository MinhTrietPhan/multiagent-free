from ..base import AgentSpec
from .node import research_node

SPEC = AgentSpec(
    name="research",
    description=(
        "Research Web Agent: LLM lập kế hoạch tìm kiếm, thu thập và trích nội dung từ "
        "nhiều website, lọc trùng, kiểm chứng chéo nguồn, xếp hạng và tóm tắt top tin sạch."
    ),
    node=research_node,
    parallel_safe=False,
)

__all__ = ["SPEC", "research_node"]
