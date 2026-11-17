# Agent Hub – Research Fetcher patch

This patch updates the Research Agent fetch layer. It is an overlay for the existing repository, not a complete copy of the project.

## Files

- `src/hub/agents/research/collectors/fetch.py` – bounded retries, HTTP error classification, optional Playwright rendering for thin JS pages, CAPTCHA/anti-bot challenge detection, and URL validation for browser requests.
- `src/hub/agents/research/controller/pipeline.py` – keeps actionable fetch error details in research warnings.
- `src/hub/agents/research/reporting/formatter.py` – exposes fetch errors in the report.
- `tests/test_research_agent.py` – tests for retries, access-denied handling, JS fallback, and challenge detection.
- `pyproject.toml` – adds the optional `research-browser` dependency group.
- `.env.example` – documents the browser fallback toggle.

## Apply

Extract this ZIP into the root of the existing `agent-hub` repository and allow the files to overwrite the matching paths.

## Install

The regular Research Agent still works without Playwright:

```powershell
pip install -e ".[research]"
```

To enable the browser fallback for JavaScript-rendered pages:

```powershell
pip install -e ".[research,research-browser]"
playwright install chromium
```

Configuration:

```dotenv
HUB_RESEARCH_BROWSER_FALLBACK=true
```

Set it to `false` to disable browser rendering. Playwright is only used when a normal HTTP request succeeds but extracted article text is too short. HTTP 401/403, rate limits, CAPTCHA and anti-bot challenges are not bypassed; the source is reported as blocked so the agent can rely on search snippets or other sources.

## Verify

Run from the repository root using the project's environment:

```powershell
python -m compileall -q src/hub/agents/research
pytest -q tests/test_research_agent.py
```

The patch was validated with the Research Agent test file: 12 passed. It was not tested against live websites or the user's local environment. Browser fallback requires installing Chromium separately.
