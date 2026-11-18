
"""Run the existing Agent Hub graph through a private Telegram bot.

The bot uses long polling: it runs on the same machine as Agent Hub and does
not expose an inbound HTTP endpoint. Access is restricted by Telegram user ID.
"""
from __future__ import annotations

import asyncio
import logging
import html
import re
import os
import threading
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command

from ...graph import build_hub
from ...memory import MemoryStore
from ...model_router import TRACKER
from ...registry import AGENTS
from ...tools.builtin import register_builtin_tools
from ...tools.mcp import load_mcp_tools

LOGGER = logging.getLogger("agent_hub.telegram")
TELEGRAM_MAX_LENGTH = 3900


def parse_agent_message(text: str, agent_names: set[str]) -> tuple[str | None, str]:
    """Parse a direct agent prefix such as '/qa explain this'."""
    value = text.strip()
    if not value.startswith("/"):
        return None, value
    head, _, body = value[1:].partition(" ")
    name = head.split("@", 1)[0].lower()
    if name in agent_names:
        return name, body.strip()
    return None, value


def split_telegram_text(text: str, limit: int = TELEGRAM_MAX_LENGTH) -> list[str]:
    """Split long responses into Telegram-safe messages without dropping text."""
    value = str(text or "").strip()
    if not value:
        return ["Agent Hub không trả về nội dung."]
    chunks: list[str] = []
    while len(value) > limit:
        cut = value.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = value.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(value[:cut].rstrip())
        value = value[cut:].lstrip()
    if value:
        chunks.append(value)
    return chunks


def clean_agent_output(text: str) -> str:
    """Remove leaked internal file-request control payloads, not ordinary JSON answers."""
    value = str(text or "").strip()
    if re.fullmatch(r'\{\s*"need_file"\s*:\s*"[^"\n]+"\s*\}', value):
        return "Agent Hub cần đọc thêm file nhưng không hoàn tất được yêu cầu đọc file. Hãy thử hỏi lại hoặc chỉ rõ đường dẫn file."
    value = re.sub(r'(?m)^\s*\{\s*"need_file"\s*:\s*"[^"\n]+"\s*\}\s*$', '', value)
    return value.strip() or "Agent Hub không trả về nội dung."


def _inline_markdown_to_html(text: str) -> str:
    """Convert a safe subset of Markdown to Telegram HTML."""
    escaped = html.escape(text, quote=False)
    escaped = re.sub(r'`([^`]+)`', r'<code>\1</code>', escaped)
    escaped = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', escaped)
    escaped = re.sub(r'(?<!\*)\*([^*\n]+)\*(?!\*)', r'<i>\1</i>', escaped)
    escaped = re.sub(r'\[(.{1,200}?)\]\((https?://[^\s)]+)\)', r'<a href="\2">\1</a>', escaped)
    return escaped


def render_telegram_html(text: str) -> str:
    """Render headings, lists, tables and fenced code as Telegram-supported HTML."""
    value = clean_agent_output(text).replace("\r\n", "\n")
    lines = value.split("\n")
    rendered: list[str] = []
    i = 0
    in_list = False
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()
        if not stripped:
            if in_list:
                in_list = False
            rendered.append("")
            i += 1
            continue
        if stripped.startswith("```"):
            code: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            rendered.append("<pre><code>" + html.escape("\n".join(code), quote=False) + "</code></pre>")
            i += 1 if i < len(lines) else 0
            continue
        if stripped.startswith("|") and "|" in stripped:
            rows: list[list[str]] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{3,}:?", c or "-") for c in cells):
                    rows.append(cells)
                i += 1
            if rows:
                for row_index, cells in enumerate(rows):
                    label = " · ".join(c for c in cells if c)
                    tag = "<b>" + _inline_markdown_to_html(label) + "</b>" if row_index == 0 else _inline_markdown_to_html(label)
                    rendered.append("• " + tag)
            continue
        heading = re.match(r"^#{1,6}\s+(.*)$", stripped)
        if heading:
            rendered.append("<b>" + _inline_markdown_to_html(heading.group(1)) + "</b>")
            i += 1
            continue
        bullet = re.match(r"^(?:[-*•])\s+(.*)$", stripped)
        numbered = re.match(r"^(\d+)[.)]\s+(.*)$", stripped)
        if bullet:
            rendered.append("• " + _inline_markdown_to_html(bullet.group(1)))
        elif numbered:
            rendered.append(html.escape(numbered.group(1) + ". ") + _inline_markdown_to_html(numbered.group(2)))
        else:
            rendered.append(_inline_markdown_to_html(raw))
        i += 1
    return "\n".join(rendered).strip()


