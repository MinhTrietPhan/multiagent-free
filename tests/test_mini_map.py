"""Tests for the on-the-fly project index used by the QA agent."""
import time

from hub.mini_map import build_mini_map


def make_project(root):
    (root / "pyproject.toml").write_text(
        "[project]\nname = 'demo'\n[project.scripts]\ndemo = \"demo.main:run\"\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text("# demo\n", encoding="utf-8")
    pkg = root / "src" / "demo"
    pkg.mkdir(parents=True)
    (pkg / "main.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    (pkg / "util.py").write_text("X = 1\n", encoding="utf-8")
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text("def test_x():\n    pass\n", encoding="utf-8")


def test_index_lists_tree_entry_points_and_scripts(tmp_path):
    make_project(tmp_path)
    text = build_mini_map(tmp_path)
    assert "Dự án: " in text and "file" in text
    assert "src/demo/" in text
    assert "src/demo/main.py" in text          # entry point
    assert "demo = demo.main:run" in text      # pyproject script
    assert "test_x.py" in text


def test_filters_secrets_and_generated_dirs(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "ok.py").write_text("x = 1\n", encoding="utf-8")
    for name in (".venv", "__pycache__", "node_modules", "pkg.egg-info", ".git"):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "inside.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (tmp_path / "id_rsa").write_text("key\n", encoding="utf-8")
    (tmp_path / "secret.json").write_text("{}\n", encoding="utf-8")

    text = build_mini_map(tmp_path)
    assert "ok.py" in text
    for hidden in (".venv", "__pycache__", "node_modules", "egg-info", ".env", "id_rsa", "secret.json"):
        assert hidden not in text


def test_does_not_read_context_file_contents(tmp_path):
    (tmp_path / "PROJECT_CONTEXT_full.md").write_text("MARKER_SHOULD_NOT_APPEAR", encoding="utf-8")
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    text = build_mini_map(tmp_path)
    assert "MARKER_SHOULD_NOT_APPEAR" not in text
    assert "PROJECT_CONTEXT_full.md" in text  # name only, as a root file entry


def test_budget_truncates_whole_lines(tmp_path):
    for i in range(60):
        directory = tmp_path / f"pkg{i:02d}"
        directory.mkdir()
        for j in range(30):
            (directory / f"f{j}.py").write_text("x = 1\n", encoding="utf-8")

    text = build_mini_map(tmp_path, max_chars=1200)
    assert len(text) <= 1300
    assert "... +" in text and "mục khác" in text
    assert all(line.strip() for line in text.splitlines())
    assert "Ghi chú:" in text  # the hint always survives truncation


def test_large_project_is_fast_and_small(tmp_path):
    for i in range(200):
        directory = tmp_path / "packages" / f"mod{i:03d}"
        directory.mkdir(parents=True)
        for j in range(5):
            (directory / f"f{j}.py").write_text("x = 1\n", encoding="utf-8")

    start = time.perf_counter()
    text = build_mini_map(tmp_path)
    assert time.perf_counter() - start < 5
    assert len(text) <= 3100


def test_empty_project_is_safe(tmp_path):
    text = build_mini_map(tmp_path)
    assert "chưa thấy file source nào" in text


def test_respects_agentignore(tmp_path):
    generated = tmp_path / "generated"
    generated.mkdir()
    (generated / "gen.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "keep.py").write_text("y = 1\n", encoding="utf-8")
    (tmp_path / ".agentignore").write_text("generated/\n", encoding="utf-8")

    text = build_mini_map(tmp_path)
    assert "keep.py" in text
    assert "generated" not in text


def test_does_not_touch_the_project(tmp_path):
    make_project(tmp_path)

    def snapshot():
        return sorted(
            (path.relative_to(tmp_path).as_posix(), path.stat().st_mtime_ns)
            for path in tmp_path.rglob("*")
        )

    before = snapshot()
    build_mini_map(tmp_path)
    build_mini_map(str(tmp_path))
    assert snapshot() == before


def test_env_budget(tmp_path, monkeypatch):
    make_project(tmp_path)
    monkeypatch.setenv("HUB_QA_MINIMAP_CHARS", "600")
    text = build_mini_map(tmp_path)
    assert len(text) <= 620
