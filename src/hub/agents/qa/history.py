"""Conversation memory for the QA agent: one budget, three layers.

A. **Verbatim** — the newest ``HUB_QA_VERBATIM_TURNS`` turns, kept in full (with a per-answer
   safety cap) so follow-ups like "cái đó", "mục 2", "sửa tiếp" still point at real text.
B. **Digest** — older turns reduced deterministically to a question line plus the informative
   lines (paths, ``file:dòng`` citations, numbers, bullets). No LLM call, so it is free and
   stable in tests.
C. **Durable memory** — facts that must survive many turns or a restart belong in
   ``<project>/.hub/project_memory.md`` (read by QA) or ``~/.agent-hub/user_memory.md``,
   not in the rolling window.

Every layer shares ``HUB_QA_HISTORY_CHARS`` (default 8000 chars); turns are added
newest-first until the budget is spent, and the transcript never ends mid-line.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from langchain_core.messages import HumanMessage

QA_NAME = "qa"

DEFAULT_TOTAL_CHARS = 8000
DEFAULT_MAX_TURNS = 12
DEFAULT_VERBATIM_TURNS = 2
DEFAULT_VERBATIM_CHARS = 2500      # per answer in the verbatim window
DEFAULT_DIGEST_CHARS = 320         # per answer in the digest window
DEFAULT_QUESTION_CHARS = 240

_HEADER_RESERVE = 220              # header + footer + separators
_CODE_FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
_FOOTER = "\n\n---\n"
_INFORMATIVE = re.compile(
    r"`[^`]+`"                                      # inline code / path
    r"|\b[\w./\\-]+\.(?:py|js|ts|tsx|jsx|go|rs|java|kt|cs|rb|php|md|toml|json|ya?ml)\b"
    r"|:L\d+"                                       # file:L12 citations
    r"|\b\d{2,}\b"                                  # numbers that matter
)


@dataclass
class Turn:
    question: str
    answers: list[tuple[str, str]] = field(default_factory=list)


def _int_env(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def _clip(text: str, limit: int) -> str:
    """Cut at a word boundary so a line never ends in the middle of a word."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text.rfind(" ", 0, max(1, limit - 1))
    return text[: cut if cut > limit // 2 else max(1, limit - 1)].rstrip(" ,;:") + " …"


def _clip_keep(text: str, limit: int) -> str:
    """Trim at a line (then word) boundary, keeping the original line structure."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, limit)
    if cut < limit // 2:
        cut = text.rfind(" ", 0, limit)
    if cut < limit // 2:
        cut = limit
    return text[:cut].rstrip() + "\n…(cắt bớt)"


def _strip_footer(text: str) -> str:
    """Drop the "---\\n📂 Project: …" block QA appends to every answer."""
    return text.split(_FOOTER, 1)[0]


def _squeeze_code(text: str) -> str:
    """Replace code blocks with a size marker: the digest needs facts, not source."""
    def repl(match: re.Match) -> str:
        lines = [line for line in match.group(1).splitlines() if line.strip()]
        if len(lines) <= 2:
            return " " + " ".join(line.strip() for line in lines) + " "
        return f" [code {len(lines)} dòng] "

    return _CODE_FENCE.sub(repl, text)


def _informative(line: str) -> bool:
    return bool(_INFORMATIVE.search(line))


def digest_answer(text: str, limit: int = DEFAULT_DIGEST_CHARS) -> str:
    """Extractive summary: keep the lead sentence and the lines carrying facts."""
    body = _squeeze_code(_strip_footer(text))
    lines = [" ".join(line.split()) for line in body.splitlines()]
    lines = [line.lstrip("#>*- ").strip() for line in lines if line.strip()]
    if not lines:
        return ""
    picked: list[str] = []
    budget = limit
    for index, line in enumerate(lines):
        if index == 0 or _informative(line):
            piece = _clip(line, max(40, budget))
            if not piece or piece in picked:
                continue
            picked.append(piece)
            budget -= len(piece) + 3
            if budget <= 40:
                break
    return _clip(" | ".join(picked), limit)


def _label(message) -> str:
    name = getattr(message, "name", None)
    return str(name).lower() if name else "hub"


def _turns(messages: list) -> list[Turn]:
    """Group messages into (human question, [agent answers]) turns; skip the open question."""
    turns: list[Turn] = []
    current: Turn | None = None
    for message in messages:
        kind = getattr(message, "type", "")
        content = getattr(message, "content", "")
        if not isinstance(content, str):
            content = str(content)
        if kind == "human":
            current = Turn(question=content)
            turns.append(current)
        elif kind == "ai" and current is not None and hasattr(message, "name"):
            current.answers.append((_label(message), content))
    if turns and not turns[-1].answers:
        turns.pop()          # the question being answered right now
    return turns


def _render_turn(turn: Turn, *, verbatim: bool, question_chars: int,
                 answer_chars: int, digest_chars: int) -> str:
    parts = [f"Q: {_clip_keep(turn.question, question_chars)}"]
    for name, text in turn.answers:
        body = _strip_footer(text)
        body = _clip_keep(body, answer_chars) if verbatim else digest_answer(body, digest_chars)
        if body:
            parts.append(f"[{name}] {body}")
    return "\n".join(parts)


def render_history(state: dict, max_turns: int | None = None,
                   total_chars: int | None = None) -> str:
    """Build the history block for the QA prompt. Empty string when there is no past turn."""
    turns = _turns(list(state.get("messages", [])))
    if not turns:
        return ""

    total = total_chars if total_chars is not None else _int_env(
        "HUB_QA_HISTORY_CHARS", DEFAULT_TOTAL_CHARS, 1500, 30000)
    limit_turns = max_turns if max_turns is not None else _int_env(
        "HUB_QA_HISTORY_TURNS", DEFAULT_MAX_TURNS, 1, 60)
    verbatim_turns = _int_env("HUB_QA_VERBATIM_TURNS", DEFAULT_VERBATIM_TURNS, 0, 10)
    verbatim_chars = _int_env("HUB_QA_VERBATIM_CHARS", DEFAULT_VERBATIM_CHARS, 300, 6000)
    digest_chars = _int_env("HUB_QA_DIGEST_CHARS", DEFAULT_DIGEST_CHARS, 120, 1200)
    question_chars = _int_env("HUB_QA_QUESTION_CHARS", DEFAULT_QUESTION_CHARS, 60, 2000)

    if limit_turns <= 0:
        return ""

    budget = max(400, total - _HEADER_RESERVE)
    # Keep the verbatim window from eating the whole budget: at most ~60% split across it,
    # so the digest window still has room for the older turns.
    verbatim_cap = min(verbatim_chars, max(400, int(budget * 0.6 // max(1, verbatim_turns))))
    kept: list[str] = []
    for index, turn in enumerate(reversed(turns[-limit_turns:])):
        verbatim = index < verbatim_turns
        block = _render_turn(
            turn, verbatim=verbatim, question_chars=question_chars,
            answer_chars=verbatim_cap, digest_chars=digest_chars,
        )
        if len(block) + 2 > budget:                      # does not fit: try the compact form
            if verbatim:
                block = _render_turn(
                    turn, verbatim=False, question_chars=question_chars,
                    answer_chars=verbatim_cap, digest_chars=digest_chars,
                )
            if len(block) + 2 > budget:
                break
        kept.append(block)
        budget -= len(block) + 2

    kept.reverse()
    older = len(turns) - len(kept)
    header = f"--- LỊCH SỬ HỘI THOẠI ({len(kept)} lượt gần nhất"
    header += f"; {older} lượt cũ hơn đã lược bớt) ---" if older else ") ---"
    hint = ('Dùng để hiểu các từ như "cái đó", "file kia", "làm tiếp". '
            "Lượt cũ được rút gọn; source code vừa đọc luôn đúng hơn lịch sử.")
    return "\n".join([header, hint, *kept, "--- HẾT LỊCH SỬ ---"])


def history_messages(state: dict, max_turns: int | None = None) -> list:
    """History as prompt messages (empty list when there is nothing to add)."""
    block = render_history(state, max_turns=max_turns)
    return [HumanMessage(content=block)] if block else []
