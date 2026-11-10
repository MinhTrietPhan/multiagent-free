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
