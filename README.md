# Agent Hub

Một "trung tâm agent" chạy trên dòng lệnh, dùng **LangGraph** + **Gemini**. Hiện có 2 agent:

| Agent | Làm gì | Dùng LLM? |
|---|---|---|
| `context` | Quét một dự án Python và tạo file `PROJECT_CONTEXT_<mức>.md` (cây thư mục, skeleton, code) | Không |
| `qa` | Trả lời câu hỏi về dự án dựa trên file context, tự đọc thêm code khi cần | Có (Gemini) |

Ngoài ra hub có: chọn agent thẳng bằng `/qa`, `/context` (đỡ tốn request), tự chuyển model khi hết quota (failover), hiển thị model đang trả lời, đếm số request mỗi model, và trí nhớ hội thoại cho `qa`.

---

## Mục lục
1. [Cài đặt từ đầu](#1-cài-đặt-từ-đầu)
2. [Chạy thử nhanh](#2-chạy-thử-nhanh-5-phút)
3. [Cách dùng hub](#3-cách-dùng-hub)
4. [Agent `context`](#4-agent-context)
5. [Agent `qa`](#5-agent-qa)
6. [Model, failover và quota](#6-model-failover-và-quota)
7. [Biến môi trường](#7-biến-môi-trường)
8. [Cấu trúc thư mục](#8-cấu-trúc-thư-mục)
9. [Chạy test](#9-chạy-test)
10. [Thêm agent mới](#10-thêm-agent-mới)
11. [Xử lý lỗi thường gặp](#11-xử-lý-lỗi-thường-gặp)
12. [Giới hạn hiện tại](#12-giới-hạn-hiện-tại)

---

## 1. Cài đặt từ đầu

### 1.1. Yêu cầu
- **Python 3.11 trở lên** (code dùng `tomllib`; đã chạy với Python 3.12).
- **Git** (nên có; agent `context` dùng `git ls-files` để bỏ qua file bị `.gitignore`. Không có git vẫn chạy, chỉ quét bằng cách duyệt thư mục).
- **Gemini API key** (miễn phí): vào <https://aistudio.google.com/apikey>, đăng nhập Google và bấm tạo key.
- **Có internet** khi chạy `qa` hoặc khi hub phải dùng LLM để điều phối.

### 1.2. Lấy code
```powershell
git clone <đường-dẫn-repo-của-bạn> agent-hub
cd agent-hub
```

### 1.3. Tạo môi trường

**Cách A — Conda (Windows/macOS/Linux):**
```powershell
conda create -n agent-hub python=3.12 -y
conda activate agent-hub
```

**Cách B — venv có sẵn của Python:**

Windows (PowerShell):
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```
macOS / Linux:
```bash
python3 -m venv .venv
source .venv/bin/activate
```
> Nếu PowerShell báo lỗi "running scripts is disabled", chạy `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` rồi kích hoạt lại.

### 1.4. Cài thư viện
Chạy trong thư mục gốc repo (nơi có `pyproject.toml`), khi môi trường đã được kích hoạt (đầu dòng lệnh có `(agent-hub)` hoặc `(.venv)`):
```powershell
pip install -e .
pip install pytest
```
- `pip install -e .` cài dự án ở chế độ "chỉnh sửa được": sửa code là có hiệu lực ngay và tạo lệnh `hub`.
- Thư viện chính: `langchain-core`, `langchain-google-genai`, `langgraph`, `python-dotenv`. Code còn dùng `pydantic` và `tiktoken` (đếm token; không có thì tự ước lượng bằng số ký tự / 4).
- Nếu `pip install -e .` thiếu thư viện nào đó khi chạy, cài thêm bằng `pip install <tên>`.

### 1.5. Khai báo API key
Tạo file `.env` ở **thư mục gốc repo** (cùng chỗ với `pyproject.toml`):
```env
GOOGLE_API_KEY=dán_key_của_bạn_vào_đây
```
(Có thể dùng tên `GEMINI_API_KEY` thay cho `GOOGLE_API_KEY`.)

> **Quan trọng:** không commit file `.env`. Thêm dòng `.env` vào `.gitignore`.

Hub đọc `.env` từ thư mục bạn đang đứng khi chạy lệnh, nên hãy chạy `hub` từ thư mục gốc repo (hoặc đặt biến môi trường hệ thống).

### 1.6. Kiểm tra cài đặt
```powershell
pytest
```
Tất cả test phải pass. Các test dùng LLM giả nên không cần API key và không tốn quota.

---

## 2. Chạy thử nhanh (5 phút)

```powershell
hub
```
(hoặc `python -m hub.cli`). Bạn sẽ thấy dấu nhắc `bạn>`. Gõ lần lượt:

```
bạn> /context map .
context> Đã tạo `PROJECT_CONTEXT_map.md` từ `D:\...\agent-hub` (mode=map, ~3,000 token). ...

bạn> /qa hàm nào xử lý failover model?
gemini-3.1-flash-lite> Hàm `_invoke` trong `src/hub/model_router.py:L75-96` ...
---
📄 Nguồn: `PROJECT_CONTEXT_map.md` + code bổ sung (src/hub/model_router.py::_invoke)

bạn> /models
```

Dòng đầu mỗi câu trả lời cho biết **ai trả lời**: tên agent (`context>`, không tốn quota) hoặc tên model Gemini (`gemini-2.5-flash>`).

Thoát bằng `exit`, `quit` hoặc Ctrl+C.

---

## 3. Cách dùng hub

### 3.1. Hai cách gửi tin nhắn

**Cách 1 — Chọn agent thẳng (khuyến nghị):** gõ `/tên_agent` rồi nội dung.
```
bạn> /context D:\projects\my_repo
bạn> /qa hàm train nằm ở đâu?
```
Không tốn request điều phối, không bao giờ chọn nhầm.

**Cách 2 — Gõ bình thường:** một LLM nhỏ ("người điều phối") đọc mô tả các agent và đoán nên giao cho agent nào. Cách này **tốn 1 request** mỗi tin nhắn và **có thể chọn nhầm**. Khi hub thực sự dùng LLM để điều phối, bạn sẽ thấy dòng:
```
  ↳ điều phối bằng gemini-2.5-flash-lite (gõ /qa hoặc /context để bỏ bước này)
```

> Trí nhớ hội thoại của `qa` chỉ hoạt động với các câu mà `qa` thực sự xử lý. Nếu bạn quên `/qa` và hub giao nhầm cho agent khác thì câu đó không vào trí nhớ.

### 3.2. Danh sách lệnh

| Lệnh | Tác dụng |
|---|---|
| `/qa <câu hỏi>` | Hỏi đáp về dự án (cần có file context) |
| `/context <yêu cầu>` | Tạo file context |
| `/models` | Xem model pool, số request đã dùng, model nào đang bị nghỉ (cooldown) |
| `/reset` | Xoá trí nhớ hội thoại, bắt đầu cuộc trò chuyện mới |
| `/help` hoặc `/agents` | Xem hướng dẫn và danh sách agent |
| `exit` / `quit` | Thoát |

- Gõ `/qa` hoặc `/context` mà không có nội dung → hub nhắc bạn thêm nội dung.
- Gõ lệnh không tồn tại (ví dụ `/abc`) → hub báo không có lệnh đó.

### 3.3. Cảnh báo khi đổi model
Nếu một model hết quota giữa chừng, hub tự chuyển sang model kế tiếp và báo:
```
  ⚠ gemini-3.1-flash-lite: hết quota/rate limit → chuyển model kế tiếp
gemini-2.5-flash-lite> ...
```
Nếu **tất cả** model đều không dùng được, hub in cảnh báo (không crash) và gợi ý gõ `/models`.

---

## 4. Agent `context`

Tạo file Markdown mô tả cả dự án để đưa cho `qa` hoặc dán cho chatbot khác. **Không dùng LLM, không tốn quota.**

### 4.1. Cách gọi
```
bạn> /context D:\projects\my_repo            ← sẽ hỏi mức ngữ cảnh
bạn> /context full D:\projects\my_repo       ← chọn luôn mức full
bạn> /context map .                          ← "." là thư mục hiện tại
bạn> /context                                 ← (sẽ nhắc bạn thêm nội dung; hãy gõ kèm đường dẫn)
```
Nếu thiếu đường dẫn hoặc mức, agent hỏi lại. Gõ `0` (hoặc `q`, `exit`, `huỷ`, `cancel`) để huỷ.

Đường dẫn được nhận từ bất kỳ đoạn nào trong câu mà **là thư mục có thật**. Mức được nhận theo từ khoá trong câu:

| Mức | Từ khoá nhận diện | Nội dung |
|---|---|---|
| `map` | map, tổng quan, bản đồ, skeleton | Cây thư mục + skeleton (chữ ký hàm/class, docstring dòng đầu). Nhỏ nhất |
| `balanced` | balanced, cân bằng | Map + full code của vài file quan trọng nhất (ngân sách 8.000 token) |
| `full` | full, đầy đủ, toàn bộ | Toàn bộ code, đã bỏ comment và output notebook. Lớn nhất |

### 4.2. File đầu ra
File được ghi ở **thư mục bạn đang chạy `hub`**, tên theo mức:

| Mức | Tên file |
|---|---|
| map | `PROJECT_CONTEXT_map.md` |
| balanced | `PROJECT_CONTEXT_balanced.md` |
| full | `PROJECT_CONTEXT_full.md` |

Muốn ép mọi mức ghi vào **một file cố định** (hoặc thư mục khác), đặt `HUB_CONTEXT_FILE` trong `.env`:
```env
HUB_CONTEXT_FILE=D:\Trietdeptrai\contexts\agent-hub.md
```

### 4.3. Bên trong file có gì
1. **Overview:** thư viện khai báo, thư viện bên thứ ba thấy trong code, điểm vào (entry points), cây thư mục, sơ đồ import nội bộ (Mermaid).
2. **Files:** mỗi file có đánh dấu `[FULL]` (code đầy đủ, đã bỏ comment) hoặc `[SKELETON]` (chỉ chữ ký + số dòng `# L12`). File được xếp hạng theo độ "quan trọng" (được import nhiều, là entry point, tên chứa `model`/`config`/`core`...).

### 4.4. Pipeline bên trong
`discovery → parser → ranker → renderer`
- **discovery:** tìm file `.py` và `.ipynb` (bỏ `.git`, `.venv`, `node_modules`, `__pycache__`, `build`, `dist`...). Lấy thêm các file manifest (`README.md`, `requirements.txt`, `pyproject.toml`...).
- **parser:** phân tích bằng `ast` của Python (notebook: ghép các cell code, bỏ output).
- **ranker:** dựng đồ thị import và chấm điểm file.
- **renderer:** ghép Markdown, nâng skeleton lên full code trong giới hạn token của mức đã chọn.

### 4.5. Dùng như một công cụ độc lập
```powershell
python run_context.py                                  # hỏi từng bước (đường dẫn, mức)
python -m hub.agents.context.cli D:\projects\my_repo --mode map
python -m hub.agents.context.cli . --mode balanced --pin src/main.py --exclude "tests/*"
python -m hub.agents.context.cli . --mode full -o -    # in ra màn hình thay vì ghi file
```
Tuỳ chọn: `--budget <token>` (ghi đè ngân sách), `--pin <file>` (luôn gửi full file này), `--exclude <glob>` (bỏ file), `-o <tên file>`.

> **Giới hạn:** hiện chỉ hiểu **Python** (`.py`, `.ipynb`). Dự án JS/Java... sẽ không được liệt kê trong context.

---

## 5. Agent `qa`

Trả lời câu hỏi về dự án bằng Gemini. **Mỗi lần hỏi tốn ít nhất 1 request** (có thể 2–3 nếu phải đọc thêm code).

### 5.1. Quy trình cho mỗi câu hỏi
1. **Chọn file context** (theo thứ tự ưu tiên):
   1. Đường dẫn file `.md` bạn viết ngay trong câu hỏi (xem 5.2).
   2. Biến môi trường `HUB_CONTEXT_FILE`.
   3. File `PROJECT_CONTEXT*.md` **mới nhất** ở thư mục hiện tại.
   4. File `PROJECT_CONTEXT*.md` mới nhất trong các thư mục con.
2. **Gửi cho Gemini:** context + vài lượt hỏi–đáp gần nhất (trí nhớ) + câu hỏi.
3. **Nếu context chưa đủ**, Gemini xin đọc thêm code (xem 5.3). `qa` đọc từ ổ đĩa rồi hỏi lại, tối đa 2 lần mặc định.
4. **Trả lời** kèm trích dẫn `file:dòng` và dòng cuối ghi rõ **đã dùng file context nào** và **đã đọc thêm gì**:
   ```
   📄 Nguồn: `PROJECT_CONTEXT_full.md` + code bổ sung (src/hub/model_router.py::_invoke)
   ```

> Thứ tự chạy đúng là: **`/context` trước, `/qa` sau.** Chưa có file context thì `qa` chỉ báo "Chưa có PROJECT_CONTEXT.md" và không tốn quota.

### 5.2. Dùng file context ở chỗ khác
```
bạn> /qa đọc "C:\Users\LENOVO\Desktop\PROJECT_CONTEXT.md" rồi giải thích luồng supervisor
```
- Đường dẫn có dấu cách phải để trong dấu nháy.
- Nếu đường dẫn sai, `qa` báo ngay "Không tìm thấy file" và **không gọi Gemini**. Desktop của Windows nhiều khi nằm ở `...\OneDrive\Desktop\...`.
- Khi đọc thêm code, `qa` lấy thư mục dự án từ dòng `> Root:` trong file context đó, nên thư mục này phải còn tồn tại trên máy.

### 5.3. Cách `qa` đọc thêm code
Khi cần, Gemini trả về JSON (áp dụng cho mọi ngôn ngữ lập trình):
```json
{"need_file": "src/hub/model_router.py"}
{"need_file": "src/hub/model_router.py", "start": 60, "end": 120}
{"need_file": "src/hub/model_router.py", "function": "_invoke"}
{"need_file": "src/app.py", "function": "Server.handle"}
```
- Không chỉ định khoảng dòng → đọc 500 dòng đầu, kèm thông báo "còn N dòng, đọc tiếp bằng start". Có `start`/`end` → đọc đúng khoảng đó (giới hạn mềm khoảng 60.000 ký tự mỗi lần; vượt thì cũng báo để đọc tiếp).
- **Đọc hàm/class:** Python dùng `ast` (hỗ trợ `Class.method`, lấy cả decorator). JS/TS, Java, Kotlin, C/C++/C#, Go, Rust, PHP, Swift, Ruby, shell... dùng heuristic nên đôi khi cắt sai ở code lạ.
- **An toàn:** chỉ đọc được file **nằm trong thư mục dự án**. Mọi đường dẫn thoát ra ngoài (`../`, đường dẫn tuyệt đối khác) đều bị từ chối.
- Hết lượt đọc thêm mà Gemini vẫn đòi nữa → `qa` ép nó trả lời bằng thông tin hiện có.

### 5.4. Trí nhớ hội thoại
- `qa` nhớ **3 lượt hỏi–đáp gần nhất** (chỉnh bằng `HUB_QA_HISTORY_TURNS`; đặt `0` để tắt) để hiểu các câu như "còn hàm kia thì sao?".
- Mỗi lượt cũ bị cắt tối đa 1.500 ký tự và bỏ dòng nguồn để đỡ tốn token.
- Trí nhớ **chỉ tồn tại trong phiên đang chạy**. Tắt chương trình là mất. Gõ `/reset` để xoá chủ động.

### 5.5. Mẹo dùng đỡ tốn quota
- Dùng `/context map` rồi để `qa` tự đọc thêm khi cần, thay vì dùng `full` (file lớn gửi lại ở **mọi** lần gọi).
- Luôn gõ `/qa` để bỏ qua bước điều phối.
- Hỏi cụ thể ("hàm nào làm X?") thay vì hỏi chung chung.

---

## 6. Model, failover và quota

### 6.1. Model pool
Danh sách model thử **từ trên xuống**. Mặc định:
```
gemini-3.1-flash-lite, gemini-3.5-flash-lite, gemini-2.5-flash-lite, gemini-2.5-flash
```
Đổi bằng `HUB_MODELS` trong `.env` (cách nhau bằng dấu phẩy, **không** dấu cách thừa cũng được):
```env
HUB_MODELS=gemini-2.5-flash,gemini-2.5-flash-lite
```
Tên model phải đúng với tên mà Gemini API của bạn đang hỗ trợ.

### 6.2. Cơ chế failover
- Model gặp lỗi **có thể thử lại** (429, `RESOURCE_EXHAUSTED`, rate limit, quota exceeded, 500/502/503/504, timeout) → hub **bỏ qua model đó 1 giờ** và thử model kế tiếp.
- Lỗi khác (sai API key, sai tên model, nội dung bị chặn...) → **báo lỗi ngay**, không chuyển model.
- Lưu ý: mọi lỗi "có thể thử lại" đều khiến model nghỉ **cả giờ**, kể cả lỗi mạng thoáng qua.

### 6.3. Xem quota ước lượng với `/models`
```
bạn> /models
Model pool (ưu tiên từ trên xuống, ▶ = model sẽ được gọi tiếp theo):
  ▶ gemini-3.1-flash-lite        7 request
    gemini-3.5-flash-lite        0 request
    gemini-2.5-flash-lite        2 request   ✗ đang nghỉ, còn ~42 phút
    gemini-2.5-flash             0 request
```
- Số request tính **từ lúc mở chương trình** và chỉ gồm các lần gọi thành công (gồm cả request điều phối).
- Gemini **không trả về quota còn lại**, nên đây chỉ là con số để ước lượng. Giới hạn thật xem tại <https://aistudio.google.com> (mục usage/quota của tài khoản bạn).

---

## 7. Biến môi trường

Đặt trong file `.env` ở thư mục gốc repo (hoặc biến môi trường hệ thống).

| Biến | Bắt buộc | Mặc định | Ý nghĩa |
|---|---|---|---|
| `GOOGLE_API_KEY` hoặc `GEMINI_API_KEY` | **Có** (khi dùng LLM) | — | API key Gemini |
| `HUB_MODELS` | Không | 4 model ở mục 6.1 | Danh sách model, thử từ trái sang phải |
| `HUB_CONTEXT_FILE` | Không | — | Ép `context` ghi vào, và `qa` đọc từ, một file cố định |
| `HUB_QA_MAX_READS` | Không | `2` | Số lần `qa` được xin đọc thêm code cho mỗi câu hỏi |
| `HUB_QA_HISTORY_TURNS` | Không | `3` | Số lượt hỏi–đáp `qa` nhớ; `0` = tắt |

Ví dụ `.env` đầy đủ:
```env
GOOGLE_API_KEY=AIza...
HUB_MODELS=gemini-2.5-flash,gemini-2.5-flash-lite
HUB_QA_MAX_READS=3
HUB_QA_HISTORY_TURNS=4
```

---

## 8. Cấu trúc thư mục

```text
agent-hub/
├── src/hub/
│   ├── cli.py            # vòng chat: lệnh /qa /context /models /reset, hiển thị model
│   ├── graph.py          # dựng đồ thị: START → supervisor → agent → END
│   ├── supervisor.py     # chọn agent (thẳng nếu có /agent, hoặc nhờ LLM)
│   ├── model_router.py   # failover giữa các model + đếm request (UsageTracker)
│   ├── llm.py            # tạo các model Gemini từ MODEL_POOL
│   ├── config.py         # TEMPERATURE, MODEL_POOL, đọc .env
│   ├── registry.py       # NƠI DUY NHẤT đăng ký agent
│   ├── state.py          # HubState (messages, next, forced)
│   └── agents/
│       ├── base.py       # AgentSpec(name, description, node)
│       ├── context/      # agent tạo context (không LLM)
│       │   ├── node.py       # lớp bọc cho hub: hỏi đường dẫn/mức, ghi file
│       │   ├── graph.py      # pipeline discovery→parser→ranker→renderer
│       │   ├── config.py     # MODES, tên file đầu ra, danh sách bỏ qua
│       │   ├── cli.py        # chạy độc lập (argparse)
│       │   ├── nodes/        # discovery, parser, ranker, renderer
│       │   └── tools/        # files, pyparse, notebook, tokens
│       └── qa/           # agent hỏi đáp (Gemini)
│           ├── node.py       # luồng chính, trí nhớ, chọn file context
│           ├── tools.py      # đọc file/khoảng dòng/hàm, chặn đường dẫn ngoài dự án
│           └── prompts.py    # system prompt và hướng dẫn định dạng JSON
├── tests/                # test_hub, test_context_agent, test_qa_agent, test_qa_upgrade
├── run_context.py        # tạo context nhanh bằng menu (không qua hub)
├── visual_langgraph.py   # xuất sơ đồ đồ thị ra hub_graph.png (cần internet)
├── pyproject.toml
└── .env                  # KHÔNG commit
```

Luồng một tin nhắn:
```
bạn gõ → cli.py ─ tách "/agent" ─→ graph.py
          supervisor ─ có /agent?  → chọn thẳng
                     ─ chỉ 1 agent? → chọn luôn
                     ─ ngược lại   → 1 request LLM để chọn
          agent chạy → trả lời → cli in "<model hoặc agent>> câu trả lời"
```

---

## 9. Chạy test

```powershell
pytest                       # tất cả
pytest tests/test_hub.py     # một file
pytest -k history            # theo tên
```
Test dùng LLM giả (mock), **không cần API key** và không tốn quota.

---

## 10. Thêm agent mới

1. Tạo thư mục `src/hub/agents/<tên>/` với `__init__.py` khai báo `SPEC`:
   ```python
   from ..base import AgentSpec
   from .node import my_node

   SPEC = AgentSpec(
       name='my_agent',
       description='Một câu rõ ràng: khi nào nên chọn agent này và khi nào KHÔNG nên.',
       node=my_node,
   )
   ```
2. `my_node(state) -> dict` đọc `state['messages']` và trả `{'messages': [AIMessage(...)]}`. Cần hỏi người dùng giữa chừng thì dùng `langgraph.types.interrupt(...)`.
3. Đăng ký trong `src/hub/registry.py`:
   ```python
   from .agents.my_agent import SPEC as my_spec
   AGENTS = [qa_spec, context_spec, my_spec]
   ```
4. Xong. Lệnh `/my_agent <nội dung>` tự có hiệu lực.

Lưu ý:
- **Mô tả (`description`) quyết định LLM điều phối chọn đúng hay sai.** Viết rõ khi nào dùng và khi nào **không** dùng.
- **Agent đứng đầu danh sách** là agent mặc định khi LLM điều phối không chắc.
- Tên agent càng ngắn càng dễ gõ (`/qa` dễ hơn `/project_question_answering`).

---

## 11. Xử lý lỗi thường gặp

| Triệu chứng | Nguyên nhân & cách xử lý |
|---|---|
| `RuntimeError: Chưa có GOOGLE_API_KEY hoặc GEMINI_API_KEY.` | Chưa tạo `.env`, hoặc đang chạy `hub` ở thư mục khác. Chạy từ thư mục gốc repo hoặc đặt biến môi trường hệ thống |
| `hub` không nhận lệnh | Chưa kích hoạt môi trường, hoặc chưa `pip install -e .`. Thử `python -m hub.cli` |
| `ImportError: cannot import name 'MODEL_POOL' from 'hub.config'` | File `src/hub/config.py` bị ghi đè nhầm. Khôi phục bằng `git checkout -- src/hub/config.py` rồi kiểm tra có dòng `MODEL_POOL = ...` |
| `⚠️ Chưa có PROJECT_CONTEXT.md` | Chưa tạo context hoặc đang đứng ở thư mục khác. Chạy `/context ...` trước, hoặc đặt `HUB_CONTEXT_FILE` |
| `⚠️ Không tìm thấy file: ...` khi hỏi `qa` kèm đường dẫn | Đường dẫn sai. Copy lại đường dẫn thật từ File Explorer; Desktop có thể nằm trong OneDrive |
| `hub> ⚠ Tất cả model trong MODEL_POOL đều không khả dụng` | Mọi model đang hết quota hoặc lỗi. Gõ `/models` xem model nào đang nghỉ; đợi hoặc thêm model vào `HUB_MODELS` |
| Báo lỗi 404 / "model not found" rồi chương trình dừng | Tên model trong `HUB_MODELS` sai hoặc chưa được tài khoản hỗ trợ (lỗi này không tự chuyển model). Sửa lại tên |
| Hỏi bình thường (không `/qa`) mà bị hỏi đường dẫn | LLM điều phối chọn nhầm `context`. Gõ `0` để huỷ rồi dùng `/qa ...` |
| `/qa` hỏi mà trả lời theo dự án khác | Đang có nhiều file `PROJECT_CONTEXT*.md`; `qa` lấy file **mới nhất**. Xem dòng `📄 Nguồn:` cuối câu trả lời, hoặc chỉ định file bằng đường dẫn trong câu hỏi |
| Tiếng Việt hiển thị lỗi font trong PowerShell | Chạy `chcp 65001` và `$env:PYTHONUTF8=1` rồi chạy lại |
| `pytest` báo lỗi import khi collect | Chưa `pip install -e .`, hoặc đang dùng sai môi trường |

---

## 12. Giới hạn hiện tại

- `context` chỉ hiểu **Python** (`.py`, `.ipynb`).
- `qa` **chỉ đọc**: không sửa file, không chạy code, không tìm kiếm toàn dự án (chưa có grep) và không liệt kê thư mục.
- Mỗi câu hỏi `qa` chỉ đọc thêm tối đa 2 lần (chỉnh bằng `HUB_QA_MAX_READS`), nên câu hỏi cần lần theo nhiều file có thể thiếu.
- Phần skeleton trong context sẽ **cũ** nếu bạn sửa code sau khi tạo context. Hãy chạy lại `/context`.
- Bộ đếm request chỉ là ước lượng trong một phiên chạy; Gemini không cho biết quota còn lại.
- Trí nhớ hội thoại mất khi tắt chương trình.
- Việc chọn agent bằng LLM (khi không gõ `/agent`) có thể sai; dùng `/qa`, `/context` để chắc chắn.
