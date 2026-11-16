"""Agent registration: the only place that builds the subgraph and the AgentSpec."""
from ..base import AgentSpec
from .graph import build_code_graph

SPEC = AgentSpec(
    name="code",
    description=("Code Agent: CHỈ dùng khi người dùng yêu cầu rõ ràng SỬA hoặc TẠO source code trong một dự án "
                 "(thêm class/hàm, sửa lỗi, refactor, viết test). Đọc code liên quan, đề xuất patch, xin phê duyệt "
                 "rồi mới ghi file và chạy kiểm tra. Câu hỏi giải thích code KHÔNG thuộc agent này (dùng qa)."),
    node=build_code_graph(),
    parallel_safe=False,  # writes files and waits for human approval
)
