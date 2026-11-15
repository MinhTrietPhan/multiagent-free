from __future__ import annotations
from ..state import ResearchState


def deterministic_action(state: ResearchState) -> str:
    """Safe bounded policy used by default and whenever Laya is unavailable/uncertain."""
    if state.fetched_count >= state.max_pages:
        return "STOP"
    if state.pending_urls:
        return "FETCH_NEXT"
    if state.search_rounds < state.max_search_rounds:
        return "SEARCH_MORE"
    return "STOP"


def choose_action(state: ResearchState, laya_engine=None, mode: str = "shadow") -> tuple[str, str | None]:
    fallback = deterministic_action(state)
    if laya_engine is None or getattr(laya_engine, "_unavailable", False):
        return fallback, None
    try:
        predicted, confidence = laya_engine.choose_action(state.compact())
    except Exception as exc:
        try:
            laya_engine._unavailable = True
        except Exception:
            pass
        state.warnings.append(f"Laya unavailable; fallback policy used: {type(exc).__name__}: {exc}")
        return fallback, None
    if predicted not in {"FETCH_NEXT", "SEARCH_MORE", "VERIFY_TOP", "STOP"}:
        state.warnings.append("Laya returned an unknown action; fallback policy used.")
        return fallback, None
    state.actions.append(f"Laya predicted {predicted} (confidence={confidence})")
    if mode != "active":
        return fallback, predicted
    # Confidence is not a correctness guarantee. Active mode is opt-in and still obeys hard budgets.
    if predicted == "FETCH_NEXT" and state.pending_urls and state.fetched_count < state.max_pages:
        return predicted, predicted
    if predicted == "SEARCH_MORE" and state.search_rounds < state.max_search_rounds:
        return predicted, predicted
    if predicted == "VERIFY_TOP" and state.articles:
        return predicted, predicted
    if predicted == "STOP":
        return predicted, predicted
    state.warnings.append(f"Laya action {predicted} was unsafe/inapplicable; deterministic fallback used.")
    return fallback, predicted
