# agent-hub

Multi-agent hub: LangGraph supervisor (Gemini) -> agents. Agent đầu tiên: `context` (đọc repo -> `PROJECT_CONTEXT.md`, không dùng LLM).

```bash
pip install -e ".[dev]"            # thêm tokens nếu muốn đếm token bằng tiktoken
# dán key vào file .env (đã có sẵn, đã nằm trong .gitignore)
hub                                # chat, vd: "tạo context cho dự án ./my_repo"
repo-context ./my_repo --mode map  # chạy riêng context agent, không cần key
pytest
```

## Cấu trúc
```
src/hub/
  registry.py        # <-- đăng ký agent ở đây
  supervisor.py      # chọn agent (1 agent = không gọi LLM; nhiều agent = 1 call Gemini/lượt)
  graph.py           # START -> supervisor -> agent -> END
  state.py  llm.py  config.py  cli.py
  agents/
    base.py          # AgentSpec(name, description, node)
    context/         # agent đầu tiên (pipeline riêng: nodes/ + tools/)
```

## Thêm agent
1. `src/hub/agents/my_agent/__init__.py` expose `SPEC = AgentSpec(name, description, node)`
   (`node(state) -> {"messages": [AIMessage(...)]}`; cần hỏi user thì dùng `langgraph.types.interrupt`).
2. Thêm `SPEC` vào `AGENTS` trong `registry.py`. Xong, supervisor tự thấy agent mới qua `description`.
