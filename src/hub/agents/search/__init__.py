from ..base import AgentSpec
from .node import search_node

SPEC = AgentSpec(
    name="search",
    description="Read-only Project Search Agent: tìm kiếm symbol, hàm, chuỗi hoặc đoạn code trong source của dự án dựa trên PROJECT_CONTEXT*.md; không sửa file.",
    node=search_node,
    parallel_safe=True,
)
