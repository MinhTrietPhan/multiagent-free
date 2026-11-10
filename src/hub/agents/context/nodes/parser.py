from pathlib import Path

from ..state import ModuleInfo, State
from ..tools.notebook import analyze_notebook
from ..tools.pyparse import analyze


def parser_node(state: State) -> dict:
    root = Path(state["root"])
    parsed: dict[str, ModuleInfo] = {}
    for f in state["files"]:
        raw = (root / f).read_text(encoding="utf-8", errors="replace")
        if f.endswith(".ipynb"):
            a, kind = analyze_notebook(raw), "ipynb"
            skeleton = "\n".join(a["skeleton"])
        else:
            a, kind = analyze(raw), "py"
            skeleton = "\n".join(a["skeleton"])
        parsed[f] = {"kind": kind, "skeleton": skeleton, "clean": a["clean"],
                     "imports": a["imports"], "has_main": a["has_main"]}
    return {"parsed": parsed}
