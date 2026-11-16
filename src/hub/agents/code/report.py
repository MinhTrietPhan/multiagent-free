"""User-facing text: approval prompt, per-file diff pages, final report, and the approval vocabulary.

The approval prompt is a plain string passed to `interrupt()`: the CLI prints it and Telegram sends it
(split at ~3900 chars), so the same flow works on both without interface changes. The prompt stays
well below the Telegram limit and long diffs are paged per file (`diff N`) so code fences never get cut.
"""
from __future__ import annotations

import re

from .tools import display_command

YES = {"y", "yes", "ok", "oke", "okay", "có", "co", "đồng ý", "dong y", "duyệt", "duyet",
       "approve", "apply", "áp dụng", "ap dung"}
PAGE_CHARS = 3300
PROMPT_CHARS = 3500


def parse_approval(answer) -> tuple[str, int | None]:
    """Strict: anything that is not an explicit yes (or a diff request) means NO."""
    text = str(answer or "").strip().casefold().strip(".! ")
    if text in YES:
        return "yes", None
    match = re.fullmatch(r"diff(?:\s+(\d+))?", text)
    if match:
        return "diff", int(match.group(1)) if match.group(1) else 1
    return "no", None


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _fence(text: str) -> str:
    return "```diff\n" + text.replace("```", "'''") + "\n```"


def _preview(diff_text: str, budget: int) -> tuple[str, int]:
    """Whole lines of the diff up to `budget` chars; returns (preview, hidden line count)."""
    lines = diff_text.split("\n")
    out, size = [], 0
    for index, line in enumerate(lines):
        if size + len(line) + 1 > budget:
            return "\n".join(out), len(lines) - index
        out.append(line)
        size += len(line) + 1
    return "\n".join(out), 0


def render_approval_prompt(state: dict) -> str:
    proposal = state.get("proposal") or {}
    attempt = state.get("repair_attempts", 0)
    rnd = state.get("round", 1)
    tag = (f" · vòng {rnd}" if rnd > 1 else "") + (f" · sửa lỗi lần {attempt}" if attempt else "")
    head = ["CODE AGENT · cần duyệt trước khi sửa" + tag, f"Project: {state.get('project_root', '')}", ""]
    if rnd > 1 and state.get("round_goal"):
        head += ["Việc của vòng này: " + _clip(state["round_goal"], 240), ""]
    if proposal.get("analysis"):
        head += ["Phân tích: " + _clip(proposal["analysis"], 400), ""]
    plan = proposal.get("plan") or []
    if plan:
        head.append("Kế hoạch:")
        head += [f"{i}. {_clip(step, 160)}" for i, step in enumerate(plan[:8], 1)]
        head.append("")
    risks = proposal.get("risks") or []
    if risks:
        head.append("Rủi ro: " + _clip("; ".join(risks[:4]), 300))
        head.append("")
    head.append("File bị ảnh hưởng:")
    for change in (state.get("changes") or [])[:12]:
        mark = "+" if change["action"] == "create" else "~"
        head.append(f"  {mark} {change['path']} (+{change['added']}/-{change['removed']})")
    for warning in (state.get("warnings") or [])[:5]:
        head.append(f"Cảnh báo: {_clip(warning, 200)}")
    plan_checks = state.get("checks_plan") or []
    head.append("")
    head.append("Lệnh sẽ chạy: " + ("; ".join(display_command(c) for c in plan_checks)
                                    if plan_checks else "(không chạy kiểm tra tự động)"))
    footer = ["", "Trả lời: y = áp dụng · n = huỷ · diff [số file] = xem diff đầy đủ. "
                  "Nội dung khác = huỷ, không file nào bị sửa."]
    fixed = "\n".join(head + footer)
    budget = max(600, PROMPT_CHARS - len(fixed) - 120)
    preview, hidden = _preview(state.get("diff_text", ""), budget)
    note = f"\n(rút gọn, còn {hidden} dòng — gõ `diff` để xem từng file)" if hidden else ""
    return "\n".join([*head, "", "Diff:", _fence(preview) + note, *footer])


