"""Python source -> skeleton / imports / cleaned full code, using the stdlib `ast`."""
import ast
import copy
from typing import TypedDict
import warnings

class Parsed(TypedDict):
    skeleton: list[str]
    clean: str
    imports: list[str]
    has_main: bool


def _sig(node) -> str:
    c = copy.copy(node)
    c.body = [ast.Pass()]
    c.decorator_list = []
    return ast.unparse(c).splitlines()[0].rstrip(":")


def _doc(node) -> str | None:
    d = ast.get_docstring(node)
    return d.strip().splitlines()[0] if d and d.strip() else None


def _is_main_guard(node: ast.AST) -> bool:
    return (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__")


def _walk_defs(body, indent: int, loc: str, out: list[str]):
    pad = "  " * indent
    for n in body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for d in n.decorator_list:
                out.append(f"{pad}@{ast.unparse(d)}")
            head = _sig(n) if not isinstance(n, ast.ClassDef) else f"class {n.name}" + (
                f"({', '.join(ast.unparse(b) for b in n.bases)})" if n.bases else "")
            out.append(f"{pad}{head}  # {loc}{n.lineno}")
            if (d := _doc(n)):
                out.append(f'{pad}  """{d}"""')
            if isinstance(n, ast.ClassDef):
                _walk_defs(n.body, indent + 1, loc, out)
        elif indent == 0 and isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id.isupper():
                    val = ast.unparse(n.value)
                    out.append(f"{t.id} = {val[:60]}{'...' if len(val) > 60 else ''}  # {loc}{n.lineno}")


def analyze(source: str, loc: str = "L") -> Parsed:
    """`loc` is the location prefix, e.g. "L" for files or "c3:L" for notebook cell 3."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(source)
    except SyntaxError:
        clean = "\n".join(l for l in source.splitlines() if l.strip())
        return {"skeleton": [f"# (syntax error, raw lines kept)"], "clean": clean,
                "imports": [], "has_main": False}
    imports, skel = [], []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imports += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            imports.append(n.module)
            imports += [f"{n.module}.{a.name}" for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.level > 0:
            base = "." * n.level + (n.module or "")
            imports.append(base)
            imports += [f"{base}.{a.name}" if n.module else f"{base}{a.name}" for a in n.names]
    _walk_defs(tree.body, 0, loc, skel)
    return {"skeleton": skel, "clean": ast.unparse(tree),
            "imports": imports, "has_main": any(_is_main_guard(n) for n in tree.body)}
