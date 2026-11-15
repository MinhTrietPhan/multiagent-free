"""Agent Hub package. Heavy LangGraph imports are lazy for lightweight utilities."""

def build_hub(*args, **kwargs):
    from .graph import build_hub as _build_hub
    return _build_hub(*args, **kwargs)

__all__ = ["build_hub"]
