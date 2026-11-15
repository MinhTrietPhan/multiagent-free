
"""Hub-facing wrapper of the context agent: parse the request, ask the fixed menu if needed, run, reply."""

import re
from pathlib import Path

from langchain_core.messages import AIMessage
from langgraph.types import interrupt

from .config import output_filename
from .graph import build_context
from .tools.tokens import count_tokens


MENU = (
    "Bạn muốn mức ngữ cảnh nào?\n"
    "  1. map       Bản đồ nhanh (cây thư mục + skeleton)\n"
    "  2. balanced  Cân bằng (bản đồ + full code vài file chính)\n"
    "  3. full      Đầy đủ (toàn bộ code, đã bỏ comment/output notebook)\n"
    "Nhập 1/2/3 (mặc định 2):"
)

_MODE_KEYWORDS = {
    "map": ("map", "tổng quan", "bản đồ", "skeleton"),
    "full": ("full", "đầy đủ", "toàn bộ"),
    "balanced": ("balanced", "cân bằng"),
}

CANCEL = {"0", "q", "exit", "huỷ", "huy", "cancel"}


def _last_user_text(state) -> str:
    for message in reversed(state.get("messages", [])):
        if getattr(message, "type", "") == "human":
            return (
                message.content
                if isinstance(message.content, str)
                else str(message.content)
            )
    return ""


def _find_dir(text: str) -> str | None:
    for token in re.findall(r'[\w./~\\:-]+', text):
        cleaned = token.rstrip(",:;")
        path = Path(cleaned).expanduser()

        if cleaned and path.is_dir():
            return str(path.resolve())

    return None


def _find_mode(text: str) -> str | None:
    text_lower = text.lower()

    for mode, keywords in _MODE_KEYWORDS.items():
        if any(
            re.search(rf"\b{re.escape(keyword)}\b", text_lower)
            for keyword in keywords
        ):
            return mode

    return None


def context_node(state) -> dict:
    text = _last_user_text(state)

    # 1. Xác định thư mục dự án
    root = _find_dir(text)

    if root is None:
        answer = str(
            interrupt(
                "Dự án nằm ở đâu? Nhập đường dẫn thư mục "
                "(gõ . cho thư mục hiện tại, 0 để huỷ):"
            )
        ).strip().strip('"')

        if answer.lower() in CANCEL:
            return {
                "messages": [
                    AIMessage("Đã huỷ, không tạo file.")
                ]
            }

        project_path = Path(answer).expanduser()

        if not project_path.is_dir():
            return {
                "messages": [
                    AIMessage(f"Không tìm thấy thư mục: {answer}")
                ]
            }

        root = str(project_path.resolve())

    project_root = Path(root).resolve()

    # 2. Xác định chế độ tạo context
    mode = _find_mode(text)

    if mode is None:
        choice = str(interrupt(MENU)).strip().lower()

        if choice in CANCEL:
            return {
                "messages": [
                    AIMessage("Đã huỷ, không tạo file.")
                ]
            }

        mode = {
            "1": "map",
            "2": "balanced",
            "3": "full",
        }.get(
            choice,
            choice if choice in _MODE_KEYWORDS else "balanced"
        )

    # 3. Tạo nội dung context
    out = build_context(str(project_root), mode)

    # 4. Luôn lưu file trong thư mục gốc của dự án
    out_file = project_root / output_filename(mode)

    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(out, encoding="utf-8")

    # 5. Thông báo kết quả và đường dẫn đầy đủ
    msg = (
        "Đã tạo PROJECT_CONTEXT thành công.\n\n"
        f"- Dự án: `{project_root}`\n"
        f"- Chế độ: `{mode}`\n"
        f"- File: `{out_file.name}`\n"
        f"- Đường dẫn đầy đủ: `{out_file}`\n"
        f"- Dung lượng nội dung: khoảng {count_tokens(out):,} tokens"
    )

    return {
        "messages": [AIMessage(msg)]
    }
