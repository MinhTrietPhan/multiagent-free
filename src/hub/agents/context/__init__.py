from ..base import AgentSpec
from .graph import build_context
from .node import context_node

SPEC = AgentSpec(
    name="context",
    description="Đọc một dự án/repo code và tạo file ngữ cảnh gọn (cây thư mục, skeleton, code chính) "
                "để đưa cho chatbot khác hiểu dự án nhanh. Không dùng LLM.",
    node=context_node,
)

__all__ = ["SPEC", "build_context"]
