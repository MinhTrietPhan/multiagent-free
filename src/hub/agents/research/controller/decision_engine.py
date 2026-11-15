"""Laya adapter. Lazy-loads the local decision model; never calls Gemini."""
from __future__ import annotations
import os
from functools import lru_cache
from typing import Any


ACTIONS = ("FETCH_NEXT", "SEARCH_MORE", "VERIFY_TOP", "STOP")


@lru_cache(maxsize=1)
def _load_router():
    # Laya's multilingual router handles Vietnamese. Loading is deferred until research is used.
    os.environ.setdefault("USE_TF", "0")
    try:
        from laya import Router
    except ImportError as exc:
        raise RuntimeError("Thiếu Laya. Cài bằng: pip install -e '.[research]'") from exc
    device = os.getenv("HUB_LAYA_DEVICE") or None
    return Router(device=device) if device else Router()


class LayaDecisionEngine:
    def __init__(self, router=None):
        self._router = router
        self.model_name = os.getenv("HUB_LAYA_MODEL", "multilingual")

    @property
    def router(self):
        if self._router is None:
            self._router = _load_router()
        return self._router

    def predict(self, state: dict[str, Any], questions: dict[str, Any]) -> dict[str, Any]:
        result = self.router.predict(state, questions, model=self.model_name)
        return result.get("answers", result)

    def choose_action(self, state: dict[str, Any]) -> tuple[str | None, float | None]:
        question = {
            "next_action": {
                "type": "choice",
                "instructions": (
                    "Choose the safest useful next step in a bounded web research workflow. "
                    "Do not stop while useful unprocessed pages remain, but stop at configured limits."
                ),
                "criteria": {
                    "FETCH_NEXT": "Fetch the next relevant URL already in the pending queue.",
                    "SEARCH_MORE": "Search another query because coverage is insufficient and search budget remains.",
                    "VERIFY_TOP": "Search for independent sources corroborating the leading story.",
                    "STOP": "Stop because enough evidence is collected or the budget is exhausted.",
                },
            }
        }
        answers = self.predict(state, question)
        answer = answers.get("next_action", {})
        if isinstance(answer, str):
            return answer, None
        action = answer.get("choice")
        confidence = answer.get("confidence")
        try:
            confidence = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            confidence = None
        return action, confidence

    def score_articles(self, articles: list[dict[str, Any]]) -> dict[str, float]:
        """Experimental ordinal scoring; use in shadow/benchmark until domain-validated."""
        if not articles:
            return {}
        state = {"task": "Rank news by importance, relevance, freshness and evidence quality.",
                 "articles": articles}
        questions = {}
        for i, article in enumerate(articles):
            questions[f"article_{i}"] = {
                "type": "score",
                "instructions": (
                    f"Rate the research value of article {i}: relevance to query, public impact, "
                    "freshness and evidence quality. Ignore sensational wording."
                ),
                "criteria": ["very low", "low", "medium", "high", "very high"],
            }
        answers = self.predict(state, questions)
        scores: dict[str, float] = {}
        for i, article in enumerate(articles):
            raw = answers.get(f"article_{i}", {})
            if isinstance(raw, dict):
                raw = raw.get("score")
            try:
                # Laya score is an ordinal expected level, not a calibrated percentage.
                value = float(raw)
                scores[str(article.get("url", i))] = max(0.0, min(100.0, value / 4.0 * 100.0))
            except (TypeError, ValueError):
                continue
        return scores