class TelegramSessionStore:
    """Persist Telegram chat -> LangGraph thread mapping across bot restarts."""

    def __init__(self, directory: Path):
        import sqlite3

        self.path = directory / "telegram_sessions.sqlite3"
        self._sqlite3 = sqlite3
        with closing(self._connect()) as db:
            with db:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS sessions ("
                    "chat_key TEXT PRIMARY KEY, thread_id TEXT NOT NULL)"
                )

    def _connect(self):
        return self._sqlite3.connect(self.path, timeout=15)

    def get_thread(self, chat_key: str) -> str:
        with closing(self._connect()) as db:
            with db:
                row = db.execute(
                    "SELECT thread_id FROM sessions WHERE chat_key=?", (chat_key,)
                ).fetchone()
                if row:
                    return str(row[0])
                thread_id = f"telegram-{uuid.uuid4().hex}"
                db.execute(
                    "INSERT INTO sessions(chat_key, thread_id) VALUES (?, ?)",
                    (chat_key, thread_id),
                )
                return thread_id

    def reset_thread(self, chat_key: str) -> str:
        thread_id = f"telegram-{uuid.uuid4().hex}"
        with closing(self._connect()) as db:
            with db:
                db.execute(
                    "INSERT INTO sessions(chat_key, thread_id) VALUES (?, ?) "
                    "ON CONFLICT(chat_key) DO UPDATE SET thread_id=excluded.thread_id",
                    (chat_key, thread_id),
                )
        return thread_id


class TelegramHub:
    """Adapter that reuses the project's graph, agents, and SQLite history."""

    def __init__(self, app: Any, store: MemoryStore, sessions: TelegramSessionStore):
        self.app = app
        self.store = store
        self.sessions = sessions
        self.agent_names = {agent.name for agent in AGENTS}
        self._initialized_threads: set[str] = set()
        self._pending_questions: dict[str, str] = {}
        # One graph invocation at a time avoids overlapping checkpoint writes.
        self._invoke_lock = threading.Lock()

    @staticmethod
    def _chat_key(user_id: int, chat_id: int) -> str:
        return f"{user_id}:{chat_id}"

    def reset(self, user_id: int, chat_id: int) -> None:
        key = self._chat_key(user_id, chat_id)
        thread_id = self.sessions.reset_thread(key)
        self._initialized_threads.discard(thread_id)
        self._pending_questions.pop(key, None)

    def _restore_messages(self, thread_id: str):
        messages = []
        for item in self.store.load_history(thread_id):
            if item["role"] == "user":
                messages.append(HumanMessage(content=item["content"]))
            else:
                messages.append(
                    AIMessage(content=item["content"], name=item.get("agent"))
                )
        return messages

    def run_message(self, user_id: int, chat_id: int, text: str) -> str:
        key = self._chat_key(user_id, chat_id)
        thread_id = self.sessions.get_thread(key)
        config = {"configurable": {"thread_id": thread_id}}
        forced, body = parse_agent_message(text, self.agent_names)
        if text.strip().startswith("/") and forced is None:
            return "Không nhận diện được agent. Dùng /help để xem lệnh hỗ trợ."
        if forced and not body:
            return f"Thêm nội dung sau /{forced}. Ví dụ: /{forced} ..."

        with self._invoke_lock:
            TRACKER.begin_turn()
            try:
                original_question = self._pending_questions.get(key, body)
                if key in self._pending_questions:
                    # The next Telegram message is the answer to an agent interrupt.
                    result = self.app.invoke(Command(resume=body), config)
                elif thread_id not in self._initialized_threads:
                    # MemorySaver is process-local, so restore durable SQLite history
                    # once when this bot process first sees a Telegram thread.
                    history = self._restore_messages(thread_id)
                    result = self.app.invoke(
                        {"messages": history + [HumanMessage(content=body)], "forced": forced},
                        config,
                    )
                    self._initialized_threads.add(thread_id)
                else:
                    result = self.app.invoke(
                        {"messages": [HumanMessage(content=body)], "forced": forced},
                        config,
                    )

                while "__interrupt__" in result:
                    prompt = str(result["__interrupt__"][0].value)
                    self._pending_questions[key] = original_question
                    return f"{prompt}\n\nTrả lời tin nhắn này để tiếp tục."

                self._pending_questions.pop(key, None)
                reply = clean_agent_output(str(result["messages"][-1].content))
                agent = result.get("next") or forced
                self.store.save_turn(thread_id, original_question, reply, agent)
                return reply
            except Exception as exc:
                LOGGER.exception("Agent Hub Telegram turn failed")
                return f"Agent Hub gặp lỗi: {type(exc).__name__}: {exc}"


