"""Turn a user question into a bounded, explicit research plan.

The plan is the only place where the LLM decides *what to look for*:
a small list of queries, the languages, the recency window and the intent
(``digest`` = "give me today's news", ``verify`` = "is this claim true?").
Everything after this step is deterministic and budgeted, so a bad or missing
plan can only make research narrower, never run away.

When the LLM is disabled, unavailable or returns invalid JSON, a deterministic
fallback plan is used and research still works offline.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import date

from ....model_router import get_llm
from ..processors.source_quality import APPROVED_DOMAINS

INTENTS = {"digest", "verify"}
MAX_QUERIES = 3
MAX_DOMAINS = 3
DEFAULT_RECENCY_DAYS = 7

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


@dataclass
class ResearchPlan:
    queries: list[str]
    intent: str = "digest"
    domains: list[str] = field(default_factory=list)
    recency_days: int = DEFAULT_RECENCY_DAYS
    language: str = "vi"
    origin: str = "fallback"          # "llm" | "fallback"
    note: str = ""                    # why the fallback was used, if any

    def as_dict(self) -> dict:
        return {
            "queries": list(self.queries),
            "intent": self.intent,
            "domains": list(self.domains),
            "recency_days": self.recency_days,
            "language": self.language,
            "origin": self.origin,
        }


def llm_enabled(explicit: bool | None = None) -> bool:
    if explicit is not None:
        return explicit
    return os.getenv("HUB_RESEARCH_LLM", "true").strip().lower() not in {
        "0", "false", "no", "off",
    }


def fallback_plan(query: str, *, language: str = "vi",
                  recency_days: int = DEFAULT_RECENCY_DAYS, note: str = "") -> ResearchPlan:
    """Deterministic plan used offline and whenever the LLM plan is unusable."""
    base = " ".join(query.split())[:200]
    variants = [base, f"{base} mới nhất", f"{base} phân tích"]
    queries: list[str] = []
    for variant in variants:
        if variant and variant.casefold() not in {q.casefold() for q in queries}:
            queries.append(variant)
    return ResearchPlan(queries=queries[:MAX_QUERIES], intent="digest",
                        domains=[], recency_days=recency_days,
                        language=language, origin="fallback", note=note)


def build_prompt(query: str, *, today: str | None = None,
                 language: str = "vi", max_queries: int = MAX_QUERIES) -> str:
    today = today or date.today().isoformat()
    approved = ", ".join(APPROVED_DOMAINS)
    return (
        "Bạn là bộ lập kế hoạch tìm kiếm web cho một trợ lý research.\n"
        f"Hôm nay là {today}.\n"
        f"Câu hỏi người dùng: {query}\n\n"
        "Trả về DUY NHẤT một JSON object theo schema:\n"
        '{"intent": "digest|verify", "queries": ["..."], "domains": ["..."], '
        '"recency_days": 7, "language": "vi"}\n\n'
        "Quy tắc:\n"
        f"- Tối đa {max_queries} query, cùng ngôn ngữ với câu hỏi.\n"
        "- intent = \"digest\" khi người dùng muốn tổng hợp tin/tìm hiểu chung; "
        "\"verify\" khi họ cần kiểm chứng một thông tin cụ thể.\n"
        "- Query phải cụ thể, có mốc thời gian khi câu hỏi nói \"hôm nay/tuần này\".\n"
        "- Không bịa sự kiện, không thêm chi tiết người dùng không nói.\n"
        f"- domains chỉ được chọn trong danh sách nguồn đã duyệt: {approved}.\n"
        "- recency_days trong khoảng 1-30.\n"
        "Chỉ trả JSON, không giải thích."
    )


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block if isinstance(block, str) else block.get("text", "")
            for block in content
        )
    return str(content)


def _json_payload(raw: str) -> dict:
    text = raw.strip()
    match = _JSON_BLOCK.search(text)
    if match:
        text = match.group(1)
    elif not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object in planner response")
        text = text[start:end + 1]
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("planner response is not an object")
    return data


def _clean_queries(raw, fallback_text: str) -> list[str]:
    if not isinstance(raw, list):
        return []
    queries: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        value = " ".join(item.split())[:200].strip()
        if len(value) < 3:
            continue
        if value.casefold() not in {q.casefold() for q in queries}:
            queries.append(value)
    return queries[:MAX_QUERIES] or [fallback_text]


def _clean_domains(raw) -> list[str]:
    if not isinstance(raw, list):
        return []
    allowed = set(APPROVED_DOMAINS)
    chosen: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        value = item.strip().lower().removeprefix("www.")
        if value in allowed and value not in chosen:
            chosen.append(value)
    return chosen[:MAX_DOMAINS]


def _int(value, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def plan_from_payload(query: str, data: dict, *, language: str = "vi") -> ResearchPlan:
    """Validate an LLM plan; raises ValueError when it cannot be used."""
    intent = data.get("intent") if data.get("intent") in INTENTS else "digest"
    queries = _clean_queries(data.get("queries"), query)
    if not queries:
        raise ValueError("planner returned no usable query")
    return ResearchPlan(
        queries=queries,
        intent=intent,
        domains=_clean_domains(data.get("domains")),
        recency_days=_int(data.get("recency_days"), DEFAULT_RECENCY_DAYS, 1, 30),
        language=str(data.get("language") or language)[:8],
        origin="llm",
    )


class ResearchPlanner:
    def __init__(self, llm_factory=None, enabled: bool | None = None,
                 language: str | None = None):
        self.llm_factory = llm_factory or get_llm
        self.enabled = enabled
        self.language = language or os.getenv("HUB_RESEARCH_LANGUAGE", "vi")

    def plan(self, query: str) -> ResearchPlan:
        query = " ".join((query or "").split())
        if not query:
            return fallback_plan("tin tức hôm nay", language=self.language)
        if not llm_enabled(self.enabled):
            return fallback_plan(query, language=self.language, note="LLM tắt (HUB_RESEARCH_LLM)")
        try:
            llm = self.llm_factory("research")
            response = llm.invoke(build_prompt(query, language=self.language))
            payload = _json_payload(_text(getattr(response, "content", response)))
            return plan_from_payload(query, payload, language=self.language)
        except Exception as exc:                      # quota, timeout, bad JSON, missing model
            return fallback_plan(query, language=self.language,
                                 note=f"LLM plan lỗi: {type(exc).__name__}")
