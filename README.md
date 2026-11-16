# Agent Hub

Agent Hub là nền tảng multi-agent chạy local bằng Python, LangGraph và Gemini. Phiên bản này giữ lại Context, QA, Search, Supervisor, Orchestrator, memory, skills, tool registry và MCP bridge; bổ sung Research Agent dùng Laya.

| Agent | Chức năng | Model |
|---|---|---|
| `qa` | Hỏi đáp, phân tích kiến trúc/source code | Gemini |
| `search` | Tìm kiếm read-only trong source dự án | Python |
| `context` | Tạo/cập nhật Project Context | Python |
| `research` | Search web, fetch, extract, deduplicate, verify và rank bài viết | Laya + Python; không gọi Gemini trong research loop |

## Architecture

```text
CLI
  -> Supervisor
      -> QA / Search / Context / Research
      -> Orchestrator (chỉ phối hợp các agent parallel-safe)

Research Agent
  -> Research Controller
  -> Laya Decision Engine (lazy-loaded, multilingual)
  -> Python tools: Search / Fetch / Extract / Deduplicate / Verify
  -> Laya experimental scoring + deterministic ranking baseline
  -> Report
```

Research Agent là agent độc lập, `parallel_safe=False`. Laya là decision engine bên trong Research Agent, không phải agent ngang hàng. Research Agent không import ModelRouter và không gọi Gemini trong vòng lặp tìm kiếm/thu thập.

## Install

Yêu cầu Python 3.11+.

```bash
python -m venv .venv
# Windows PowerShell: .venv\\Scripts\\Activate.ps1
# macOS/Linux: source .venv/bin/activate
pip install -e ".[research,dev]"
cp .env.example .env
```

Điền `GOOGLE_API_KEY` nếu dùng QA/Supervisor. Research Agent không cần Gemini API key. Laya và các thư viện research được cài qua optional extra `research`.

## CLI

```text
hub
/research Tin tức AI mới nhất hôm nay
/qa Giải thích ModelRouter
/search ModelRouter
/context full cho .
/agents
/models
/help
```

Các lệnh `/qa`, `/search`, `/context`, `/research` gọi agent trực tiếp và bỏ qua Supervisor routing. Câu hỏi không có prefix sẽ được Supervisor định tuyến.

## Research Agent (Laya)

- Search: `ddgs` (DuckDuckGo search).
- Fetch: `requests`, chỉ HTTP(S), kiểm tra public IP, chặn localhost/private/reserved IP, giới hạn redirect, timeout và 2 MB/trang.
- Extract: Trafilatura; BeautifulSoup là fallback.
- Processing: Python dedup URL/title, giữ evidence từ nhiều domain để đo corroboration.
- Decision: Laya chọn trong tập action hữu hạn `FETCH_NEXT`, `SEARCH_MORE`, `VERIFY_TOP`, `STOP`.
- Ranking: baseline minh bạch kết hợp freshness, heuristic source quality, corroboration và độ đầy đủ nội dung. Laya ordinal score được lưu riêng và chỉ ảnh hưởng final score ở chế độ `active`.

### Laya modes

`HUB_RESEARCH_LAYA_MODE=shadow` là mặc định. Laya được gọi để dự đoán action và chấm điểm, nhưng Python policy vẫn quyết định thao tác thật và ranking vẫn dùng baseline. Đây là chế độ nên dùng để benchmark.

- `shadow`: chạy song song để so sánh, không để Laya điều khiển thực tế.
- `active`: cho Laya chọn action và kết hợp điểm Laya vào ranking; chỉ bật sau benchmark held-out.
- `off`: không load Laya; dùng Python policy và ranking baseline.

Cấu hình trong `.env`:

```env
HUB_LAYA_MODEL=multilingual
HUB_LAYA_DEVICE=cpu
HUB_RESEARCH_LAYA_MODE=shadow
HUB_RESEARCH_MAX_SEARCH_ROUNDS=2
HUB_RESEARCH_MAX_PAGES=12
HUB_RESEARCH_RESULTS_PER_QUERY=12
```

Laya được load lazy và tái sử dụng trong process. Để tránh Gemini token, research loop không gọi QA, Gemini hoặc Supervisor ở từng trang. Chỉ một lần routing ở Supervisor có thể phát sinh Gemini khi người dùng không gọi trực tiếp `/research`.

### Important model limitation