def _allowed_user_id() -> int:
    raw = os.getenv("HUB_TELEGRAM_ALLOWED_USER_ID", "").strip()
    if not raw:
        raise RuntimeError(
            "Thiếu HUB_TELEGRAM_ALLOWED_USER_ID trong .env. "
            "Bot không được khởi chạy nếu chưa giới hạn người dùng."
        )
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError("HUB_TELEGRAM_ALLOWED_USER_ID phải là số nguyên.") from exc


async def _send_text(message, text: str) -> None:
    # Split before formatting so Telegram never receives a partial HTML tag.
    for chunk in split_telegram_text(clean_agent_output(text)):
        rendered = render_telegram_html(chunk)
        try:
            await message.reply_text(
                rendered, parse_mode="HTML", disable_web_page_preview=True
            )
        except Exception as exc:
            # A malformed/unsupported entity must not make the whole reply disappear.
            if exc.__class__.__name__ not in {"BadRequest", "TelegramError"}:
                raise
            LOGGER.warning("Telegram HTML rendering failed; sending plain text: %s", exc)
            await message.reply_text(chunk, disable_web_page_preview=True)


def main() -> None:
    """Console entry point: `hub-telegram`."""
    load_dotenv()
    token = os.getenv("HUB_TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("Thiếu HUB_TELEGRAM_BOT_TOKEN trong file .env.")
    allowed_user_id = _allowed_user_id()

    try:
        from telegram import Update
        from telegram.ext import (
            Application,
            CommandHandler,
            ContextTypes,
            MessageHandler,
            filters,
        )
    except ImportError as exc:
        raise SystemExit(
            'Chưa cài Telegram dependency. Chạy: pip install -e ".[telegram]"'
        ) from exc

    logging.basicConfig(
        level=os.getenv("HUB_TELEGRAM_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Match CLI startup: register built-in tools and optional MCP tools once.
    tool_registry = register_builtin_tools()
    if os.getenv("HUB_MCP_SERVERS", "").strip():
        try:
            asyncio.run(load_mcp_tools(tool_registry))
        except Exception:
            LOGGER.exception("MCP startup warning; bot will continue without MCP tools")

    store = MemoryStore()
    sessions = TelegramSessionStore(store.directory)
    hub = TelegramHub(build_hub(), store, sessions)
    agent_names = sorted(hub.agent_names)

    async def authorized(update: Update) -> bool:
        user = update.effective_user
        if user is None or user.id != allowed_user_id:
            LOGGER.warning("Rejected Telegram update from user_id=%s", getattr(user, "id", None))
            return False
        return True

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await authorized(update) or update.effective_message is None:
            return
        await _send_text(
            update.effective_message,
            "Agent Hub đã kết nối. Gửi câu hỏi bình thường để Supervisor định tuyến.\n"
            "Lệnh: /qa, /research, /search, /context, /agents, /reset, /help",
        )

    async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await authorized(update) or update.effective_message is None:
            return
        lines = ["Agent Hub Telegram", "Gửi câu hỏi bình thường để Supervisor tự chọn agent.", ""]
        lines.extend(f"/{name} <nội dung>" for name in agent_names)
        lines.extend(["/agents — danh sách agent", "/reset — tạo thread mới", "/help — trợ giúp"])
        await _send_text(update.effective_message, "\n".join(lines))

    async def agents_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await authorized(update) or update.effective_message is None:
            return
        descriptions = [f"/{a.name} — {a.description}" for a in AGENTS]
        await _send_text(update.effective_message, "\n".join(descriptions))

    async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await authorized(update) or update.effective_message is None:
            return
        user, chat = update.effective_user, update.effective_chat
        hub.reset(user.id, chat.id)
        await _send_text(update.effective_message, "Đã tạo thread Telegram mới. Lịch sử thread cũ vẫn được giữ.")

    async def agent_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await authorized(update) or update.effective_message is None:
            return
        text = update.effective_message.text or ""
        user, chat = update.effective_user, update.effective_chat
        await update.effective_message.chat.send_action("typing")
        reply = await asyncio.to_thread(hub.run_message, user.id, chat.id, text)
        await _send_text(update.effective_message, reply)

    async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await authorized(update) or update.effective_message is None:
            return
        text = update.effective_message.text or ""
        user, chat = update.effective_user, update.effective_chat
        await update.effective_message.chat.send_action("typing")
        reply = await asyncio.to_thread(hub.run_message, user.id, chat.id, text)
        await _send_text(update.effective_message, reply)

    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("agents", agents_command))
    application.add_handler(CommandHandler("reset", reset_command))
    for name in agent_names:
        application.add_handler(CommandHandler(name, agent_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message))

    LOGGER.info("Starting private Agent Hub Telegram bot; allowed user_id=%s", allowed_user_id)
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

