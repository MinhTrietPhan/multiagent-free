"""Assembles the final markdown: overview (always) + per-file skeleton, upgraded to full code within budget."""
import re
import sys
import tomllib
from pathlib import Path
from ..config import CONFIG_LIMIT
from ..state import State
from ..tools.files import git_head, render_tree
from ..tools.tokens import count_tokens

def _manifest_deps(root: Path, manifests: list[str]) -> list[str]:
    deps: list[str] = []
    for m in manifests:
        p = root / m
        try:
            if p.name == 'requirements.txt':
                for l in p.read_text().splitlines():
                    l = l.split('#')[0].strip()
                    if l and (not l.startswith('-')):
                        deps.append(re.split('[=<>~!\\[; ]', l)[0])
            elif p.name == 'pyproject.toml':
                data = tomllib.loads(p.read_text())
                for d in data.get('project', {}).get('dependencies', []):
                    deps.append(re.split('[=<>~!\\[; ]', d)[0])
        except Exception:
            pass
    return sorted(set(deps))

def _third_party(parsed: dict, files: list[str]) -> list[str]:
    local = {Path(f).with_suffix('').parts[-1] for f in files} | {p for f in files for p in Path(f).parts[:-1]}
    std = set(sys.stdlib_module_names)
    out = set()
    for info in parsed.values():
        for imp in info['imports']:
            if imp.startswith('.'):
                continue
            top = imp.split('.')[0]
            if top not in std and top not in local:
                out.add(top)
    return sorted(out)

def _mermaid(edges: list[tuple[str, str]], limit: int=40) -> str:
    nid = lambda p: re.sub('\\W', '_', p)
    lines = ['graph LR']
    for a, b in edges[:limit]:
        lines.append(f'  {nid(a)}["{a}"] --> {nid(b)}["{b}"]')
    if len(edges) > limit:
        lines.append(f'  %% +{len(edges) - limit} more edges omitted')
    return '\n'.join(lines)

LANG = {'.yaml': 'yaml', '.yml': 'yaml', '.json': 'json', '.toml': 'toml', '.ini': 'ini', '.cfg': 'ini', '.md': 'markdown', '.js': 'javascript', '.jsx': 'jsx', '.mjs': 'javascript', '.cjs': 'javascript', '.ts': 'typescript', '.tsx': 'tsx', '.vue': 'vue', '.svelte': 'svelte', '.java': 'java', '.kt': 'kotlin', '.kts': 'kotlin', '.scala': 'scala', '.go': 'go', '.rs': 'rust', '.c': 'c', '.h': 'c', '.cc': 'cpp', '.cpp': 'cpp', '.hpp': 'cpp', '.cs': 'csharp', '.php': 'php', '.rb': 'ruby', '.swift': 'swift', '.sh': 'bash', '.bash': 'bash', '.sql': 'sql'}

def _config_section(root: Path, manifests: list[str], limit) -> list[str]:
    out = ['', '## 3. Config & docs']
    for m in manifests:
        try:
            text = (root / m).read_text(encoding='utf-8', errors='replace').strip()
        except OSError:
            continue
        if limit is not None and len(text) > limit:
            text = text[:limit].rsplit('\n', 1)[0] + '\n# ... (truncated)'
        lang = 'dockerfile' if Path(m).name == 'Dockerfile' else LANG.get(Path(m).suffix, 'text')
        out += ['', f'### {m}  [CONFIG]', f'```{lang}', text, '```']
    return out

def renderer_node(state: State) -> dict:
    root = Path(state['root']).resolve()
    parsed, files = (state['parsed'], state['files'])
    budget, pins = (state.get('budget'), set(state.get('pin', [])))
    full: list[str] = []
    remaining = budget if budget is not None else 0
    order = [f for f in state['ranking'] if f in pins] + [f for f in state['ranking'] if f not in pins]
    for f in order:
        cost = count_tokens(parsed[f]['clean'])
        if budget is None or f in pins or (remaining > 0 and cost <= remaining):
            full.append(f)
            remaining = max(0, remaining - cost)
    deps = _manifest_deps(root, state.get('manifests', []))
    tp = _third_party(parsed, files)
    parts = ['## 1. Overview', '### Stack']
    parts.append(f"- Declared dependencies: {(', '.join(deps) if deps else '(none found)')}")
    parts.append(f"- Third-party imports seen in code: {(', '.join(tp) if tp else '(none)')}")
    parts += ['', '### Entry points']
    parts += [f'- `{f}`' for f in state['entry_points']] or ['- (none detected)']
    parts += ['', '### Structure', '```text', render_tree(files + state.get('manifests', [])), '```']
    if state['edges']:
        parts += ['', '### Internal imports', '```mermaid', _mermaid(state['edges']), '```']
    parts += ['', '## 2. Files']
    for f in state['ranking']:
        info = parsed[f]
        if f in full:
            lang = 'python' if Path(f).suffix == '.py' else ('json' if Path(f).suffix == '.ipynb' else LANG.get(Path(f).suffix, 'text'))
            parts += ['', f'### {f}  [FULL]', f'```{lang}', info['clean'], '```']
        else:
            body = info['skeleton'] or '# (no top-level definitions)'
            parts += ['', f'### {f}  [SKELETON]', '```text', body, '```']
    limit = CONFIG_LIMIT.get(state.get('mode'), 3000)
    if limit != 0 and state.get('manifests'):
        parts += _config_section(root, state['manifests'], limit)
    body = '\n'.join(parts)
    head = ['# Project context: ' + root.name, f'> Root: `{root}`']
    sha = git_head(str(root))
    meta = [f"mode={state.get('mode')}", f'files={len(files)}', f'full_code={len(full)}']
    if sha:
        meta.append(f'commit={sha}')
    head.append('> repo-context · ' + ' · '.join(meta))
    cm = 'comments kept' if state.get('mode') == 'full' else 'comments stripped'
    head.append(f'> Reading guide: `[SKELETON]` = signatures + first docstring line only (`# L12` = line number; `c3:L5` = notebook cell 3, line 5). `[FULL]` = complete code, {cm}. `[CONFIG]` = config/doc file content. If you need the body of a skeleton file, ask the user for it by path.')
    out = '\n'.join(head) + '\n\n' + body + '\n'
    out = out.replace('\n\n## 1.', f'\n> ~{count_tokens(out):,} tokens\n\n## 1.', 1)
    return {'output': out}
