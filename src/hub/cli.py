
"""Chat loop. Handles agent questions (interrupts) transparently.

Chọn agent thẳng:  /qa <câu hỏi>   /context <yêu cầu>   -> bỏ qua bước LLM điều phối (đỡ tốn request).
Lệnh:              /help  /agents  /models  /reset
"""
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command
import os
import re
import shutil
import sys
import textwrap
from pathlib import Path
from .config import MODEL_POOL
from .graph import build_hub
from .model_router import TRACKER
from .registry import AGENTS
from .memory import MemoryStore
from .skills import SkillRegistry
from .tools.builtin import register_builtin_tools
from .tools.mcp import load_mcp_tools



_ANSI = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "cyan": "\033[96m",
    "green": "\033[92m",
    "yellow": "\033[93m",
    "red": "\033[91m",
    "dim": "\033[2m",
    "blue": "\033[94m",
}


def _color_enabled(stream=None) -> bool:
    """Enable ANSI only for interactive terminals; respect NO_COLOR/FORCE_COLOR."""
    if os.getenv("NO_COLOR") is not None:
        return False
    if os.getenv("FORCE_COLOR"):
        return True
    stream = stream or sys.stdout
    try:
        return bool(stream.isatty())
    except (AttributeError, OSError):
        return False


def _paint(text: str, color: str, enabled: bool = True, *, bold: bool = False) -> str:
    if not enabled:
        return text
    prefix = (_ANSI["bold"] if bold else "") + _ANSI[color]
    return f"{prefix}{text}{_ANSI['reset']}"


def input_prompt(label: str = "Bạn") -> str:
    """Distinct prompt so user input is visually separated from agent output."""
    color = _color_enabled()
    label_text = _paint(label.upper(), "cyan", color, bold=True)
    arrow = _paint("❯", "cyan", color, bold=True)
    return f"{label_text} {arrow} "


def _wrap_reply(reply: str, width: int | None = None) -> list[str]:
    """Wrap paragraphs while preserving blank lines, bullets and code indentation."""
    width = width or shutil.get_terminal_size((100, 24)).columns
    width = max(40, width - 4)  # reserve space for the left quote bar
    output: list[str] = []
    in_fence = False
    for raw in str(reply).replace("\r\n", "\n").split("\n"):
        stripped = raw.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            output.append(raw)
            continue
        if not stripped:
            output.append("")
            continue
        indent = raw[:len(raw) - len(raw.lstrip())]
        if in_fence:
            output.extend(textwrap.wrap(raw, width=width, subsequent_indent=indent + "    ",
                                        replace_whitespace=False, drop_whitespace=False) or [raw])
            continue
        prefix = ""
        content = raw.lstrip()
        for marker in ("- ", "* ", "• "):
            if content.startswith(marker):
                prefix, content = marker, content[len(marker):]
                break
        numbered = __import__("re").match(r"(\d+[.)]\s+)(.*)", content)
        if numbered:
            prefix, content = numbered.group(1), numbered.group(2)
        first_indent = indent + prefix
        subsequent = indent + " " * len(prefix)
        output.extend(textwrap.wrap(
            content, width=max(20, width - len(first_indent)),
            initial_indent=first_indent, subsequent_indent=subsequent,
            break_long_words=False, break_on_hyphens=False,
        ) or [raw])
    return output


def _terminal_markdown(reply: str, enabled: bool) -> str:
    """Render common Markdown constructs without requiring a terminal UI dependency."""
    output: list[str] = []
    in_code = False
    for raw in str(reply).replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        if line.strip().startswith("```"):
            in_code = not in_code
            if line.strip() == "```":
                output.append(_paint("  ── code ──", "dim", enabled))
            continue
        if in_code:
            output.append(_paint("  " + line, "blue", enabled))
            continue
        if re.match(r"^\s*\|?\s*:?-{3,}", line):
            continue  # Markdown table separator
        if "|" in line and line.strip().startswith("|"):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            output.append("  " + "  │  ".join(cells))
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            output.append(_paint(heading.group(2), "cyan", enabled, bold=True))
            continue
        line = re.sub(r"`([^`]+)`", lambda m: _paint(m.group(1), "yellow", enabled), line)
        line = re.sub(r"\*\*(.+?)\*\*", lambda m: _paint(m.group(1), "bold", enabled, bold=True), line)
        line = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", line)
        output.append(line)
    return "\n".join(output)


def print_agent_reply(agent: str, reply: str, *, model: str | None = None, stream=None) -> None:
    """Render a readable, boxed assistant response with a stable agent label."""
    stream = stream or sys.stdout
    color = _color_enabled(stream)
    width = max(48, min(120, shutil.get_terminal_size((100, 24)).columns))
    label = (agent or "hub").replace("_", "-").upper()
    title = f" AGENT · {label} "
    if model:
        title += f"· {model} "
    rule = "─" * max(4, width - len(title) - 2)
    print(_paint(f"╭─{title}{rule}", "green", color, bold=True), file=stream)
    for line in _wrap_reply(_terminal_markdown(reply, color), width):
        print(_paint("│", "green", color) + (f" {line}" if line else ""), file=stream)
    print(_paint("╰" + "─" * (width - 1), "green", color), file=stream)

