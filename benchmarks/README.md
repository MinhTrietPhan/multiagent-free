# Laya research benchmark data

Create a JSONL file with human-labelled examples. Keep a held-out test split; do not tune prompts on it.

Action row:

```json
{"kind":"action","state":{"query":"AI news","search_rounds":1,"fetched_count":5,"pending_count":3,"article_count":5,"max_search_rounds":2,"max_pages":12},"expected":"FETCH_NEXT"}
```

Article-score row (expected is an ordinal label from 0 to 4):

```json
{"kind":"article_score","article":{"url":"https://example.org/news","title":"Example AI announcement","source":"example.org","published_at":"2026-11-15T09:00:00+00:00","content":"Short factual article excerpt..."},"expected":3}
```

Run:

```bash
python scripts/benchmark_laya_research.py benchmarks/my_labels.jsonl
```

Label examples using a written rubric. For ranking, compare pairwise ordering or Spearman correlation in addition to score MAE. Do not enable active mode based only on training-set accuracy.
