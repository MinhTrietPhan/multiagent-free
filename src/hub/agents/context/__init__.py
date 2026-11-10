from ..base import AgentSpec
from .graph import build_context
from .node import context_node

SPEC = AgentSpec(name='context', description='Project Context Agent: CHỈ dùng khi người dùng yêu cầu TẠO, XÂY DỰNG LẠI hoặc CẬP NHẬT file PROJECT_CONTEXT.md cho một dự án (có đưa đường dẫn hoặc nói rõ "tạo context"). Quét thư mục và ghi file, không dùng LLM, không trả lời câu hỏi. Mọi câu hỏi giải thích, hỏi cách hoạt động của code hoặc của agent đều KHÔNG thuộc agent này.', node=context_node)

__all__ = ["SPEC", "build_context"]
