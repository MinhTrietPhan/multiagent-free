import json
from hub.agents.context import build_context


def _make_project(tmp_path):
    (tmp_path / "model.py").write_text(
        'import torch\n\nclass CNN:\n    """Simple CNN."""\n    def __init__(self, n: int = 3):\n'
        '        # a comment\n        self.n = n\n\nLR = 0.001\n')
    (tmp_path / "train.py").write_text(
        'from model import CNN\n\ndef fit(m: CNN, epochs: int = 10) -> list[float]:\n    """Train."""\n'
        '    return []\n\nif __name__ == "__main__":\n    fit(CNN())\n')
    nb = {"cells": [
        {"cell_type": "markdown", "source": ["# Demo"]},
        {"cell_type": "code", "source": ["!pip install x\n", "import model\n", "def f(x): return x\n"],
         "outputs": [{"text": "HUGE OUTPUT"}]}]}
    (tmp_path / "demo.ipynb").write_text(json.dumps(nb))
    (tmp_path / "requirements.txt").write_text("torch>=2\n")


def test_map_mode(tmp_path):
    _make_project(tmp_path)
    out = build_context(str(tmp_path), "map")
    assert "  [SKELETON]" in out and "  [FULL]" not in out
    assert "class CNN  # L3" in out and "LR = 0.001" in out
    assert "a comment" not in out and "HUGE OUTPUT" not in out
    assert "train.py" in out.split("### Entry points")[1].split("###")[0]
    assert "train_py --> model_py" in out.replace('["train.py"]', "").replace('["model.py"]', "")


def test_full_and_pin(tmp_path):
    _make_project(tmp_path)
    assert out_full(tmp_path, "full").count("  [FULL]") == 3
    out = build_context(str(tmp_path), "map", pin=["model.py"])
    assert "### model.py  [FULL]" in out and "### train.py  [SKELETON]" in out


def out_full(tmp_path, mode):
    return build_context(str(tmp_path), mode)





def test_context_cli_default_output_is_saved_in_project_root(tmp_path, monkeypatch):
    from hub.agents.context import cli

    project = tmp_path / "project"
    project.mkdir()
    caller = tmp_path / "caller"
    caller.mkdir()
    monkeypatch.chdir(caller)
    monkeypatch.setattr(cli, "build_context", lambda *args, **kwargs: "generated context")

    cli.main([str(project), "--mode", "map"])

    assert (project / "PROJECT_CONTEXT_map.md").read_text(encoding="utf-8") == "generated context"
    assert not (caller / "PROJECT_CONTEXT_map.md").exists()


def test_context_skips_versioned_envs_and_pyinstaller_internal(tmp_path):
    (tmp_path / "main.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    junk = [
        ".venv-release/Lib/site-packages/pkg/noise.py",
        "Transcripts_automation/_internal/torch/noise.py",
        "build/generated.py",
        "dist/generated.py",
        ".git/hooks/generated.py",
        "__pycache__/cached.py",
    ]
    for relative in junk:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("SHOULD_NOT_APPEAR = True\n", encoding="utf-8")

    out = build_context(str(tmp_path), "full")
    assert "main.py" in out
    for part in (".venv-release", "_internal", "build/generated.py", "dist/generated.py", "__pycache__"):
        assert part not in out


def test_agentignore_excludes_project_specific_generated_folder(tmp_path):
    (tmp_path / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / ".agentignore").write_text("Transcripts_automation/\n", encoding="utf-8")
    generated = tmp_path / "Transcripts_automation" / "output.py"
    generated.parent.mkdir()
    generated.write_text("GENERATED = True\n", encoding="utf-8")

    out = build_context(str(tmp_path), "full")
    assert "main.py" in out
    assert "Transcripts_automation" not in out


def test_context_indexes_javascript_and_typescript_files(tmp_path):
    (tmp_path / "package.json").write_text('{"name":"sample"}', encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.ts").write_text(
        'import { run } from "./service";\nexport async function main() { return run(); }\n',
        encoding="utf-8")
    (tmp_path / "src" / "service.ts").write_text(
        'export class Service {\n  start() {}\n}\n', encoding="utf-8"
    )
    out = build_context(str(tmp_path), "map")
    assert "src/main.ts" in out and "src/service.ts" in out
    assert "function main" in out and "class Service" in out
    assert "package.json" in out
    assert 'src_main_ts["src/main.ts"] --> src_service_ts["src/service.ts"]' in out