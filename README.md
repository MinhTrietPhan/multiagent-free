# Agent Hub

Agent Hub là nền tảng multi-agent chạy local bằng Python, LangGraph và Gemini. Bản nâng cấp giữ lại Context Agent, QA Agent, model failover và CLI hiện tại; bổ sung Search Agent, điều phối đa agent, memory bền vững, skills, shared tool registry và MCP bridge tùy chọn.

| Agent | Chức năng | LLM |
|---|---|---|
| `qa` | Hỏi đáp, phân tích kiến trúc/source code dựa trên context và đọc thêm code khi cần | Gemini |
| `search` | Tìm kiếm read-only trong source dự án | Không |
| `context` | Tạo mới project context | Không |

## Kiến trúc

```text
CLI
 ├── Supervisor (route một agent hoặc lập kế hoạch delegate)
 │    ├── QA Agent
 │    ├── Context Agent
 │    ├── Search Agent
 │    └── Orchestrator (chạy các agent parallel-safe độc lập)
 ├── ModelRouter (preferred model theo agent + fallback pool)
 ├── Memory
 │    ├── SQLite: lịch sử hội thoại theo thread
 │    ├── ~/.agent-hub/user_memory.md: ghi chú người dùng do người dùng thêm
 │    └── .hub/project_memory.md: ghi chú riêng của dự án
 ├── Skills: skills/<name>/SKILL.md và ~/.agent-hub/skills/<name>/SKILL.md
 └── Shared Tool Registry (built-in tools + MCP tools tùy chọn)
```

### Thành phần

| Thành phần | Trách nhiệm |
|---|---|
| Supervisor | Chọn agent phù hợp; chỉ delegate khi có từ hai agent `parallel_safe` phù hợp |
| Orchestrator | Chạy các subagent độc lập song song, tổng hợp kết quả |
| ModelRouter | Ưu tiên model cấu hình riêng của agent, sau đó failover theo `HUB_MODELS` |
| MemoryStore | Lưu lịch sử chat vào SQLite; hỗ trợ user/project Markdown memory |
| SkillRegistry | Nạp `SKILL.md`, chọn skill liên quan và đưa hướng dẫn vào QA context |
| ToolRegistry | Đăng ký và gọi tool tập trung; tool có side effect có thể yêu cầu xác nhận |
| MCP bridge | Nạp MCP tools nếu cài adapter và khai báo server; mặc định tắt |

`context` không được đánh dấu parallel-safe vì nó ghi file và có thể yêu cầu input. `qa` là read-only nên được phép tham gia delegation song song.

## Cài đặt

Yêu cầu Python 3.11+.

```bash
python -m venv .venv
# Windows PowerShell: .venv\\Scripts\\Activate.ps1
# macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Điền `GOOGLE_API_KEY` vào `.env`, sau đó chạy:

```bash
hub
```

Không commit `.env`, SQLite database hoặc dữ liệu memory cá nhân.

## CLI commands

| Lệnh | Chức năng |
|---|---|
| `/qa <câu hỏi>` | Gọi QA trực tiếp, bỏ qua supervisor |
| `/context <yêu cầu>` | Tạo project context |
| `/search <từ khóa>` | Tìm kiếm trong source dự án |
| `/agents` | Liệt kê agent đã đăng ký |
| `/models` | Xem model pool, số request và cooldown |
| `/skills` | Liệt kê skills được tìm thấy |
| `/tools` | Liệt kê shared tools |
| `/tool <name> <JSON>` | Gọi tool; tool có side effect sẽ hỏi xác nhận |
| `/history` | Xem lịch sử thread hiện tại |
| `/memory` | Xem user memory |
| `/memory add <ghi chú>` | Thêm ghi chú vào user memory (chủ động, không tự trích xuất) |
| `/reset` | Tạo thread mới; lịch sử thread cũ vẫn nằm trong SQLite |
| `/help` | Hướng dẫn |

Lịch sử chat được lưu tại `~/.agent-hub/hub.sqlite3` (có thể đổi bằng `HUB_DATA_DIR`). Khi khởi động lại, CLI tiếp tục thread gần nhất. `/reset` tạo thread mới chứ không xóa dữ liệu cũ.

## Memory

- **Conversation memory:** SQLite, bền vững qua lần chạy lại chương trình.
- **User memory:** `~/.agent-hub/user_memory.md`; chỉ lưu khi người dùng gọi `/memory add`.
- **Project memory:** `.hub/project_memory.md` trong repository; QA nạp nội dung này khi trả lời dự án đó.

Memory được đưa vào prompt như dữ liệu tham khảo, không phải chỉ thị hệ thống. Không lưu API key, mật khẩu hoặc thông tin bí mật vào memory.

## Skills

Tạo skill theo cấu trúc:

```text
skills/
└── code-review/
    └── SKILL.md
```

`SKILL.md` có thể có metadata `description:` ở đầu file. QA tự chọn tối đa ba skill có nội dung liên quan đến câu hỏi. Có thể cài skill dùng chung tại `~/.agent-hub/skills/<skill-name>/SKILL.md`.

## Shared tools và quyền

Built-in tools hiện tại:

- `read_file`: đọc file trong project root, chặn path traversal.
- `read_function`: trích function/class từ file dự án.
- `search_project`: tìm chuỗi trong file text dự án.

Tool khai báo `requires_confirmation=True` sẽ yêu cầu xác nhận khi gọi qua `/tool`. MCP tools mặc định luôn yêu cầu xác nhận. Không đăng ký tool xóa file hoặc chạy shell tùy ý nếu chưa có lớp phê duyệt riêng.

## MCP (tùy chọn)

Cài adapter:

```bash
pip install -e ".[mcp]"
```

`HUB_MCP_SERVERS` nhận JSON cấu hình theo định dạng `MultiServerMCPClient` của `langchain-mcp-adapters`. MCP bridge hiện là lớp nạp tool tùy chọn; chưa tự khởi động server MCP nếu biến này trống. Chỉ kết nối server tin cậy và cấp quyền tối thiểu.

## Model routing

- `HUB_MODELS`: danh sách model fallback, phân tách bằng dấu phẩy.
- `HUB_MODEL_QA`: model ưu tiên cho QA.
- `HUB_MODEL_SUPERVISOR`: model ưu tiên cho supervisor.

Nếu model riêng lỗi có thể retry, router tiếp tục thử các model còn lại trong pool. Không cấu hình model riêng thì dùng pool chung.

## Thêm agent

Tạo `src/hub/agents/<name>/` và export `SPEC` kiểu `AgentSpec`. Đặt `parallel_safe=True` chỉ khi agent không yêu cầu interrupt/input và không gây side effect không an toàn khi chạy đồng thời. Đăng ký agent trong `src/hub/registry.py`.

## Tests

```bash
pytest -q
```

Các test hiện có dùng mock LLM, không cần API key. MCP integration cần được kiểm thử riêng với MCP server thực tế.
