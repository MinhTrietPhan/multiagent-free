"""Builds the import graph and scores files. Higher score = more likely to deserve full code."""
from pathlib import PurePosixPath

from ..config import CORE_HINTS, ENTRY_NAMES, LOW_HINTS
from ..state import State


def _module_names(path: str) -> set[str]:
    p = PurePosixPath(path)
    parts = list(p.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    # every dotted suffix: a/b/c.py -> "a.b.c", "b.c", "c"
    return {".".join(parts[i:]) for i in range(len(parts))} if parts else set()


def ranker_node(state: State) -> dict:
    parsed = state["parsed"]
    index: dict[str, set[str]] = {}
    for f in parsed:
        for name in _module_names(f):
            index.setdefault(name, set()).add(f)

    edges: set[tuple[str, str]] = set()
    for f, info in parsed.items():
        for imp in info["imports"]:
            name = imp.lstrip(".")
            for cand in (name, name.rsplit(".", 1)[0] if "." in name else name):
                targets = index.get(cand, set())
                if len(targets) == 1:  # ambiguous names are skipped to stay accurate
                    t = next(iter(targets))
                    if t != f:
                        edges.add((f, t))
                    break

    in_deg = {f: 0 for f in parsed}
    for _, t in edges:
        in_deg[t] += 1

    entry_points, scores = [], {}
    for f, info in parsed.items():
        name = PurePosixPath(f).name.lower()
        score = in_deg[f] * 2.0
        if info["has_main"] or name in ENTRY_NAMES:
            score += 3
            entry_points.append(f)
        if any(h in name for h in CORE_HINTS):
            score += 1.5
        if any(h in f.lower() for h in LOW_HINTS):
            score -= 4
        scores[f] = score

    ranking = sorted(parsed, key=lambda f: (-scores[f], f))
    return {"edges": sorted(edges), "ranking": ranking, "entry_points": sorted(entry_points)}
