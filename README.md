# InsightEngine — Natural-Language SQL Analytics

> A natural-language analytics layer over a multi-table SQL warehouse. Lets non-technical users ask business questions in plain English; an LLM query-planner generates parameterised, guardrailed SQL against PostgreSQL and returns charts, trend analysis, and follow-up suggestions.

**Stack:** Python (FastAPI) · PostgreSQL · OpenAI · Next.js · Recharts · Docker
**Status:** Production for one client; reusable engine

---

## The problem

Most SMEs sit on a Postgres warehouse that only the engineer who built it can query effectively. Stakeholders ask things like *"what's our churn by tier last quarter?"* and either wait days for a dashboard or accept a half-answer from a static report.

InsightEngine sits in front of that warehouse and answers those questions in 3–8 seconds with a chart, a SQL receipt, and a "you may also want to ask…" follow-up.

## What it does

1. **Question intake.** User asks in English, optionally with a context hint ("this is for a board pack").
2. **Schema-aware planning.** LLM is given a compact, embedding-retrieved view of the relevant tables and column descriptions only — never the whole schema.
3. **SQL generation with guardrails.** Generated SQL is parsed (sqlglot) and rejected if it contains `UPDATE`, `DELETE`, `DROP`, `INSERT`, `TRUNCATE`, or non-allowlisted functions.
4. **Parameterised execution.** Final query runs against a read-only role with statement-timeout, against the analytical replica.
5. **Result rendering.** Tabular results are charted by heuristic (time series → line, categorical → bar, single number → KPI card).
6. **Follow-up suggestions.** LLM is shown the question + result and proposes 2–3 next questions.

## Architecture

```
User question
    │
    ▼
┌────────────────────┐
│ Schema retriever   │ ── embedding search over column-doc index
└─────────┬──────────┘
          ▼
┌────────────────────┐
│ LLM query planner  │ ── system prompt + few-shot exemplars
└─────────┬──────────┘
          ▼
┌────────────────────┐
│ SQL parser/guard   │ ── sqlglot AST whitelist
└─────────┬──────────┘
          ▼
┌────────────────────┐
│ Read-only Postgres │ ── statement_timeout = 8s, role with SELECT only
└─────────┬──────────┘
          ▼
┌────────────────────┐
│ Chart picker       │ ── shape detection → Recharts component
└────────────────────┘
```

## Why these choices

| Decision | Trade-off |
|---|---|
| **sqlglot AST whitelist** instead of regex | Catches creative LLM phrasings (e.g. `WITH x AS (DELETE…)` inside a CTE) that string filters miss |
| **Read-only role with timeout** | Defence-in-depth: even if guard is bypassed, no destructive ops; runaway queries auto-cancel |
| **Embedding-retrieved schema slice** | A 200-table warehouse won't fit in context; retrieval is faster + cheaper + more accurate than full-schema dumps |
| **Few-shot in system prompt** | 5–10 worked examples lift accuracy on this client's quirky table names from ~70% to ~94% |
| **Result-shape heuristic for charts** | Avoids a second LLM call; deterministic; cheap |

## Results (one production client)

- **~94% accuracy** on a benchmark set of 60 typical business questions.
- **3–8s median end-to-end latency** for non-trivial questions.
- **0 destructive incidents** — guardrails caught 100% of red-team attempts.
- Replaced ~70% of ad-hoc analyst tickets in the first month.

## Repo structure

```
.
├── api/
│   ├── main.py              # FastAPI app
│   ├── planner/             # Schema retrieval + LLM call
│   ├── guard/               # sqlglot AST whitelist
│   ├── exec/                # Read-only Postgres client
│   └── charts/              # Result-shape heuristic
├── web/                     # Next.js dashboard
├── eval/
│   ├── golden_questions.yml # Benchmark question set
│   └── run_eval.py          # Accuracy harness
└── docs/
    ├── prompt-design.md
    └── threat-model.md
```

## Eval harness

The benchmark set is critical — without it you're flying blind on prompt changes.

```bash
uv run python eval/run_eval.py --model gpt-4o-mini
# → accuracy: 94.1% (53/60), median latency: 4.2s
```

Each prompt change runs the eval before merge. Regressions block.

## Local setup

```bash
docker compose up -d   # Postgres with sample warehouse
uv sync
uv run uvicorn api.main:app --reload
cd web && pnpm dev
```

## Author

Darrshan Govender · Founder, [Agulhas Code](https://agulhascode.co.za)
