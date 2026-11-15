"""Runs independent, explicitly parallel-safe agents concurrently and synthesizes their outputs."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from langchain_core.messages import AIMessage


def make_orchestrator(agents):
    by_name = {a.name: a for a in agents}

    def orchestrator(state):
        selected = [by_name[n] for n in state.get("delegated", []) if n in by_name and by_name[n].parallel_safe]
        if len(selected) < 2:
            return {"messages": [AIMessage("Không đủ agent an toàn để chạy song song. Hãy chọn một agent cụ thể.", name="orchestrator")]}
        outputs = {}
        with ThreadPoolExecutor(max_workers=min(4, len(selected))) as pool:
            futures = {pool.submit(a.node, dict(state)): a.name for a in selected}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    result = future.result()
                    msgs = result.get("messages", [])
                    outputs[name] = str(getattr(msgs[-1], "content", msgs[-1])) if msgs else "Agent không trả nội dung."
                except Exception as exc:
                    outputs[name] = f"Agent lỗi: {type(exc).__name__}: {exc}"
        body = "\n\n".join(f"### {name}\n{outputs[name]}" for name in sorted(outputs))
        return {"messages": [AIMessage(content=body, name="orchestrator")]}
    return orchestrator

