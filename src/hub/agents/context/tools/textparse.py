"""Lightweight source indexer for non-Python languages (no compiler/toolchain needed)."""
from __future__ import annotations

import re
from pathlib import Path

_IMPORT_PATTERNS = (
    re.compile(r"^\s*(?:import|export)\s+(?:.+?\s+from\s+)?[\"']([^\"']+)[\"']", re.M),
    re.compile(r"^\s*import\s+([\w.*]+)\s*;?", re.M),
    re.compile(r"^\s*#include\s*[<\"]([^>\"]+)[>\"]", re.M),
    re.compile(r"^\s*(?:from|use)\s+([\w.:/]+)", re.M),
    re.compile(r"^\s*require\s*\(?\s*[\"']([^\"']+)[\"']", re.M),
)

_DEF_PATTERNS = (
    re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function|class|interface|type|enum)\s+\w+[^;{]*", re.M),
    re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+\w+\s*=.*(?:=>|function).*$", re.M),
    re.compile(r"^\s*(?:public|private|protected|static|abstract|final|async|\s)*(?:class|interface|enum|record|struct)\s+\w+[^;{]*", re.M),
    re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+\w+[^;{]*", re.M),
    re.compile(r"^\s*(?:pub\s+)?(?:struct|enum|trait|impl|type)\s+\w+[^;{]*", re.M),
    re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?\w+[^;{]*", re.M),
    re.compile(r"^\s*def\s+\w+[^:]*:?", re.M),
    re.compile(r"^\s*(?:class|module)\s+\w+.*", re.M),
)


def analyze_text(source: str, path: str, keep_source: bool = False) -> dict:
    """Extract imports and declaration signatures with source line numbers."""
    imports: list[str] = []
    for pattern in _IMPORT_PATTERNS:
        imports.extend(match.group(1) for match in pattern.finditer(source))

    declarations: list[tuple[int, str]] = []
    for pattern in _DEF_PATTERNS:
        for match in pattern.finditer(source):
            line_no = source.count("\n", 0, match.start()) + 1
            signature = " ".join(match.group(0).strip().split())
            item = (line_no, signature[:220])
            if item not in declarations:
                declarations.append(item)
    declarations.sort(key=lambda item: item[0])

    if not declarations:
        # A short preview is more useful than an empty skeleton for config-like code.
        for line_no, line in enumerate(source.splitlines(), 1):
            clean = line.strip()
            if clean and not clean.startswith(("//", "#", "/*", "*", "<!--")):
                declarations.append((line_no, clean[:180]))
                if len(declarations) >= 8:
                    break

    skeleton = [f"{signature}  # L{line_no}" for line_no, signature in declarations[:120]]
    entry_name = Path(path).name.lower()
    has_main = entry_name in {"main.js", "index.js", "main.ts", "index.ts", "app.js", "app.ts", "main.go", "main.rs", "program.cs"}
    has_main = has_main or bool(re.search(r"\b(?:function\s+main|fn\s+main|func\s+main|public\s+static\s+void\s+main)\b", source))
    return {
        "skeleton": skeleton,
        "clean": source if keep_source else "\n".join(skeleton),
        "imports": sorted(set(imports)),
        "has_main": has_main,
    }
