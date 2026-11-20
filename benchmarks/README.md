# Research report benchmark data

Create a JSONL file with human-labelled examples to evaluate **report quality**,
not model decisions. Keep a held-out split; do not tune prompts on it.

Each line describes one question and what a good answer must contain:

```json
{"query": "tin hot hôm nay", "must_cover": ["sự kiện A", "sự kiện B"], "banned": ["PR", "giật tít"], "min_sources": 3, "max_duplicates": 0}
```

Columns:

- `query`: the exact user question.
- `must_cover`: facts/stories a good report should mention (checked by a human or an LLM judge).
- `banned`: content that must not appear (ads, clickbait, off-topic items).
- `min_sources`: minimum number of distinct domains the report should cite.
- `max_duplicates`: how many repeated stories are tolerated (0 = all duplicates filtered).

Scoring suggestions:

- **Relevance**: fraction of `must_cover` present.
- **Junk filter**: any `banned` item present fails the row.
- **Coverage**: number of distinct domains ≥ `min_sources`.
- **Duplication**: repeated stories ≤ `max_duplicates`.
