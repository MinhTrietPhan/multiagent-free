"""Chat loop. Handles agent questions (interrupts) transparently.

Chọn agent thẳng:  /qa <câu hỏi>   /context <yêu cầu>   -> bỏ qua bước LLM điều phối (đỡ tốn request).
Lệnh:              /help  /agents  /models  /reset
"""
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command
from .config import MODEL_POOL
from .graph import build_hub
from .model_router import TRACKER
from .registry import AGENTS
from .memory import MemoryStore
from .skills import SkillRegistry
from .tools.builtin import register_builtin_tools
from .tools.mcp import load_mcp_tools


def parse_route(text: str, names: list[str]) -> tuple[str | None, str]:
    """'/qa hàm nào train?' -> ('qa', 'hàm nào train?'). Không có prefix agent hợp lệ -> (None, text)."""
    if not text.startswith('/'):
        return (None, text)
    head, _, rest = text[1:].partition(' ')
    if head.lower() in names:
        return (head.lower(), rest.strip())
    return (None, text)


def _turn(app, text: str, config: dict, ask, say, forced: str | None) -> tuple[str, str | None]:
    result = app.invoke({'messages': [HumanMessage(text)], 'forced': forced}, config)
    while '__interrupt__' in result:
        say(result['__interrupt__'][0].value)
        result = app.invoke(Command(resume=ask('> ')), config)
    return (result['messages'][-1].content, result.get('next'))


def run_turn(app, text: str, config: dict, ask=input, say=print, forced: str | None=None) -> str:
    return _turn(app, text, config, ask, say, forced)[0]


def help_text() -> str:
    lines = ['Chọn agent thẳng (không tốn request điều phối):']
    for a in AGENTS:
        lines.append(f"  /{a.name} <nội dung>   {a.description.split(':')[0]}")
    lines += ['Không có prefix -> LLM tự chọn agent (tốn 1 request nhỏ).', 'Lệnh: /models · /agents · /skills · /tools · /tool <name> <JSON> · /history · /memory · /reset · /help · exit']
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
    for ev in TRACKER.turn_events:
        print(f'  ⚠ {ev}')
    if TRACKER.route_models:
        print(f"  ↳ điều phối bằng {' → '.join(TRACKER.route_models)} (gõ /qa hoặc /context để bỏ bước này)")


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
    print("Agent Hub. Gõ /help để xem lệnh, 'exit' để thoát.")
    while True:
        try:
            text = input("bạn> ").strip()
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
                print("Đã lưu ghi chú vào user memory.")
            else:
                print(store.read_user_memory() or "User memory đang trống. Dùng /memory add <ghi chú> để lưu thủ công.")
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
                result = app.invoke({"messages": history + [HumanMessage(body)], "forced": forced}, config)
                while "__interrupt__" in result:
                    answer = input(str(result["__interrupt__"][0].value) + "\n> ")
                    result = app.invoke(Command(resume=answer), config)
                reply, agent = result["messages"][-1].content, result.get("next")
                restore_pending = False
            else:
                reply, agent = _turn(app, body, config, input, print, forced)
        except RuntimeError as e:
            _print_turn_notes()
            print(f"hub> ⚠ {e}  (gõ /models để xem trạng thái)")
            continue
        except Exception as e:
            _print_turn_notes()
            print(f"hub> Lỗi: {type(e).__name__}: {e}")
            continue
        _print_turn_notes()
        label = " → ".join(TRACKER.turn_models) or forced or agent or "hub"
        print(f"{label}> {reply}")
        store.save_turn(thread_id, body, reply, agent or forced)


if __name__ == "__main__":
    main()
