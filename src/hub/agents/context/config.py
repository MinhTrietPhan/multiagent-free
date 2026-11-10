"""Constants and mode presets. Token budget for full code: 0 = off, None = unlimited."""

MODES = {
    "map": 0,          # tree + skeleton only
    "balanced": 8000,  # map + full code of top-ranked files within budget
    "full": None,      # everything, comments stripped
}
DEFAULT_MODE = "balanced"

IGNORE_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "env", "node_modules", ".ipynb_checkpoints",
    ".mypy_cache", ".pytest_cache", "build", "dist", ".idea", ".vscode", "site-packages",
}
CODE_EXT = {".py", ".ipynb"}
MANIFESTS = {"README.md", "requirements.txt", "pyproject.toml", "Dockerfile", "environment.yml"}

# ranking heuristics
ENTRY_NAMES = {"main.py", "train.py", "app.py", "run.py", "cli.py", "__main__.py"}
CORE_HINTS = ("model", "config", "core", "dataset", "data")
LOW_HINTS = ("test_", "_test", "setup.py", "conftest", "migrations")
