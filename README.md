# Agent Hub

Agent Hub là nền tảng multi-agent chạy local bằng Python, LangGraph và Gemini. Giao diện gồm CLI và Telegram Bot. Dự án có Context, QA, Search, Research và Code Agent, cùng Supervisor, Orchestrator, memory, skills, tool registry và MCP bridge.

| Agent | Chức năng | Model |
|---|---|---|
| `qa` | Hỏi đáp, phân tích kiến trúc/source code | Gemini |
| `search` | Tìm kiếm read-only trong source dự án | Python |
| `context` | Tạo/cập nhật Project Context | Python |
| `research` | LLM lập kế hoạch, search web, fetch/extract, dedup, kiểm chứng chéo, rank và tóm tắt | Gemini (2 request/lần) + Python |
| `code` | Đọc, sửa và tạo source code trong dự án; xin phê duyệt trước khi ghi file; tự kiểm tra sau khi sửa | Gemini + Python (Python xử lý tối đa) |

## Architecture

```text
CLI / Telegram Bot
  -> Supervisor
      -> QA / Search / Context / Research / Code
      -> Orchestrator (chỉ phối hợp các agent parallel-safe)

Research Agent
  -> Research Planner (Gemini: query, intent, nguồn ưu tiên)
  -> Python tools: Search (DDGS + Google News RSS) / Select / Fetch+Extract / Deduplicate / Rank
  -> Evidence policy (digest ≥5 câu chuyện, verify ≥2 nguồn độc lập)
  -> Article Summarizer (Gemini: top 5 tin sạch, lọc trùng/giật tít)
  -> Report

Code Agent (LangGraph subgraph)
  entry -> resolve project -> gather (+ explore) -> propose -> validate
        -> approval (human) -> execute (apply + checks + review) -> report
              ^---- repair loop ----|      ^---- next round / ask user ----|
```

Research Agent và Code Agent đều là agent độc lập, `parallel_safe=False`. Research Agent dùng Gemini đúng 2 lần mỗi lần chạy (lập kế hoạch và tóm tắt); toàn bộ vòng lặp tìm kiếm/chọn/tải/xếp hạng là Python deterministic với ngân sách cứng.

## Install