def parse_route(text: str, names: list[str]) -> tuple[str | None, str]:
    """'/qa hàm nào train?' -> ('qa', 'hàm nào train?'). Không có prefix agent hợp lệ -> (None, text)."""
    if not text.startswith('/'):
        return (None, text)
    head, _, rest = text[1:].partition(' ')
    if head.lower() in names:
        return (head.lower(), rest.strip())
    return (None, text)


def _chat_input(_prompt: str = "") -> str:
    if _prompt:
        print(_paint(_prompt, "yellow", _color_enabled()))
    return input(input_prompt("Lựa chọn" if _prompt else "Bạn"))


def _turn(app, text: str, config: dict, ask, say, forced: str | None) -> tuple[str, str | None]:
    result = app.invoke({'messages': [HumanMessage(text)], 'forced': forced}, config)
    while '__interrupt__' in result:
        say(result['__interrupt__'][0].value)
        result = app.invoke(Command(resume=ask('')), config)
    last = result['messages'][-1]
    return (last.content, result.get('next') or getattr(last, 'name', None))


def run_turn(app, text: str, config: dict, ask=input, say=print, forced: str | None=None) -> str:
    return _turn(app, text, config, ask, say, forced)[0]


def help_text() -> str:
    lines = ['Chọn agent thẳng (không tốn request điều phối):']
    for a in AGENTS:
        lines.append(f"  /{a.name} <nội dung>   {a.description.split(':')[0]}")
    lines += ['Không có prefix -> LLM tự chọn agent (tốn 1 request nhỏ).', 'Lệnh: /models · /agents · /skills · /tools · /tool <name> <JSON> · /history · /memory · /remember · /reset · /help · exit']
    return '\n'.join(lines)


def models_report() -> str:
    nxt = TRACKER.next_model(MODEL_POOL)
    lines = ['Model pool (ưu tiên từ trên xuống, ▶ = model sẽ được gọi tiếp theo):']
    for m in MODEL_POOL:
        left = TRACKER.cooldown_left(m)
        status = f'   ✗ đang nghỉ, còn ~{left // 60 + 1} phút' if left else ''
        lines.append(f"  {'▶' if m == nxt else ' '} {m:<28}{TRACKER.calls.get(m, 0):>4} request{status}")
    lines.append('(Số request tính từ lúc mở chương trình; Gemini không trả quota còn lại.)')
    return '\n'.join(lines)


def _print_turn_notes():
    color = _color_enabled()
    for ev in TRACKER.turn_events:
        print(_paint(f"  ⚠ {ev}", "yellow", color))
    if TRACKER.route_models:
        print(_paint(
            f"  ↳ Điều phối: {' → '.join(TRACKER.route_models)} (gõ /qa hoặc /context để bỏ bước này)",
            "dim", color,
        ))


def _current_project(app, config: dict) -> str | None:
    """Project the graph is working on right now (None before the first project-aware turn)."""
    try:
        return app.get_state(config).values.get("active_project_root")
    except Exception:
        return None


def _restore_messages(store: MemoryStore, thread_id: str):
    messages = []
    for item in store.load_history(thread_id):
        if item["role"] == "user":
            messages.append(HumanMessage(content=item["content"]))
        else:
            messages.append(AIMessage(content=item["content"], name=item.get("agent")))
    return messages


def _current_thread_file(store: MemoryStore):
    return store.directory / "current_thread.txt"


def _new_thread_id(store: MemoryStore):
    import uuid
    thread_id = f"cli-{uuid.uuid4().hex[:12]}"
    _current_thread_file(store).write_text(thread_id, encoding="utf-8")
    return thread_id