Model card hiện công bố checkpoint base multilingual đạt 0.342 trên benchmark typed-decisions, thấp hơn majority baseline 0.461 của benchmark đó. Đây không phải benchmark research tiếng Việt, nhưng đủ để không tin zero-shot decisions ngay. Vì vậy chế độ `shadow` là mặc định; không coi confidence là bảo đảm đúng.

Điểm hiện tại **không phải điểm viral/trending**: pipeline chưa lấy view/share/engagement từ nền tảng mạng xã hội. Nó xếp hạng theo độ mới, tín hiệu uy tín nguồn, sự xác nhận từ domain khác và độ đầy đủ nội dung. Ngày đăng thiếu sẽ bị giảm tín hiệu thay vì tự bịa ngày.

### Benchmark Laya

Tạo JSONL theo schema trong `benchmarks/README.md`, sau đó chạy:

```bash
python scripts/benchmark_laya_research.py benchmarks/my_labels.jsonl
```

Chỉ chuyển `active` khi Laya vượt baseline trên tập held-out, có kết quả theo từng loại action và ranking được kiểm tra bằng nhãn người dùng.

## Other components

- **Supervisor:** chọn agent phù hợp; chỉ delegate nhiều agent `parallel_safe`.
- **Orchestrator:** chạy các subagent độc lập song song và tổng hợp kết quả.
- **ModelRouter:** preferred model theo agent và Gemini fallback pool.
- **MemoryStore:** SQLite conversation history; user/project Markdown memory.
- **SkillRegistry:** nạp `SKILL.md`.
- **ToolRegistry:** shared tools; tool có side effect yêu cầu xác nhận.
- **MCP:** tùy chọn, cấu hình bằng `HUB_MCP_SERVERS`.

## Tests

```bash
pytest -q
```

Research tests không cần tải model Laya hoặc gọi Internet vì sử dụng mock search/fetch/model. MCP cần kiểm thử riêng với server thật. Không commit `.env`, API keys hoặc dữ liệu memory cá nhân.

## QA / Search project selection

- QA và Search chỉ tìm project bên trong `HUB_WORKSPACE_ROOTS`; không quét toàn bộ ổ đĩa.
- Khai báo nhiều workspace bằng dấu `;`, ví dụ trong `.env`:

```dotenv
HUB_WORKSPACE_ROOTS=D:\Trietdeptrai;D:\Projects
HUB_PROJECT_SCAN_DEPTH=4
```

- Khi người dùng nêu đường dẫn hoặc tên project, Agent Hub ưu tiên lựa chọn đó. Nếu có nhiều kết quả, agent hỏi chọn; nếu không tìm thấy, agent hỏi đường dẫn.
- Project đã chọn được giữ trong thread. QA đọc `PROJECT_CONTEXT*.md` trong thư mục gốc nếu có; nếu không có, QA vẫn đọc source code trực tiếp. Source code luôn được ưu tiên khi khác với context.
- Context Agent và lệnh `repo-context <project-path>` lưu `PROJECT_CONTEXT_<mode>.md` ngay trong thư mục project. Không dùng `HUB_CONTEXT_FILE` làm context toàn cục.

## PMT Studio integration

Agent Hub có các tool gọi PMT Studio local API. Cài `api_server/` vào PMT Studio theo gói `PMT-Studio-API`, chạy API ở `127.0.0.1:8765`, sau đó thêm cùng một token vào `.env` Agent Hub:

```env
HUB_PMT_STUDIO_URL=http://127.0.0.1:8765
HUB_PMT_STUDIO_TOKEN=TOKEN_DA_TAO_BEN_PMT
```

Khởi động lại `hub`, dùng `/tools` để xem tool và `/tool` để gọi:

```text
/tool pmt_health {}
/tool pmt_list_channels {}
/tool pmt_list_projects {"channel_id":"channel-id"}
/tool pmt_create_project {"title":"Video title","language":"vi","channel_id":"channel-id"}
/tool pmt_start_build {"project_name":"video-title","channel_id":"channel-id","options":{"num_stories":3,"char_len":4500}}
/tool pmt_job_status {"job_id":"JOB_ID"}
```

`pmt_create_project`, `pmt_start_build` và `pmt_cancel_job` yêu cầu xác nhận thủ công. Tool trả về `job_id`; gọi `pmt_job_status` để theo dõi. Khi job hoàn tất, trường `output` là đường dẫn video render.

Các tool này được đăng ký trong ToolRegistry hiện tại. Chúng chưa được gắn vào một Video Agent tự động; bước tiếp theo là xây Video Agent để hiểu yêu cầu tự nhiên, hỏi thông tin còn thiếu và gọi đúng PMT tools theo từng giai đoạn.
