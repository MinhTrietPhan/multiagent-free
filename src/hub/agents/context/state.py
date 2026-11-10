"""Shared graph state. Every agent reads from it and returns a PARTIAL dict to merge in."""
from typing import TypedDict


class ModuleInfo(TypedDict):
    kind: str            # "py" | "ipynb"
    skeleton: str        # signatures + first docstring line, with L<n> locations
    clean: str           # full code, comments/blank lines stripped, notebook outputs dropped
    imports: list[str]   # raw dotted module names
    has_main: bool       # has `if __name__ == "__main__"`


class State(TypedDict, total=False):
    # inputs
    root: str
    mode: str
    budget: int | None
    pin: list[str]
    exclude: list[str]
    # produced by agents
    files: list[str]                  # discovery
    manifests: list[str]              # discovery
    parsed: dict[str, ModuleInfo]     # parser
    edges: list[tuple[str, str]]      # ranker: (importer, imported)
    ranking: list[str]                # ranker: most important first
    entry_points: list[str]           # ranker
    output: str                       # renderer