def main():
    app = build_hub()
    names = [a.name for a in AGENTS]
    store = MemoryStore()
    registry = register_builtin_tools()
    if __import__("os").getenv("HUB_MCP_SERVERS", "").strip():
        import asyncio
        try:
            asyncio.run(load_mcp_tools(registry))
        except Exception as exc:
            print(f"MCP warning: {type(exc).__name__}: {exc}")
    thread_file = _current_thread_file(store)
    thread_id = thread_file.read_text(encoding="utf-8").strip() if thread_file.exists() else ""
    if not thread_id:
        thread_id = _new_thread_id(store)
    config = {"configurable": {"thread_id": thread_id}}
    restore_pending = True
    print("Agent Hub · /help để xem lệnh · exit để thoát")
    print(_paint("─" * min(72, shutil.get_terminal_size((100, 24)).columns), "dim", _color_enabled()))
    while True:
        try:
            text = input(input_prompt()).strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text.lower() in {"exit", "quit"}:
            break
        cmd = text.split()[0].lower()
        if cmd in {"/help", "/agents"}:
            print(help_text())
            if cmd == "/agents":
                for agent in AGENTS:
                    print(f"  {agent.name}: {agent.description}")
            continue
        if cmd == "/models":
            print(models_report())
            continue
        if cmd == "/skills":
            skills = SkillRegistry().load_all()
            print("Skills:" if skills else "Chưa có skill. Tạo skills/<tên>/SKILL.md hoặc ~/.agent-hub/skills/<tên>/SKILL.md")
            for skill in skills:
                print(f"  {skill.name}: {skill.description}")
            continue
        if cmd == "/tools":
            for tool in register_builtin_tools().list():
                print(f"  {tool.name} [{tool.source}]{' [confirmation required]' if tool.requires_confirmation else ''}: {tool.description}")
            continue
        if cmd == "/tool":
            import json
            parts = text.split(" ", 2)
            if len(parts) < 2:
                print("Cách dùng: /tool <name> <JSON arguments>")
                continue
            tool_name = parts[1]
            try:
                arguments = json.loads(parts[2]) if len(parts) > 2 else {}
                tool = register_builtin_tools().get(tool_name)
                confirmed = False
                if tool.requires_confirmation:
                    confirmed = input(f"Tool '{tool_name}' yêu cầu xác nhận. Thực thi? [y/N] ").strip().lower() == "y"
                    if not confirmed:
                        print("Đã hủy tool call.")
                        continue
                result = register_builtin_tools().execute(tool_name, confirmed=confirmed, **arguments)
                print(result)
            except Exception as exc:
                print(f"Tool error: {type(exc).__name__}: {exc}")
            continue
        if cmd == "/history":
            for item in store.load_history(thread_id, limit=20):
                print(f"{item['role']}: {item['content'][:300]}")
            continue
        if cmd == "/memory":
            if text.lower().startswith("/memory add "):
                note = text[len("/memory add "):].strip()
                store.append_user_memory(note)
                print("Đã lưu ghi chú vào user memory (mọi project).")
            else:
                print(store.read_user_memory() or "User memory đang trống. Dùng /memory add <ghi chú> để lưu thủ công.")
            continue
        if cmd == "/remember":
            note = text[len("/remember"):].strip()
            if not note:
                print("Cách dùng: /remember <ghi chú> — lưu vào .hub/project_memory.md của project đang làm việc.")
                continue
            root = _current_project(app, config) or store.get_active_project(thread_id)
            if not root or not Path(root).is_dir():
                print("Chưa có project đang làm việc. Hỏi /qa hoặc /code một câu trước rồi dùng lại /remember.")
                continue
            path = MemoryStore.append_project_memory(root, note)
            print(f"Đã lưu vào {path}")
            continue
        if cmd == "/reset":
            thread_id = _new_thread_id(store)
            config = {"configurable": {"thread_id": thread_id}}
            restore_pending = True
            print("Đã bắt đầu thread mới. Lịch sử cũ vẫn được lưu trong SQLite.")
            continue
        forced, body = parse_route(text, names)
        if text.startswith("/") and forced is None:
            print(f'Không có lệnh/agent "{cmd}". Gõ /help.')
            continue
        if forced and not body:
            print(f"Thêm nội dung sau /{forced}. Ví dụ: /{forced} ...")
            continue
        TRACKER.begin_turn()
        try:
            if restore_pending:
                history = _restore_messages(store, thread_id)
                seed = {"messages": history + [HumanMessage(body)], "forced": forced}
                saved_root = store.get_active_project(thread_id)
                if saved_root and Path(saved_root).is_dir():
                    seed["active_project_root"] = saved_root
                result = app.invoke(seed, config)
                while "__interrupt__" in result:
                    print(_paint("\nCẦN BẠN CHỌN", "yellow", _color_enabled(), bold=True))
                    print(str(result["__interrupt__"][0].value))
                    answer = input(input_prompt("Lựa chọn"))
                    result = app.invoke(Command(resume=answer), config)
                last = result["messages"][-1]
                reply, agent = last.content, result.get("next") or getattr(last, "name", None)
                restore_pending = False
            else:
                reply, agent = _turn(app, body, config, _chat_input, print, forced)
        except RuntimeError as e:
            _print_turn_notes()
            print(_paint(f"\nLỖI · {e}", "red", _color_enabled(), bold=True))
            print(_paint("Gõ /models để xem trạng thái model.", "dim", _color_enabled()))
            continue
        except Exception as e:
            _print_turn_notes()
            print(_paint(f"\nLỖI · {type(e).__name__}: {e}", "red", _color_enabled(), bold=True))
            continue
        _print_turn_notes()
        label = forced or agent or "hub"
        model_label = " → ".join(TRACKER.turn_models) or None
        print_agent_reply(label, reply, model=model_label)
        store.save_turn(thread_id, body, reply, agent or forced)
        project_root = _current_project(app, config)
        if project_root:
            store.set_active_project(thread_id, project_root)


if __name__ == "__main__":
    main()
