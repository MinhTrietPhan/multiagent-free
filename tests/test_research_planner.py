from types import SimpleNamespace

from hub.agents.research.controller.planner import (
    ResearchPlanner,
    build_prompt,
    fallback_plan,
    llm_enabled,
)


class FakeLLM:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.prompts = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return SimpleNamespace(content=self.payload)


def _planner(fake, enabled=True):
    seen = []

    def factory(agent):
        seen.append(agent)
        return fake

    return ResearchPlanner(llm_factory=factory, enabled=enabled), seen


def test_valid_llm_json_is_validated_and_cleaned():
    fake = FakeLLM(
        '```json\n{"intent": "verify", "queries": ["OpenAI ra mắt GPT-6", '
        '"GPT-6 xác nhận chính thức", "query 3", "query 4"], '
        '"domains": ["vnexpress.net", "evil.example", "reuters.com"], '
        '"recency_days": 99, "language": "vi"}\n```'
    )
    planner, seen = _planner(fake)
    plan = planner.plan("Có đúng OpenAI vừa ra mắt GPT-6 không?")

    assert seen == ["research"]
    assert plan.origin == "llm"
    assert plan.intent == "verify"
    assert len(plan.queries) == 3
    assert plan.domains == ["vnexpress.net", "reuters.com"]
    assert plan.recency_days == 30          # clamped to 1-30
    assert fake.prompts


def test_invalid_json_falls_back_without_crashing():
    fake = FakeLLM("đây không phải JSON")
    planner, _ = _planner(fake)
    plan = planner.plan("tin hot hôm nay")

    assert plan.origin == "fallback"
    assert plan.intent == "digest"
    assert plan.queries[0] == "tin hot hôm nay"
    assert len(plan.queries) <= 3
    assert "LLM plan lỗi" in plan.note


def test_llm_exception_falls_back_to_deterministic_plan():
    planner, _ = _planner(FakeLLM(error=RuntimeError("quota exhausted")))
    plan = planner.plan("giá vàng hôm nay")

    assert plan.origin == "fallback"
    assert plan.queries[0] == "giá vàng hôm nay"
    assert any("mới nhất" in query for query in plan.queries)


def test_disabled_llm_never_calls_the_model():
    fake = FakeLLM('{"queries": ["should not happen"]}')
    planner, seen = _planner(fake, enabled=False)
    plan = planner.plan("tin hot hôm nay")

    assert plan.origin == "fallback"
    assert seen == []
    assert fake.prompts == []


def test_prompt_contains_today_and_only_approved_domains():
    prompt = build_prompt("tin công nghệ", today="2026-11-19")
    assert "2026-11-19" in prompt
    assert "vnexpress.net" in prompt
    assert "JSON" in prompt


def test_fallback_plan_deduplicates_variants():
    plan = fallback_plan("trí tuệ nhân tạo")
    assert len(plan.queries) == len({query.casefold() for query in plan.queries})


def test_llm_enabled_reads_env(monkeypatch):
    monkeypatch.delenv("HUB_RESEARCH_LLM", raising=False)
    assert llm_enabled() is True
    monkeypatch.setenv("HUB_RESEARCH_LLM", "false")
    assert llm_enabled() is False
    assert llm_enabled(True) is True
