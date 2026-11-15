from pathlib import Path
from unittest.mock import patch

import pytest

from hub.agents.qa.project import (
    discover_projects,
    load_project_context,
    resolve_project_root,
    workspace_roots,
)


def make_project(root: Path, name: str) -> Path:
    project = root / name
    project.mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        f"[project]\nname = '{name}'\n", encoding="utf-8"
    )
    return project


def test_workspace_roots_are_scoped_to_configured_paths(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = make_project(workspace, "alpha")
    outside = make_project(tmp_path, "outside")
    monkeypatch.setenv("HUB_WORKSPACE_ROOTS", str(workspace))

    assert workspace_roots() == [workspace.resolve()]
    assert discover_projects() == [project.resolve()]
    assert outside.resolve() not in discover_projects()


def test_project_name_resolves_unique_match(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = make_project(workspace, "agent-hub")
    monkeypatch.setenv("HUB_WORKSPACE_ROOTS", str(workspace))

    result = resolve_project_root({}, "Phân tích project Agent Hub giúp tôi")
    assert result == project.resolve()


def test_ambiguous_project_prompts_for_selection(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    first = make_project(workspace, "agent-hub-one")
    second = make_project(workspace, "agent-hub-two")
    monkeypatch.setenv("HUB_WORKSPACE_ROOTS", str(workspace))

    with patch("hub.agents.qa.project.interrupt", return_value="2") as ask:
        result = resolve_project_root({}, "Phân tích project Agent Hub")

    assert result == second.resolve()
    assert "nhiều dự án" in ask.call_args.args[0].lower()
    assert first.resolve() != result


def test_missing_context_is_optional(tmp_path):
    context, path = load_project_context(tmp_path)
    assert context is None
    assert path is None


def test_context_is_loaded_only_from_selected_project(tmp_path):
    project = make_project(tmp_path, "selected")
    other = make_project(tmp_path, "other")
    (other / "PROJECT_CONTEXT_full.md").write_text("wrong project", encoding="utf-8")
    (project / "PROJECT_CONTEXT_map.md").write_text("selected project", encoding="utf-8")

    context, path = load_project_context(project)
    assert context == "selected project"
    assert path == project / "PROJECT_CONTEXT_map.md"


def test_parallel_resolution_does_not_interrupt_when_ambiguous(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    make_project(workspace, "alpha")
    make_project(workspace, "beta")
    monkeypatch.setenv("HUB_WORKSPACE_ROOTS", str(workspace))

    with pytest.raises(FileNotFoundError, match="nhiều dự án"):
        resolve_project_root({}, "giải thích code", allow_interrupt=False)
