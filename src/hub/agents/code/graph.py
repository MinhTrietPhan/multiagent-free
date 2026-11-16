"""LangGraph workflow of the code agent (compiled subgraph, used directly as the AgentSpec node).

entry -> resolve -> gather(+explore) -> propose -> validate -> approval -> execute(+review) -> report
              ^______ repair loop ______|                                     |
              ^______ next round (continue) ___________________________________|
              ^______ ask -> user answers -> next round ________________________|

Why a subgraph: LangGraph re-runs an interrupted *node* from its first line on resume. As a plain
function that called Gemini before `interrupt()`, the agent would call Gemini again after the user
approves and could apply a different patch than the one shown. Here `approval` and `ask` are their own
nodes (deterministic, no LLM), so resume replays only them and the approved patch is the stored one.

Autonomy: after each applied patch the review step looks for the related work that is still missing
(registering a model, config, data loader...) and either continues, or asks the user one question.
Every patch is still shown for approval (approve mode), and rounds/passes/tool calls are bounded.
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from ...memory.store import data_dir
from ..qa.project import resolve_project_root
from .prompts import Explore, Proposal, Review, build_explore_prompt, build_prompt, build_review_prompt
from .report import parse_approval, render_approval_prompt, render_diff_page, render_report
from .security import in_workspace
from .settings import CodeSettings, handle_config_command, load_settings_checked
from .state import CodeState
from .tools import (ToolBudget, apply_changes, build_changes, build_context, classify_request,
                    git_dirty_paths, git_stat, plan_checks, read_more, run_checks, summarize_failures)

MAX_DIFF_VIEWS = 8
# One invocation of the graph is limited to 25 steps. Without a human pause (auto mode) a pass costs 5 nodes,
# so passes are capped; in approve mode every pass ends at an approval pause, which restarts the step counter.
MAX_PASSES_AUTO = 4
MAX_PASSES_APPROVE = 8
STOP_WORDS = {"n", "no", "dừng", "dung", "stop", "thôi", "thoi", "kết thúc", "ket thuc", "0", "huỷ", "huy", "cancel"}
RESET = {
    "request": "", "base_request": "", "round_goal": "", "kind": "", "project_root": "", "tool_calls": 0,
    "repair_attempts": 0, "round": 1, "passes": 0, "context": "", "context_files": [], "touched": [],
    "read_next": [], "summary_log": [], "next_action": "", "pending_question": "", "stopped": False,
    "feedback": "", "model_key": "", "proposal": {}, "changes": [], "diff_text": "", "warnings": [],
    "checks_plan": [], "approved": False, "cancelled": False, "applied_all": [], "backup_dirs": [],
    "check_results": [], "need_repair": False, "last_errors": [], "notes": [], "done": False, "error": "",
}


def _last_user_text(state) -> str:
    for message in reversed(state.get("messages", [])):
        if getattr(message, "type", "") == "human":
            return message.content if isinstance(message.content, str) else str(message.content)
    return ""


def _settings(state) -> CodeSettings:
    return CodeSettings(**state["settings"])


def choose_model_key(strategy: str, attempt: int, kind: str, explicit_files: int) -> str:
    """cheap -> "code"; strong -> "code_strong"; hybrid upgrades only when the task needs it."""
    if strategy == "strong":
        return "code_strong"
    if strategy == "hybrid" and (attempt > 0 or kind in {"refactor", "debug"} or explicit_files >= 3):
        return "code_strong"
    return "code"


def _default_llm_factory(key: str):
    from ...model_router import get_llm  # lazy: importing the package must not need Gemini
    return get_llm(key)  # reads HUB_MODEL_CODE / HUB_MODEL_CODE_STRONG, falls back to MODEL_POOL


def _pass_cap(settings: CodeSettings) -> int:
    return MAX_PASSES_AUTO if settings.approval_mode == "auto" else MAX_PASSES_APPROVE


def _follow_up_request(state, goal: str, extra: str = "") -> str:
    log = "\n".join(f"- Vòng {e['round']}: {e['summary']} ({', '.join(e['files'])})" for e in state.get("summary_log", []))
    parts = [state["base_request"], "[Đã hoàn thành ở các vòng trước]\n" + log]
    if extra:
        parts.append(extra)
    parts.append(f"[Việc cần làm ở vòng {state.get('round', 1)}]\n{goal}")
    return "\n\n".join(parts)


def build_code_graph(llm_factory=None, backup_root: Path | None = None):
    def make_llm(key: str):
        factory = llm_factory or _default_llm_factory
        try:
            return factory(key)
        except TypeError:  # factories that take no argument (same convention as the supervisor)
            return factory()

    def ask_model(key: str, schema, prompt: str):
        result = make_llm(key).with_structured_output(schema).invoke(prompt)
        return result if isinstance(result, schema) else schema(**(result or {}))

    # ---- nodes -------------------------------------------------------------------------------
    def entry(state: CodeState) -> dict:
        text = _last_user_text(state)
        reply = handle_config_command(text)
        if reply is not None:
            return {**RESET, "done": True, "messages": [AIMessage(content=reply, name="code")]}
        settings, warnings = load_settings_checked()
        return {**RESET, "request": text, "base_request": text, "kind": classify_request(text),
                "settings": asdict(settings), "notes": warnings}

    def resolve(state: CodeState) -> dict:
        try:
            root = resolve_project_root(state, state["request"])
        except (InterruptedError, FileNotFoundError, OSError) as exc:
            return {"error": str(exc) or "Không xác định được project root."}
        if not in_workspace(root):
            return {"error": f"Project nằm ngoài HUB_WORKSPACE_ROOTS: {root}. Code Agent chỉ sửa code trong "
                             "các workspace đã khai báo (đặt HUB_WORKSPACE_ROOTS trong .env)."}
        return {"project_root": str(root), "active_project_root": str(root)}

    def gather(state: CodeState) -> dict:
        update: dict = {}
        attempts = state.get("repair_attempts", 0)
        if state.get("need_repair"):
            attempts += 1
            update.update({"repair_attempts": attempts, "need_repair": False})
        update["passes"] = state.get("passes", 0) + 1
        settings = _settings(state)
        root = Path(state["project_root"])
        budget = ToolBudget(settings.max_tool_calls, state.get("tool_calls", 0))
        explore = settings.explore_rounds > 0 and not state.get("feedback")   # repairs reuse what is known
        limit = int(settings.max_context_chars * (0.75 if explore else 1.0))
        context, shown, notes = build_context(root, state["request"], state.get("touched", []), limit, budget,
                                              extra=state.get("read_next", []))
        if explore:
            for _ in range(settings.explore_rounds):
                if not budget.try_spend():
                    break
                try:
                    wanted = ask_model("code", Explore, build_explore_prompt(state["request"], context)).read
                except Exception as exc:  # exploring is optional: never fatal
                    notes.append(f"Bỏ qua bước khám phá ({type(exc).__name__}).")
                    break
                wanted = [w for w in dict.fromkeys(wanted) if w not in shown][:4]
                if not wanted:
                    break
                added, got = read_more(root, wanted, settings.max_context_chars - len(context), budget)
                if not got:
                    break
                context += "\n\n" + added
                shown += got
        update.update({"context": context, "context_files": shown, "tool_calls": budget.used,
                       "read_next": [], "notes": [*state.get("notes", []), *notes]})
        return update

    def propose(state: CodeState) -> dict:
        settings = _settings(state)
        budget = ToolBudget(settings.max_tool_calls, state.get("tool_calls", 0))
        if not budget.try_spend():
            return {"error": f"Vượt giới hạn max_tool_calls={settings.max_tool_calls}; dừng để tránh tốn token."}
        key = choose_model_key(settings.model_strategy, state.get("repair_attempts", 0),
                               state.get("kind", ""), len(state.get("context_files", [])))
        prompt = build_prompt(state["request"], state.get("kind", ""), state.get("context", ""),
                              state.get("feedback", ""), state.get("repair_attempts", 0))
        try:
            proposal = ask_model(key, Proposal, prompt)
        except RuntimeError as exc:  # ModelRouter: quota / no model available
            return {"error": f"Gemini không khả dụng: {exc}", "tool_calls": budget.used}
        except Exception as exc:
            return {"error": f"Không nhận được đề xuất hợp lệ từ Gemini ({type(exc).__name__}: {exc})",
                    "tool_calls": budget.used}
        return {"proposal": proposal.model_dump(), "model_key": key, "tool_calls": budget.used}

    def validate(state: CodeState) -> dict:
        proposal = state.get("proposal") or {}
        edits = proposal.get("edits") or []
        if not edits:
            reason = proposal.get("analysis") or "Gemini không đề xuất chỉnh sửa nào."
            return {"error": f"Không có chỉnh sửa để áp dụng. {reason}"}
        root = Path(state["project_root"])
        changes, errors, warnings = build_changes(root, edits)
        if errors:
            feedback = "Patch bị Python từ chối:\n" + "\n".join(f"- {e}" for e in errors)
            return {"need_repair": True, "feedback": feedback, "last_errors": errors, "changes": []}
        settings = _settings(state)
        budget = ToolBudget(settings.max_tool_calls, state.get("tool_calls", 0))
        if budget.try_spend():
            dirty = git_dirty_paths(root) or set()
            for change in changes:
                if change["action"] == "modify" and change["path"] in dirty:
                    warnings.append(f"{change['path']} đang có thay đổi chưa commit trong git.")
        checks = (plan_checks(root, [c["path"] for c in changes], settings.python_path)
                  if settings.auto_run_tests else [])
        return {"changes": changes, "diff_text": "\n".join(c["diff"] for c in changes),
                "warnings": warnings, "checks_plan": checks, "need_repair": False, "last_errors": [],
                "tool_calls": budget.used}

    def approval(state: CodeState) -> dict:
        if _settings(state).approval_mode == "auto":
            return {"approved": True}
        page = render_approval_prompt(state)
        for _ in range(MAX_DIFF_VIEWS + 1):
            verdict, number = parse_approval(interrupt(page))
            if verdict == "yes":
                return {"approved": True}
            if verdict == "diff":
                page = render_diff_page(state, number)
                continue
            break
        return {"approved": False, "cancelled": True}

    def review(state: CodeState, settings: CodeSettings, budget: ToolBudget, update: dict) -> None:
        """Look for related work still missing; sets update['next_action'] and the next round's request."""
        if state.get("round", 1) >= settings.max_rounds or state.get("passes", 0) >= _pass_cap(settings):
            return
        if not budget.try_spend():
            return
        checks = "\n".join(f"[{'bỏ qua' if c.get('skipped') else ('OK' if c['ok'] else 'LỖI')}] {c['cmd']}"
                           for c in update.get("check_results", [])) or "(không chạy kiểm tra)"
        summary = (state.get("proposal") or {}).get("analysis", "")[:300]
        prompt = build_review_prompt(state["base_request"], summary, state.get("diff_text", "")[:6000], checks,
                                     state.get("context", ""))
        try:
            result = ask_model("code", Review, prompt)
        except Exception as exc:  # review is optional: never fatal
            update["notes"] = [*update.get("notes", state.get("notes", [])), f"Bỏ qua bước rà soát ({type(exc).__name__})."]
            return
        update["summary_log"][-1]["summary"] = (result.summary or summary or "đã áp dụng patch")[:200]
        read = [r for r in result.read if r][:4]
        next_round = state.get("round", 1) + 1
        if result.status == "continue" and result.next_task.strip():
            update.update({"next_action": "continue", "round": next_round, "repair_attempts": 0, "feedback": "",
                           "read_next": read, "round_goal": result.next_task.strip()[:400]})
            update["request"] = _follow_up_request({**state, **update}, result.next_task.strip())
        elif result.status == "ask" and result.question.strip():
            update.update({"next_action": "ask", "pending_question": result.question.strip()[:600], "read_next": read})

    def execute(state: CodeState) -> dict:
        settings = _settings(state)
        root = Path(state["project_root"])
        budget = ToolBudget(settings.max_tool_calls, state.get("tool_calls", 0))
        if not budget.try_spend():
            return {"error": f"Vượt giới hạn max_tool_calls={settings.max_tool_calls} trước khi áp dụng patch; chưa sửa file nào."}
        result = apply_changes(root, state["changes"], backup_root or data_dir() / "code-agent" / "backups")
        if not result.ok:
            return {"error": result.error, "tool_calls": budget.used}
        merged = {a["path"]: dict(a) for a in state.get("applied_all", [])}
        for item in result.applied:
            if item["path"] in merged:  # created earlier in this run, edited again: still "create"
                merged[item["path"]]["added"] += item["added"]
                merged[item["path"]]["removed"] += item["removed"]
            else:
                merged[item["path"]] = item
        log = [*state.get("summary_log", []), {
            "round": state.get("round", 1), "files": [i["path"] for i in result.applied],
            "summary": ((state.get("proposal") or {}).get("analysis") or "đã áp dụng patch")[:200]}]
        update = {"applied_all": list(merged.values()), "tool_calls": budget.used, "summary_log": log,
                  "touched": list(dict.fromkeys([*state.get("touched", []), *merged])),
                  "backup_dirs": [*state.get("backup_dirs", []), *([result.backup_dir] if result.backup_dir else [])],
                  "approved": False, "check_results": [], "need_repair": False, "next_action": ""}
        if settings.auto_run_tests:
            checks = run_checks(root, state.get("checks_plan") or plan_checks(root, [i["path"] for i in result.applied],
                                                                              settings.python_path),
                                settings.command_timeout, budget, settings.python_path)
            update["check_results"] = checks
            update["tool_calls"] = budget.used
            if any(not c["ok"] for c in checks):
                update.update({"need_repair": True, "feedback": "Kiểm tra thất bại sau khi áp dụng patch:\n"
                                                               + summarize_failures(checks),
                               "last_errors": ["Kiểm tra thất bại"]})
                return update
        review(state, settings, budget, update)
        update["tool_calls"] = budget.used
        return update

    def ask(state: CodeState) -> dict:
        question = state.get("pending_question", "")
        answer = str(interrupt(f"CODE AGENT hỏi bạn:\n{question}\n\n"
                               "Trả lời bằng chữ để tiếp tục · gõ `dừng` để kết thúc tại đây (phần đã làm vẫn giữ).")).strip()
        if not answer or answer.casefold() in STOP_WORDS:
            return {"stopped": True, "next_action": ""}
        update = {"round": state.get("round", 1) + 1, "repair_attempts": 0, "feedback": "", "next_action": "",
                  "round_goal": f"Trả lời câu hỏi của agent: {answer[:300]}"}
        update["request"] = _follow_up_request({**state, **update}, f"Hãy hoàn thành phần còn lại dựa trên câu trả lời.",
                                               extra=f"[Agent hỏi] {question}\n[Người dùng trả lời] {answer}")
        return update

    def report(state: CodeState) -> dict:
        root = state.get("project_root")
        stat = git_stat(Path(root), [a["path"] for a in state.get("applied_all", [])]) if root else None
        update: dict = {"done": True, "messages": [AIMessage(content=render_report(state, stat), name="code")]}
        if root:
            update["active_project_root"] = root
        return update

    # ---- routing -------------------------------------------------------------------------------
    def can_repair(state) -> bool:
        settings = _settings(state)
        return (state.get("repair_attempts", 0) < settings.max_repair_attempts
                and state.get("passes", 0) < _pass_cap(settings))

    def after_entry(state):
        return END if state.get("done") else "resolve"

    def after_resolve(state):
        return "report" if state.get("error") else "gather"

    def after_propose(state):
        return "report" if state.get("error") else "validate"

    def after_validate(state):
        if state.get("error"):
            return "report"
        if state.get("need_repair"):
            return "gather" if can_repair(state) else "report"
        return "approval"

    def after_approval(state):
        return "execute" if state.get("approved") else "report"

    def after_execute(state):
        if state.get("error"):
            return "report"
        if state.get("need_repair"):
            return "gather" if can_repair(state) else "report"
        if state.get("next_action") == "continue":
            return "gather"
        if state.get("next_action") == "ask":
            return "ask"
        return "report"

    def after_ask(state):
        return "report" if state.get("stopped") else "gather"

    g = StateGraph(CodeState)
    for name, fn in (("entry", entry), ("resolve", resolve), ("gather", gather), ("propose", propose),
                     ("validate", validate), ("approval", approval), ("execute", execute), ("ask", ask),
                     ("report", report)):
        g.add_node(name, fn)
    g.add_edge(START, "entry")
    g.add_conditional_edges("entry", after_entry, {END: END, "resolve": "resolve"})
    g.add_conditional_edges("resolve", after_resolve, {"report": "report", "gather": "gather"})
    g.add_edge("gather", "propose")
    g.add_conditional_edges("propose", after_propose, {"report": "report", "validate": "validate"})
    g.add_conditional_edges("validate", after_validate, {"report": "report", "gather": "gather", "approval": "approval"})
    g.add_conditional_edges("approval", after_approval, {"execute": "execute", "report": "report"})
    g.add_conditional_edges("execute", after_execute, {"report": "report", "gather": "gather", "ask": "ask"})
    g.add_conditional_edges("ask", after_ask, {"report": "report", "gather": "gather"})
    g.add_edge("report", END)
    return g.compile()
