from ..base import AgentSpec
from .node import search_node

SPEC = AgentSpec(
    name="search",
    description="Read-only Project Search Agent: tìm kiếm symbol, hàm, chuỗi hoặc đoạn code trong source của dự án trong project đã chọn, không phụ thuộc PROJECT_CONTEXT; không sửa file.",
    node=search_node,
    parallel_safe=True,
)
