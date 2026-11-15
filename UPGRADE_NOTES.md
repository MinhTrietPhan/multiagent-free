# Agent Hub — Research/Laya upgrade

## What changed

- Added `research` to the agent registry. `/research <query>` routes directly to it and skips Supervisor/Gemini routing.
- Added a standalone Research Agent with bounded search/fetch/verify loop.
- Added Laya adapter using `Router.predict(..., model="multilingual")`; model is lazy-loaded and cached for the process lifetime.
- Added Python collection and processing: DDGS search, safe HTTP fetch, Trafilatura extraction, URL/title deduplication, source heuristic, cross-domain corroboration and ranking.
- Added `shadow`, `active`, and `off` modes. Default is `shadow` because the public model card reports weak zero-shot performance on typed-decisions for the base multilingual checkpoint.
- Added JSONL benchmark runner and seven unit tests.

## Install and run

```powershell
pip install -e ".[research,dev]"
copy .env.example .env
hub
```

Then:

```text
/research Tin tức AI mới nhất hôm nay
```

The first Laya request downloads the selected checkpoint if it is not cached. Research does not require `GOOGLE_API_KEY`; QA and LLM-based Supervisor routing still do.

## Modes

- `shadow`: Laya predictions and article scores are observed; Python deterministic policy and baseline ranking control the actual result.
- `active`: Laya may choose bounded actions and contributes to ranking. Enable only after held-out Vietnamese research benchmark.
- `off`: no Laya inference; deterministic research only.

## Verification in this build environment

- `python -m compileall -q src tests scripts`: passed.
- `PYTHONPATH=src pytest -q tests/test_research_agent.py`: 7 passed.
- Full existing test suite was not run: this build environment does not have `langgraph` / `langchain-core`, and package installation could not reach PyPI. Run `pytest -q` in the project's normal venv after installing dependencies.
- No real DDGS request, website fetch, or downloaded Laya inference was performed here.

## Source provenance

This archive reconstructs the 50 source/config snapshots marked `[FULL]` or `[CONFIG]` in `PROJECT_CONTEXT_full(7).md` (commit shown there: `b6e4e49`) and overlays the Research/Laya changes. It is not a Git clone and does not include `.git` history or files absent from that context export. Review the diff against your working tree before replacing files.
