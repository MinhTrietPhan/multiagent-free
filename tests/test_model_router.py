from types import SimpleNamespace

import pytest

from hub.model_router import ModelRouter, UsageTracker


class QuotaModel:
    def __init__(self, name, error):
        self.model = name
        self.error = error
        self.calls = 0

    def invoke(self, data, **kwargs):
        self.calls += 1
        raise self.error


def test_shared_input_token_quota_stops_model_failover():
    error = RuntimeError("429 RESOURCE_EXHAUSTED generate_content_free_tier_input_token_count")
    first = QuotaModel("gemini-a", error)
    second = QuotaModel("gemini-b", RuntimeError("should not be called"))
    tracker = UsageTracker()
    router = ModelRouter([first, second], tracker=tracker)

    with pytest.raises(RuntimeError, match="input tokens/phút dùng chung"):
        router.invoke("large prompt")

    assert first.calls == 1
    assert second.calls == 0
    assert tracker.failures["gemini-a"] == 1
