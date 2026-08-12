"""FastAPI app exposing the NL-to-SQL pipeline as a single /ask endpoint."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from api.charts.picker import pick_chart
from api.exec.runner import QueryRunner, QueryTimeoutError
from api.guard.sql_guard import GuardError, guard_sql
from api.planner.llm_planner import LLMPlanner
from api.planner.schema_retriever import SchemaRetriever

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "demo" / "demo.db"
DB_PATH = Path(os.environ.get("INSIGHTENGINE_DB", DEFAULT_DB_PATH))

app = FastAPI(
    title="InsightEngine",
    description="Reference implementation of NL-to-SQL with sqlglot guardrails.",
    version="0.1.0",
)


class AskRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=500)


class AskResponse(BaseModel):
    question: str
    sql: str
    columns: list[str]
    rows: list[list]
    row_count: int
    elapsed_ms: float
    chart: dict
    model: str


def _retriever() -> SchemaRetriever:
    return SchemaRetriever(DB_PATH)


def _runner() -> QueryRunner:
    return QueryRunner(DB_PATH, timeout_seconds=8.0)


def _planner() -> LLMPlanner:
    return LLMPlanner(retriever=_retriever())


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "db_exists": DB_PATH.exists()}


@app.get("/schema")
def schema() -> dict:
    """Inspect the demo schema served to the planner."""
    tables = _retriever().get_all_tables()
    return {
        "tables": [
            {
                "name": t.name,
                "description": t.description,
                "columns": [
                    {"name": c.name, "type": c.type, "description": c.description}
                    for c in t.columns
                ],
            }
            for t in tables
        ]
    }


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest) -> AskResponse:
    """Translate ``question`` into SQL, run it, and return the result + chart."""
    try:
        plan = _planner().plan(req.question)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"planner error: {e}") from e

    try:
        safe_sql = guard_sql(plan.sql, dialect="sqlite")
    except GuardError as e:
        raise HTTPException(status_code=400, detail=f"unsafe SQL rejected: {e}") from e

    try:
        result = _runner().run(safe_sql)
    except QueryTimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"execution error: {e}") from e

    chart_spec = pick_chart(result.columns, result.rows)
    chart_dict = {
        "type": chart_spec.type,
        "x": chart_spec.x,
        "y": chart_spec.y,
        "label": chart_spec.label,
        "value": chart_spec.value,
        "meta": chart_spec.meta,
    }

    return AskResponse(
        question=req.question,
        sql=safe_sql,
        columns=result.columns,
        rows=[list(r) for r in result.rows],
        row_count=result.row_count,
        elapsed_ms=result.elapsed_ms,
        chart=chart_dict,
        model=plan.model,
    )
