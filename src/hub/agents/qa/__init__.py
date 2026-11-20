from ..base import AgentSpec
from .node import qa_node
SPEC = AgentSpec(name='qa', description='Project QA Agent: trả lời câu hỏi và giải thích source code của dự án đã chọn. Hệ thống dựng chỉ mục dự án (cây thư mục + entry points) tại chỗ, không cần file PROJECT_CONTEXT. Sử dụng Gemini để phân tích kiến trúc, class, function, module, luồng xử lý và mối quan hệ giữa các thành phần. Khi chỉ mục chưa đủ thông tin, Agent tự đọc thêm file source code hoặc function cụ thể để trả lời chính xác. Sử dụng khi người dùng đặt câu hỏi, muốn giải thích, phân tích hoặc tìm hiểu code trong một dự án đã chọn.', node=qa_node, parallel_safe=True)
__all__ = ['SPEC']
