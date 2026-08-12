"""LLM query planner.

Calls an LLM (Anthropic by default, OpenAI as a fallback) to translate a
natural-language question + schema slice into a single SELECT statement.

The provider is chosen by the ``LLM_PROVIDER`` env var:
    - ``anthropic`` (default): uses ``ANTHROPIC_API_KEY``
    - ``openai``: uses ``OPENAI_API_KEY``

The planner returns the raw SQL string. Validation is the caller's job
(typically ``api.guard.sql_guard.guard_sql``).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from api.planner.schema_retriever import SchemaRetriever, TableSchema

SYSTEM_PROMPT = """\
You are a SQL analyst. Given a database schema and a natural-language question,
write ONE SQLite SELECT statement that answers the question.

Hard rules:
- Output ONLY the SQL, no markdown fences, no commentary, no explanation.
- Use exactly ONE statement. No semicolons except optionally at the end.
- Use only SELECT (with optional CTE/WITH). NEVER use INSERT/UPDATE/DELETE/DROP/
  ALTER/CREATE/TRUNCATE/MERGE/GRANT/REVOKE/REPLACE.
- Reference only tables and columns that appear in the provided schema.
- Prefer explicit JOINs over comma joins.
- Use ISO-8601 date arithmetic (date('now', '-30 days') style for SQLite).
- Cents columns are integers; divide by 100.0 when returning money for display.
- If the question is ambiguous, pick the most useful interpretation rather
  than asking for clarification.
"""

FEW_SHOT_EXAMPLES = """\
Example 1
Question: How many customers signed up last month?
SQL: SELECT COUNT(*) AS signups FROM customers
     WHERE created_at >= date('now', 'start of month', '-1 month')
       AND created_at <  date('now', 'start of month');

Example 2
Question: Top 5 products by revenue this year.
SQL: SELECT p.name, SUM(oi.quantity * oi.unit_price_cents) / 100.0 AS revenue
     FROM order_items oi
     JOIN orders o ON o.id = oi.order_id
     JOIN products p ON p.id = oi.product_id
     WHERE o.status IN ('paid','shipped')
       AND o.created_at >= date('now', 'start of year')
     GROUP BY p.id, p.name
     ORDER BY revenue DESC
     LIMIT 5;
"""


@dataclass(frozen=True)
class PlanResult:
    """Output of the planner."""

    sql: str
    tables_used: list[str]
    model: str


class LLMPlanner:
    """Translates NL questions into SQL using an LLM."""

    def __init__(
        self,
        retriever: SchemaRetriever,
        provider: str | None = None,
        model: str | None = None,
    ):
        self.retriever = retriever
        self.provider = (provider or os.environ.get("LLM_PROVIDER") or "anthropic").lower()
        if self.provider not in {"anthropic", "openai"}:
            raise ValueError(f"unknown LLM_PROVIDER: {self.provider}")
        self.model = model or self._default_model()

    def _default_model(self) -> str:
        if self.provider == "anthropic":
            return os.environ.get("ANTHROPIC_MODEL", "claude-3-5-haiku-latest")
        return os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    def plan(self, question: str) -> PlanResult:
        """Plan a SQL query for ``question``. Raises on API/parse failure."""
        tables = self.retriever.get_relevant_tables(question)
        schema_block = self.retriever.to_prompt(tables)
        user_prompt = self._build_user_prompt(schema_block, question)
        raw = self._call_llm(user_prompt)
        sql = _strip_sql(raw)
        if not sql:
            raise RuntimeError(f"LLM returned no SQL; raw response: {raw!r}")
        return PlanResult(
            sql=sql,
            tables_used=[t.name for t in tables],
            model=self.model,
        )

    def _build_user_prompt(self, schema_block: str, question: str) -> str:
        return (
            f"Database schema (SQLite):\n\n{schema_block}\n\n"
            f"{FEW_SHOT_EXAMPLES}\n"
            f"Question: {question}\nSQL:"
        )

    def _call_llm(self, user_prompt: str) -> str:
        if self.provider == "anthropic":
            return self._call_anthropic(user_prompt)
        return self._call_openai(user_prompt)

    def _call_anthropic(self, user_prompt: str) -> str:
        try:
            import anthropic
        except ImportError as e:
            raise RuntimeError("anthropic SDK not installed") from e
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=self.model,
            max_tokens=512,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        # Concatenate text blocks (claude returns a list of content blocks).
        parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
        return "".join(parts).strip()

    def _call_openai(self, user_prompt: str) -> str:
        try:
            from openai import OpenAI
        except ImportError as e:
            raise RuntimeError("openai SDK not installed") from e
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is not set")
        client = OpenAI()
        resp = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
        )
        return (resp.choices[0].message.content or "").strip()


_FENCE_RE = re.compile(r"^```(?:sql)?\s*\n?|\n?```$", re.IGNORECASE | re.MULTILINE)


def _strip_sql(raw: str) -> str:
    """Strip markdown fences, leading 'SQL:' labels, surrounding whitespace."""
    s = raw.strip()
    s = _FENCE_RE.sub("", s).strip()
    # Drop a leading "SQL:" label if present.
    if s.lower().startswith("sql:"):
        s = s[4:].lstrip()
    return s
