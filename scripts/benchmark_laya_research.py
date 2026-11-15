"""Evaluate Laya on a hand-labelled research decision JSONL dataset.

Each line is one of:
  {"kind":"action", "state": {...}, "expected":"FETCH_NEXT"}
  {"kind":"article_score", "article": {"url":"...", "title":"...", "content":"..."}, "expected": 0..4}

Do not switch HUB_RESEARCH_LAYA_MODE=active until this benchmark is representative
and the model beats a simple majority/mean baseline on a held-out split.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from statistics import mean

from hub.agents.research.controller.decision_engine import LayaDecisionEngine

QUESTIONS = {
    "next_action": {
        "type": "choice",
        "instructions": "Choose the safest useful next action for the current research state.",
        "criteria": {
            "FETCH_NEXT": "Fetch the next relevant URL already queued.",
            "SEARCH_MORE": "Search more because coverage is insufficient and budget remains.",
            "VERIFY_TOP": "Seek independent corroboration for the leading result.",
            "STOP": "Stop because evidence is sufficient or limits are reached.",
        },
    }
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path, help="Hand-labelled JSONL file")
    args = parser.parse_args()
    engine = LayaDecisionEngine()
    action_total = action_correct = 0
    score_errors = []
    score_pairs = []
    for line_no, line in enumerate(args.dataset.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        row = json.loads(line)
        if row["kind"] == "action":
            predicted, _ = engine.choose_action(row["state"])
            action_total += 1
            action_correct += int(predicted == row["expected"])
        elif row["kind"] == "article_score":
            article = row["article"]
            scores = engine.score_articles([article])
            predicted_100 = scores.get(str(article.get("url", 0)))
            if predicted_100 is not None:
                predicted = predicted_100 / 25.0
                expected = float(row["expected"])
                score_errors.append(abs(predicted - expected))
                score_pairs.append((predicted, expected))
        else:
            raise ValueError(f"Unknown kind at line {line_no}: {row['kind']}")
    print(f"Action accuracy: {action_correct}/{action_total} = "
          f"{action_correct / action_total:.3f}" if action_total else "Action accuracy: no action rows")
    print(f"Article score MAE: {mean(score_errors):.3f} over {len(score_errors)} rows"
          if score_errors else "Article score MAE: no score rows")
    if action_total:
        print("Action majority baseline must be calculated from the labelled dataset before comparing.")
    print("Do not use confidence as a correctness guarantee; inspect per-class errors and held-out data.")


if __name__ == "__main__":
    main()
