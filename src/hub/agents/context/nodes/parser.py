from pathlib import Path
from ..state import ModuleInfo, State
from ..tools.notebook import analyze_notebook
from ..tools.pyparse import analyze
from ..tools.textparse import analyze_text

def parser_node(state: State) -> dict:
    root = Path(state['root'])
    parsed: dict[str, ModuleInfo] = {}
    keep = state.get('mode') == 'full'
    for f in state['files']:
        raw = (root / f).read_text(encoding='utf-8', errors='replace')
        if f.endswith('.ipynb'):
            a, kind = (analyze_notebook(raw, keep_source=keep), 'ipynb')
            skeleton = '\n'.join(a['skeleton'])
        elif f.endswith('.py'):
            a, kind = (analyze(raw, keep_source=keep), 'py')
            skeleton = '\n'.join(a['skeleton'])
        else:
            a = analyze_text(raw, f, keep_source=keep)
            kind = Path(f).suffix.lstrip('.') or 'text'
            skeleton = '\n'.join(a['skeleton'])
        parsed[f] = {'kind': kind, 'skeleton': skeleton, 'clean': a['clean'], 'imports': a['imports'], 'has_main': a['has_main']}
    return {'parsed': parsed}