Yêu cầu Python 3.11+ (khuyến nghị 3.12) và [Conda](https://docs.conda.io/en/latest/miniconda.html).

```powershell
conda create -n agent-hub python=3.12 -y
conda activate agent-hub
pip install -e ".[research,telegram,dev]"
copy .env.example .env      # macOS/Linux: cp .env.example .env
```

Các extra (cài thêm khi cần):

| Extra | Dùng cho |
|---|---|
| `research` | Research Agent (ddgs, requests, trafilatura, beautifulsoup4) |
| `telegram` | Telegram Bot (`python-telegram-bot`) |
| `dev` | `pytest` |
| `tokens` | Đếm token chính xác (`tiktoken`); mặc định ước lượng chars/4 |
| `mcp` | MCP bridge (`langchain-mcp-adapters`) |

Điền `GOOGLE_API_KEY` trong `.env` nếu dùng QA, Supervisor hoặc Code Agent. Research Agent dùng Gemini cho planner/summarizer; đặt `HUB_RESEARCH_LLM=false` để chạy hoàn toàn bằng Python mà không cần API key.

Mỗi lần mở terminal mới, kích hoạt lại môi trường trước khi chạy:

```powershell
conda activate agent-hub
```

## CLI

```text
hub
/research Tin tức AI mới nhất hôm nay
/qa Giải thích ModelRouter
/search ModelRouter
/context full cho .
/code trong dự án "D:\path\to\project" thêm hàm ... vào src/utils.py
/agents
/models
/help
```

Các lệnh `/qa`, `/search`, `/context`, `/research`, `/code` gọi agent trực tiếp và bỏ qua Supervisor routing. Câu hỏi không có prefix sẽ được Supervisor định tuyến bằng một model nhỏ; Supervisor có thể không chọn `code` khi bạn muốn sửa code, vì vậy **hãy gọi `/code` trực tiếp**.

Các lệnh khác trong CLI: `/skills`, `/tools`, `/tool <name> <JSON>`, `/history`, `/memory`, `/remember`, `/reset`, `exit`.

Chạy độc lập Context Agent (không dùng LLM):

```powershell
repo-context <project-path>
```

## Telegram Bot

Cài extra `telegram`, rồi cấu hình trong `.env` (không commit `.env`):

```dotenv
HUB_TELEGRAM_BOT_TOKEN=<token từ @BotFather>
HUB_TELEGRAM_ALLOWED_USER_ID=<user id số của bạn>   # chỉ tài khoản này dùng được bot
```

```powershell
conda activate agent-hub
hub-telegram
```

Bot có `/start`, `/help`, `/agents`, `/reset` và một lệnh cho mỗi agent (`/qa`, `/search`, `/context`, `/research`, `/code`). Khi agent cần hỏi lại (chọn project, duyệt patch, câu hỏi của Code Agent), hãy **trả lời tin nhắn tiếp theo**; tin nhắn đó được dùng làm câu trả lời. Trạng thái chờ nằm trong bộ nhớ của tiến trình bot, nên restart bot sẽ mất yêu cầu đang chờ duyệt (không có file nào bị ghi trước khi bạn duyệt).

## Code Agent

Code Agent thao tác trực tiếp trên source code của một dự án theo yêu cầu bằng ngôn ngữ tự nhiên. Nguyên tắc: **Python xử lý tối đa, Gemini chỉ suy luận phần cần thiết.**

| Python đảm nhiệm | Gemini đảm nhiệm |
|---|---|
| Xác định project root, duyệt thư mục, tìm file | Hiểu yêu cầu |
| Đọc code, lọc file không liên quan, giới hạn context | Chọn file cần đọc thêm |
| Kiểm tra đường dẫn, secret, file được phép sửa | Lập kế hoạch, đề xuất chỉnh sửa |
| Áp dụng chỉnh sửa, sinh unified diff, backup, hoàn tác | Rà soát việc còn thiếu sau mỗi vòng |
| Chạy kiểm tra theo allowlist, phát hiện lỗi từ output | Phân tích lỗi khi cần sửa lại |

### Cách dùng

```text
/code trong dự án "D:\Do_An\MyProject" thêm một model CNN1D gồm 4 convolutional blocks, BatchNorm, ReLU, MaxPool. Giữ nguyên các model hiện tại.
```

1. Xác định project (đường dẫn trong yêu cầu, hoặc project đang chọn trong thread). Project phải nằm trong `HUB_WORKSPACE_ROOTS`.
2. Khám phá: agent đọc `CONTRIBUTING.md`/`AGENTS.md`/`CLAUDE.md` nếu có, xem bản đồ các file Python và đọc thêm file liên quan (factory, config, data loader...).
3. Đề xuất chỉnh sửa, Python kiểm tra và hiển thị kế hoạch, danh sách file, các lệnh sẽ chạy và diff.
4. Bạn trả lời: `y` = áp dụng · `n` = huỷ · `diff [số file]` = xem diff đầy đủ. Mọi nội dung khác đều được coi là huỷ.
5. Áp dụng patch (có backup), chạy kiểm tra, rồi rà soát: nếu còn việc cụ thể thì tự sang vòng sau (vẫn xin duyệt), nếu cần quyết định thì hỏi bạn đúng một câu (trả lời bằng chữ, hoặc gõ `dừng` để kết thúc).
6. Báo cáo: file đã tạo/sửa, các vòng đã làm, kết quả kiểm tra, vấn đề còn lại, `git diff --stat` và đường dẫn backup.

### Loại chỉnh sửa

- `modify`: thay một đoạn có sẵn (đoạn neo phải duy nhất; nếu chỉ lệch thụt lề/khoảng trắng và vẫn duy nhất thì agent tự khớp lại).
- `create`: tạo file mới (không ghi đè file có sẵn).
- `append`: thêm vào cuối file; với file Python có `if __name__ == "__main__"`, code mới được chèn trước khối đó. Các `import` trùng với file được tự bỏ.
- Không xoá file, không sửa notebook `.ipynb`, không cài package, không thao tác git ghi (commit/checkout/reset...).

### Kiểm tra sau khi sửa

Chỉ chạy công cụ mà dự án thực sự dùng, qua allowlist:

- `python -m compileall` cho các file `.py` đã đổi.
- `ruff check` nếu có `ruff.toml`, `.ruff.toml` hoặc `[tool.ruff]`.
- `pytest -q -x` nếu dự án có `tests/`, `pytest.ini` hoặc `[tool.pytest]`; nếu không, chạy riêng các file test mà agent vừa viết.

Lệnh được chạy bằng Python của dự án: `python_path` nếu đã đặt, nếu không thì `.venv`/`venv` trong dự án, nếu không có thì Python đang chạy hub. Nếu môi trường đó thiếu thư viện (ví dụ `torch`), agent báo "bỏ qua" kèm hướng dẫn, không coi đó là lỗi patch. Biến môi trường chứa `KEY`, `TOKEN`, `SECRET`, `PASSWORD` bị loại khỏi tiến trình kiểm tra.

Nếu kiểm tra thất bại, agent phân tích output và đề xuất patch sửa lỗi (tối đa `max_repair_attempts` lần, không lặp vô hạn); mỗi patch sửa lỗi cũng cần duyệt.

### Bảo mật

- Không đọc hoặc ghi `.env`, credentials, khoá riêng và các file bí mật; nội dung các file này không bao giờ được gửi cho Gemini (`.env.example` được phép).
- Không sửa `.git`, `.venv`, `node_modules`... và không đi ra ngoài project root (chặn `..`, đường dẫn tuyệt đối, symlink, tên thiết bị Windows).
- Không tự sửa các file bảo vệ của chính Code Agent (`src/hub/agents/code/*`).
- Chỉ chạy lệnh trong allowlist; Gemini không thực thi shell tuỳ ý.
- Patch được kiểm tra hash trước khi áp dụng: file đổi giữa lúc đề xuất và áp dụng thì bị từ chối. Ghi file theo kiểu tất cả hoặc không gì (hoàn tác nếu lỗi), bản gốc được backup tại `<HUB_DATA_DIR>/code-agent/backups/<thời gian>/`.
- Chế độ `auto` không bỏ qua bất kỳ giới hạn bảo mật nào ở trên.

### Cấu hình

Lưu tại `<HUB_DATA_DIR>/code-agent.json` (mặc định `~/.agent-hub/code-agent.json`). Xem và đổi ngay trong chat:

```text
/code config                         # xem cấu hình hiện tại
/code config max_context_chars 60000
/code config python_path C:\Users\you\miniconda3\envs\my-project\python.exe
/code config reset
```

| Khoá | Mặc định | Ý nghĩa |
|---|---|---|
| `approval_mode` | `approve` | `approve`: xin duyệt trước khi sửa; `auto`: tự áp dụng trong phạm vi cho phép (chỉ bật bằng cách sửa tay file JSON) |
| `model_strategy` | `hybrid` | `cheap`: luôn dùng model `code`; `hybrid`: model `code_strong` khi sửa lỗi, refactor/debug hoặc chạm từ 3 file; `strong`: luôn `code_strong` |
| `max_rounds` | `3` (1–3) | Số vòng làm việc (sau mỗi patch, agent rà soát và có thể làm tiếp/hỏi bạn); `1` = chỉ một patch |
| `explore_rounds` | `2` (0–3) | Số lượt "cần đọc thêm file nào" trước khi đề xuất; `0` = tắt |
| `max_repair_attempts` | `2` (0–3) | Số lần sửa lại khi patch bị từ chối hoặc kiểm tra thất bại |
| `max_tool_calls` | `40` | Giới hạn thao tác (đọc, tìm, gọi model, chạy lệnh) mỗi lần chạy |
| `max_context_chars` | `24000` | Giới hạn context gửi cho Gemini |
| `auto_run_tests` | `true` | Tự chạy kiểm tra sau khi áp dụng |
| `command_timeout` | `300` | Giây tối đa cho mỗi lệnh kiểm tra |
| `python_path` | rỗng | Python của môi trường chạy được dự án đích (ví dụ có `torch`) |

Mỗi vòng thêm vài lần gọi Gemini; nếu hết quota nhanh, đặt `explore_rounds 0` và `max_rounds 1`.

Chọn model: `/code` dùng `get_llm("code")` và `get_llm("code_strong")` của ModelRouter. Đặt `HUB_MODEL_CODE` và `HUB_MODEL_CODE_STRONG` trong `.env` để chọn model; nếu để trống, cả hai dùng pool `HUB_MODELS` nên `hybrid` chưa khác gì `cheap`. Chỉ dùng model có trong `HUB_MODELS`.

### Mã nguồn

```text
src/hub/agents/code/
├── spec.py        # đăng ký AgentSpec (name="code", parallel_safe=False)
├── graph.py       # workflow LangGraph (subgraph, interrupt/resume)
├── state.py       # trạng thái workflow
├── settings.py    # đọc/ghi/validate cấu hình, lệnh /code config
├── security.py    # chính sách đường dẫn, secret và allowlist lệnh
├── tools.py       # context, patch, diff, backup, git (chỉ đọc), kiểm tra
├── prompts.py     # prompt và schema cho Gemini
└── report.py      # prompt duyệt, trang diff, báo cáo cuối
```

`approval` và `ask` là các node riêng, không gọi LLM: LangGraph chạy lại cả node bị gián đoạn khi resume, nên cách này đảm bảo patch bạn đã duyệt chính là patch được áp dụng.

## Research Agent

Pipeline: **LLM lập kế hoạch → tìm kiếm → chọn URL → tải & trích nội dung → xếp hạng → kiểm tra bằng chứng → LLM tóm tắt/lọc → báo cáo**.

- **Planner** (Gemini, đúng 1 request/lần): sinh tối đa 3 query, `intent` (`digest` = tổng hợp tin, `verify` = kiểm chứng), domain ưu tiên và `recency_days`. Domain chỉ để cộng điểm, **không chặn** nguồn khác. LLM tắt/lỗi/hết quota → fallback 3 query tiếng Việt, research vẫn chạy.
- **Search**: `ddgs` (DuckDuckGo news + text) và **Google News RSS** (`hl=vi&gl=VN`), gộp rồi dedup theo URL và tiêu đề. Một backend lỗi thì backend còn lại vẫn trả kết quả.
- **Chọn trước khi tải**: chấm relevance + độ mới + uy tín nguồn, tối đa 4 URL/vòng, ≤2 bài/domain, 1 bài/câu chuyện mỗi vòng — tránh dồn ngân sách vào bài rác. Với `verify`, các bản sao cùng câu chuyện được tải ở vòng sau để đối chiếu độc lập.
- **Fetch**: `requests`, chỉ HTTP(S), chặn localhost/private/reserved IP, giới hạn redirect, timeout và 2 MB/trang. Trafilatura trích nội dung; Playwright là fallback cho trang render JS. Tối đa 12k ký tự/bài.
- **Ngày đăng thật**: JSON-LD `datePublished` → `article:published_time` → Trafilatura metadata. Thiếu ngày thì bị hạ tín hiệu thay vì tự bịa.
- **Ranking** (cộng = 1.0): relevance 40%, độ mới 25%, uy tín nguồn 20%, xác nhận chéo 10%, độ đầy đủ 5%. Câu hỏi toàn stopword tự chuyển sang trọng số mới/uy tín.
- **Điều kiện dừng** theo intent:
  - `digest`: ≥5 câu chuyện khác nhau từ ≥2 domain.
  - `verify`: câu chuyện trọng tâm có ≥2 **nguồn độc lập** (fingerprint nội dung; bản syndication không được tính là nguồn thứ hai).
- **Summarizer** (Gemini, đúng 1 request/lần): chọn top 5 tin sạch, 2–3 câu/tin, nêu số liệu nếu có, loại trùng/giật tít/PR/ngoài chủ đề. LLM lỗi → fallback trích **2 câu hoàn chỉnh**, không cắt giữa câu.
- **Report**: ≤3500 ký tự, mỗi tin có domain, thời gian, xác nhận chéo, URL và dòng "Đã lọc: …".

Chi phí: đúng **2 request Gemini/lần research**. Đặt `HUB_RESEARCH_LLM=false` để chạy thuần Python (0 request), vẫn có relevance, evidence và báo cáo sạch.

```env
HUB_RESEARCH_LLM=true
HUB_MODEL_RESEARCH=
HUB_RESEARCH_SEARCH_BACKENDS=ddgs,rss
HUB_RESEARCH_MAX_SEARCH_ROUNDS=3
HUB_RESEARCH_PAGES_PER_ROUND=4
HUB_RESEARCH_MAX_PAGES=12
HUB_RESEARCH_RESULTS_PER_QUERY=12
HUB_RESEARCH_MIN_ITEMS=5
HUB_RESEARCH_MAX_PER_DOMAIN=2
HUB_RESEARCH_DEADLINE_SECONDS=90
```

`benchmarks/README.md` mô tả cách tạo bộ nhãn tay để đánh giá chất lượng báo cáo (độ liên quan, trùng lặp, độ phủ nguồn).

## QA / Search / Code: chọn project

- QA, Search và Code chỉ tìm hoặc thao tác project bên trong `HUB_WORKSPACE_ROOTS`; không quét toàn bộ ổ đĩa.
- Khai báo nhiều workspace bằng dấu `;`, ví dụ trong `.env`:

```dotenv
HUB_WORKSPACE_ROOTS=D:\Trietdeptrai;D:\Projects
HUB_PROJECT_SCAN_DEPTH=4
```

- Khi người dùng nêu đường dẫn hoặc tên project, Agent Hub ưu tiên lựa chọn đó. Nếu có nhiều kết quả, agent hỏi chọn; nếu không tìm thấy, agent hỏi đường dẫn.
- Project đã chọn được giữ trong thread. Mỗi câu hỏi, QA dựng **chỉ mục dự án** ngay trong RAM (cây thư mục + entry points + số file, tối đa `HUB_QA_MINIMAP_CHARS` ký tự, mặc định 3000) rồi tự đọc thêm file/hàm cụ thể khi thiếu thông tin. QA **không** tự đọc `PROJECT_CONTEXT*.md`; file đó giờ chỉ dùng khi bạn chỉ định rõ đường dẫn trong câu hỏi hoặc để người đọc/chia sẻ.
- **Bộ nhớ hội thoại của QA** dùng một ngân sách chung `HUB_QA_HISTORY_CHARS` (mặc định 8000 ký tự): 2 lượt gần nhất giữ nguyên văn (`HUB_QA_VERBATIM_TURNS`, mỗi câu trả lời tối đa `HUB_QA_VERBATIM_CHARS`), các lượt cũ hơn được rút gọn thành trích yếu có nhãn `[qa]`/`[code]`/`[context]`, tối đa `HUB_QA_DIGEST_CHARS` mỗi lượt và `HUB_QA_HISTORY_TURNS` lượt. Nhờ vậy QA hiểu "cái đó / file kia / làm tiếp" mà không gửi lại toàn bộ hội thoại.
- Thông tin phải sống qua nhiều phiên nên nằm trong memory, không nằm trong history: `/remember <ghi chú>` ghi vào `.hub/project_memory.md` của project đang làm việc (QA đọc mỗi lượt, tối đa 8000 ký tự), còn `/memory add <ghi chú>` ghi vào `~/.agent-hub/user_memory.md` cho mọi project. Project đang làm việc cũng được lưu theo thread trong SQLite nên mở lại CLI không phải chọn lại.
- Context Agent và lệnh `repo-context <project-path>` lưu `PROJECT_CONTEXT_<mode>.md` ngay trong thư mục project (QA không tự nạp). Không dùng `HUB_CONTEXT_FILE` làm context toàn cục.

## Other components

- **Supervisor:** chọn agent phù hợp; chỉ delegate nhiều agent `parallel_safe`. Có lối tắt cố định cho `context`, `search`, `research`; `code` nên gọi trực tiếp bằng `/code`.
- **Orchestrator:** chạy các subagent độc lập song song và tổng hợp kết quả.
- **ModelRouter:** preferred model theo agent và Gemini fallback pool.
- **MemoryStore:** SQLite conversation history; user/project Markdown memory.
- **SkillRegistry:** nạp `SKILL.md`.
- **ToolRegistry:** shared tools; tool có side effect yêu cầu xác nhận.
- **MCP:** tùy chọn, cấu hình bằng `HUB_MCP_SERVERS`.

## Tests

```powershell
conda activate agent-hub
pytest -q
```

Test không cần Internet, không cần Gemini và không cần model local (dùng fake); `tests/conftest.py` cô lập test khỏi `.env` thật của bạn (ví dụ `HUB_WORKSPACE_ROOTS`, `HUB_DATA_DIR`). MCP cần kiểm thử riêng với server thật. Không commit `.env`, API keys hoặc dữ liệu memory cá nhân.