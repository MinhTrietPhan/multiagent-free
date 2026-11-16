"""Keep the test-suite independent of the developer's real .env and shell.

`hub.config` loads `.env` on import, so settings such as HUB_WORKSPACE_ROOTS (several projects) would
otherwise leak into tests that expect "the current directory is the only project".
"""
import pytest


@pytest.fixture(autouse=True)
def _isolate_hub_env(monkeypatch, tmp_path_factory):
    monkeypatch.delenv("HUB_WORKSPACE_ROOTS", raising=False)
    monkeypatch.delenv("HUB_PROJECT_SCAN_DEPTH", raising=False)
    monkeypatch.setenv("HUB_DATA_DIR", str(tmp_path_factory.mktemp("hub-data")))
