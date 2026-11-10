from ..base import AgentSpec
from .node import qa_node
SPEC = AgentSpec(name='qa', description='Project QA Agent: Chuyên trả lời câu hỏi và giải thích source code của dự án dựa trên file PROJECT_CONTEXT.md. Sử dụng Gemini để phân tích kiến trúc, class, function, module, luồng xử lý và mối quan hệ giữa các thành phần. Khi context chưa đủ thông tin, Agent có thể đọc thêm file source code hoặc function cụ thể để tìm câu trả lời chính xác hơn. Sử dụng khi người dùng đặt câu hỏi, muốn giải thích, phân tích hoặc tìm hiểu code trong một dự án đã có context.', node=qa_node)
__all__ = ['SPEC']
