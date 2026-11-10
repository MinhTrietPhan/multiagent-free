"""Chat loop. Handles agent questions (interrupts) transparently."""
from langchain_core.messages import HumanMessage
from langgraph.types import Command

from .graph import build_hub


def run_turn(app, text: str, config: dict, ask=input, say=print) -> str:
    result = app.invoke({"messages": [HumanMessage(text)]}, config)
    while "__interrupt__" in result:
        say(result["__interrupt__"][0].value)
        result = app.invoke(Command(resume=ask("> ")), config)
    return result["messages"][-1].content


def main():
    app = build_hub()
    config = {"configurable": {"thread_id": "cli"}}
    print("Agent hub. Gõ 'exit' để thoát. Ví dụ: 'tạo context cho dự án ./my_repo'")
    while True:
        try:
            text = input("bạn> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text.lower() in {"exit", "quit", ""}:
            if text:
                break
            continue
        print(run_turn(app, text, config))


if __name__ == "__main__":
    main()
