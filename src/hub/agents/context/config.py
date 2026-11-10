"""Constants and mode presets. Token budget for full code: 0 = off, None = unlimited."""
MODES = {'map': 0, 'balanced': 8000, 'full': None}
DEFAULT_MODE = 'balanced'
OUTPUT_FILE_TEMPLATE = 'PROJECT_CONTEXT_{mode}.md'
IGNORE_DIRS = {'.git', '__pycache__', '.venv', 'venv', 'env', 'node_modules', '.ipynb_checkpoints', '.mypy_cache', '.pytest_cache', 'build', 'dist', '.idea', '.vscode', 'site-packages'}
CODE_EXT = {'.py', '.ipynb'}
MANIFESTS = {'README.md', 'requirements.txt', 'pyproject.toml', 'Dockerfile', 'environment.yml'}
ENTRY_NAMES = {'main.py', 'train.py', 'app.py', 'run.py', 'cli.py', '__main__.py'}
CORE_HINTS = ('model', 'config', 'core', 'dataset', 'data')
LOW_HINTS = ('test_', '_test', 'setup.py', 'conftest', 'migrations')


def output_filename(mode: str) -> str:
    """Tên file theo mức ngữ cảnh: map -> PROJECT_CONTEXT_map.md, balanced -> ..._balanced.md, full -> ..._full.md."""
    return OUTPUT_FILE_TEMPLATE.format(mode=mode)
