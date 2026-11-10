"""Jupyter notebook -> same shape as a .py module. Outputs are always dropped (they cost many tokens)."""
import json

from .pyparse import analyze


def _strip_magics(code: str) -> str:
    return "\n".join(l for l in code.splitlines() if not l.lstrip().startswith(("%", "!")))


def analyze_notebook(raw: str) -> dict:
    try:
        cells = json.loads(raw).get("cells", [])
    except Exception:
        return {"skeleton": ["# (unreadable notebook)"], "clean": "", "imports": [], "has_main": False}
    skel, clean, imports, has_main = [], [], [], False
    for i, c in enumerate(cells):
        src = "".join(c.get("source", []))
        if not src.strip():
            continue
        if c.get("cell_type") == "markdown":
            text = "\n".join(f"> {l}" for l in src.strip().splitlines())
            skel.append(f"[md cell {i}]\n{text}")
            clean.append(f"# [md cell {i}]\n" + "\n".join(f"# {l}" for l in src.strip().splitlines()))
        elif c.get("cell_type") == "code":
            a = analyze(_strip_magics(src), loc=f"c{i}:L")
            if a["skeleton"]:
                skel.append(f"[code cell {i}]")
                skel += a["skeleton"]
            clean.append(f"# [code cell {i}]\n{a['clean']}")
            imports += a["imports"]
            has_main = has_main or a["has_main"]
    return {"skeleton": skel, "clean": "\n\n".join(clean), "imports": imports, "has_main": has_main}
