"""Code agent package. `SPEC` is built lazily so importing e.g. `.settings` does not build the graph."""

__all__ = ["SPEC"]


def __getattr__(name):
    if name == "SPEC":
        from .spec import SPEC
        return SPEC
    raise AttributeError(name)