def render_diff_page(state: dict, number: int | None) -> str:
    changes = state.get("changes") or []
    if not changes:
        return "Không có diff để hiển thị. Trả lời y / n."
    index = min(max((number or 1), 1), len(changes)) - 1
    change = changes[index]
    preview, hidden = _preview(change["diff"], PAGE_CHARS)
    note = f"\n(còn {hidden} dòng nữa chưa hiển thị)" if hidden else ""
    return (f"Diff file {index + 1}/{len(changes)}: {change['path']}\n{_fence(preview)}{note}\n\n"
            "Trả lời: y = áp dụng · n = huỷ · diff <số> = xem file khác. Nội dung khác = huỷ.")


def render_report(state: dict, git_stat: str | None = None) -> str:
    applied = state.get("applied_all") or []
    results = state.get("check_results") or []
    created = [a["path"] for a in applied if a["action"] == "create"]
    modified = [a["path"] for a in applied if a["action"] == "modify"]
    failed = [r for r in results if not r["ok"]]
    error = state.get("error")

    if error and not applied:
        title = "Code Agent: không hoàn tất"
    elif error:
        title = "Code Agent: dừng giữa chừng (một số file đã được sửa)"
    elif state.get("stopped"):
        title = "Code Agent: hoàn tất phần đã làm (bạn chọn dừng ở câu hỏi tiếp theo)"
    elif state.get("cancelled"):
        title = "Code Agent: đã huỷ" if not applied else "Code Agent: đã huỷ đợt sửa lỗi (đợt trước vẫn còn hiệu lực)"
    elif failed:
        title = "Code Agent: đã áp dụng nhưng kiểm tra CHƯA đạt"
    elif state.get("need_repair"):
        title = "Code Agent: không tạo được patch hợp lệ"
    else:
        title = "Code Agent: hoàn tất"

    lines = [title, f"Project: {state.get('project_root', '(chưa xác định)')}", ""]
    if error:
        lines += [_clip(error, 600), ""]
    elif state.get("need_repair") and state.get("last_errors"):
        lines += ["Lý do:"] + [f"- {e[:500]}" for e in state["last_errors"][:3]]
        if any("không khớp nguyên văn" in e for e in state["last_errors"]):
            lines += ["", "Gợi ý: model chép sai đoạn neo. Thử tăng `/code config max_context_chars`, dùng model mạnh hơn "
                          "(HUB_MODEL_CODE_STRONG hoặc `/code config model_strategy strong`), hoặc nêu rõ tên hàm/class cần chèn gần đó.", ""]
        else:
            lines.append("")
    if state.get("cancelled") and not applied:
        lines += ["Không có file nào bị sửa.", ""]
    if created:
        lines += ["Đã tạo: " + ", ".join(created)]
    if modified:
        lines += ["Đã sửa: " + ", ".join(modified)]
    if applied:
        lines.append("")
    log = state.get("summary_log") or []
    plan = (state.get("proposal") or {}).get("plan") or []
    if log and applied:
        lines += ["Các bước đã làm:"] + [f"- Vòng {e['round']}: {_clip(e['summary'], 200)} ({', '.join(e['files'])})"
                                         for e in log] + [""]
    elif plan and applied:
        lines += ["Thay đổi chính:"] + [f"- {_clip(p, 160)}" for p in plan[:6]] + [""]
    if results:
        lines.append("Kiểm tra:")
        for r in results:
            status = "bỏ qua" if r.get("skipped") else ("OK" if r["ok"] else "LỖI")
            lines.append(f"- [{status}] {r['cmd']}")
        lines.append("")
    for r in results:
        if r.get("skipped") and "python_path" in r.get("output", ""):
            lines += [_clip(r["output"], 400), ""]
            break
    if failed:
        lines += ["Vấn đề còn tồn tại:", _clip(failed[0]["output"][-700:], 700), ""]
    if git_stat:
        lines += ["Git diff --stat:", git_stat, ""]
    if applied:
        lines.append("Xem chi tiết: git diff -- " + " ".join((created + modified)[:6]))
    for backup in (state.get("backup_dirs") or [])[-1:]:
        lines.append(f"Bản gốc đã backup tại: {backup}")
    notes = state.get("notes") or []
    for note in notes[:4]:
        lines.append(f"Lưu ý: {_clip(note, 240)}")
    lines.append(f"Tool calls: {state.get('tool_calls', 0)}/{(state.get('settings') or {}).get('max_tool_calls', '?')}")
    return "\n".join(lines).strip()
