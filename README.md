# Agent Hub

Agent Hub là nền tảng multi-agent chạy local bằng Python, LangGraph và Gemini. Phiên bản này giữ lại Context, QA, Search, Supervisor, Orchestrator, memory, skills, tool registry và MCP bridge; bổ sung Research Agent dùng Laya.

| Agent | Chức năng | Model |
|---|---|---|
| `qa` | Hỏi đáp, phân tích kiến trúc/source code | Gemini |
| `search` | Tìm kiếm read-only trong source dự án | Python |
| `context` | Tạo/cập nhật Project Context | Python |
| `research` | Search web, fetch, extract, deduplicate, verify và rank bài viết | Laya + Python; không gọi Gemini trong research loop |

## Architecture


## Multi-project workspaces

Configure one or more workspace roots in `.env` (Windows paths are separated with `;`):

```dotenv
HUB_WORKSPACE_ROOTS=D:\Projects;D:\Work
HUB_PROJECT_SCAN_DEPTH=4
```

QA and Search resolve the explicit project path/name first. If more than one project matches, Hub asks you to choose. A selected project is retained in the current thread.

Context and source search skip common virtual environments, dependency folders, caches, build outputs and PyInstaller `_internal` bundles. Add project-specific rules in a `.agentignore` file at that project's root, for example:

```gitignore
Transcripts_automation/
exports/
large_datasets/
```

The initial QA prompt keeps a compact project map; full source is read selectively with `read_file` / `read_function` to reduce Gemini input-token usage.

## CLI chat display

The terminal UI separates the `BẠN` prompt from a boxed, agent-labelled response, wraps long output to terminal width, and uses distinct colors when ANSI color is supported. Set `NO_COLOR=1` to disable colors or `FORCE_COLOR=1` to force them.
