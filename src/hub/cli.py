"""Chat loop. Handles agent questions (interrupts) transparently.

Chọn agent thẳng:  /qa <câu hỏi>   /context <yêu cầu>   -> bỏ qua bước LLM điều phối (đỡ tốn request).
Lệnh:              /help  /agents  /models  /reset
"""
from langchain_core.messages import HumanMessage
from langgraph.types import Command
from .config import MODEL_POOL
from .graph import build_hub
from .model_router import TRACKER
from .registry import AGENTS


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
    lines += ['Không có prefix -> LLM tự chọn agent (tốn 1 request nhỏ).', 'Lệnh: /models (xem quota ước lượng) · /reset (xoá trí nhớ hội thoại) · /help · exit']
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


def main():
    app = build_hub()
    names = [a.name for a in AGENTS]
    thread_no = 1
    config = {'configurable': {'thread_id': f'cli-{thread_no}'}}
    print("Agent hub. Gõ /help để xem lệnh, 'exit' để thoát. Ví dụ: '/context ./my_repo' hoặc '/qa hàm nào xử lý GAF?'")
    while True:
        try:
            text = input('bạn> ').strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text.lower() in {'exit', 'quit'}:
            break
        cmd = text.split()[0].lower()
        if cmd in {'/help', '/agents'}:
            print(help_text())
            continue
        if cmd == '/models':
            print(models_report())
            continue
        if cmd == '/reset':
            thread_no += 1
            config = {'configurable': {'thread_id': f'cli-{thread_no}'}}
            print('Đã xoá trí nhớ hội thoại, bắt đầu cuộc trò chuyện mới.')
            continue
        forced, body = parse_route(text, names)
        if text.startswith('/') and forced is None:
            print(f'Không có lệnh/agent "{cmd}". Gõ /help.')
            continue
        if forced and not body:
            print(f'Thêm nội dung sau /{forced}. Ví dụ: /{forced} ...')
            continue
        TRACKER.begin_turn()
        try:
            reply, agent = _turn(app, body, config, input, print, forced)
        except RuntimeError as e:
            _print_turn_notes()
            print(f'hub> ⚠ {e}  (gõ /models để xem trạng thái)')
            continue
        _print_turn_notes()
        label = ' → '.join(TRACKER.turn_models) or forced or agent or 'hub'
        print(f'{label}> {reply}')


if __name__ == '__main__':
    main()
