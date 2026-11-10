from ..base import AgentSpec
from .node import qa_node

SPEC = AgentSpec(
    name="qa",
    description="Trả lời câu hỏi về code/dự án dựa trên PROJECT_CONTEXT.md (dùng Gemini). "
                "Hỏi về hàm, class, luồng xử lý, kiến trúc... của dự án đã tạo context.",
    node=qa_node,
)

__all__ = ["SPEC"]
