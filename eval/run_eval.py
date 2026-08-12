"""Eval harness for the InsightEngine reference implementation.

Modes:
    --offline  (default)  Uses the hand-written reference SQL from the YAML
                          file. Always exercises guard + executor + chart
                          picker. No LLM key required.
    --online              Actually calls the LLM planner. Requires the
                          relevant provider key (ANTHROPIC_API_KEY or
                          OPENAI_API_KEY).

A question is graded as PASS when:
    1. SQL passes the guard.
    2. SQL executes within the timeout.
    3. Result row count satisfies ``expect_rows``.
    4. (online only) Every token in ``must_contain`` appears in the generated
       SQL (case-insensitive).

Usage:
    python eval/run_eval.py
    python eval/run_eval.py --online
    python eval/run_eval.py --questions eval/golden_questions.yml
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from api.exec.runner import QueryRunner, QueryTimeoutError
from api.guard.sql_guard import GuardError, guard_sql

DEFAULT_DB = Path(__file__).resolve().parent.parent / "demo" / "demo.db"
DEFAULT_QUESTIONS = Path(__file__).resolve().parent / "golden_questions.yml"


@dataclass
class EvalResult:
    id: str
    question: str
    sql: str
    passed: bool
    reason: str
    elapsed_ms: float


def _check_rows(rows: list[Any], expect: Any) -> tuple[bool, str]:
    if expect == "single":
        if len(rows) == 1:
            return True, ""
        return False, f"expected 1 row, got {len(rows)}"
    if isinstance(expect, dict) and "min" in expect:
        if len(rows) >= expect["min"]:
            return True, ""
        return False, f"expected >= {expect['min']} rows, got {len(rows)}"
    # default: nonempty
    if rows:
        return True, ""
    return False, "expected nonempty result, got 0 rows"


def _must_contain_ok(sql: str, tokens: list[str]) -> tuple[bool, str]:
    lowered = sql.lower()
    missing = [t for t in tokens if t.lower() not in lowered]
    if missing:
        return False, f"missing tokens: {missing}"
    return True, ""


def evaluate_question(
    q: dict,
    runner: QueryRunner,
    online_planner: Any = None,
) -> EvalResult:
    qid = q["id"]
    question = q["question"]
    expect = q.get("expect_rows", "nonempty")
    must_contain = q.get("must_contain", [])

    start = time.perf_counter()
    try:
        if online_planner is not None:
            plan = online_planner.plan(question)
            raw_sql = plan.sql
        else:
            raw_sql = q["reference_sql"]
        safe_sql = guard_sql(raw_sql, dialect="sqlite")
    except GuardError as e:
        elapsed = (time.perf_counter() - start) * 1000.0
        return EvalResult(qid, question, "", False, f"guard rejected: {e}", elapsed)
    except Exception as e:
        elapsed = (time.perf_counter() - start) * 1000.0
        return EvalResult(qid, question, "", False, f"planner error: {e}", elapsed)

    if online_planner is not None and must_contain:
        ok, why = _must_contain_ok(safe_sql, must_contain)
        if not ok:
            elapsed = (time.perf_counter() - start) * 1000.0
            return EvalResult(qid, question, safe_sql, False, why, elapsed)

    try:
        result = runner.run(safe_sql)
    except QueryTimeoutError as e:
        elapsed = (time.perf_counter() - start) * 1000.0
        return EvalResult(qid, question, safe_sql, False, f"timeout: {e}", elapsed)
    except Exception as e:
        elapsed = (time.perf_counter() - start) * 1000.0
        return EvalResult(qid, question, safe_sql, False, f"exec error: {e}", elapsed)

    ok, why = _check_rows(result.rows, expect)
    elapsed = (time.perf_counter() - start) * 1000.0
    if not ok:
        return EvalResult(qid, question, safe_sql, False, why, elapsed)
    return EvalResult(qid, question, safe_sql, True, "ok", elapsed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument(
        "--online",
        action="store_true",
        help="Call the real LLM planner (requires API key).",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    if not args.db.exists():
        print(f"FATAL: demo DB not found at {args.db}. Run `make seed` first.", file=sys.stderr)
        return 2

    questions = yaml.safe_load(args.questions.read_text(encoding="utf-8"))
    runner = QueryRunner(args.db, timeout_seconds=8.0)

    online_planner = None
    if args.online:
        from api.planner.llm_planner import LLMPlanner
        from api.planner.schema_retriever import SchemaRetriever

        online_planner = LLMPlanner(SchemaRetriever(args.db))
        print(f"Running ONLINE eval with provider={online_planner.provider} "
              f"model={online_planner.model}")
    else:
        print("Running OFFLINE eval using reference SQL "
              "(exercises guard + executor; no LLM calls).")

    results: list[EvalResult] = []
    for q in questions:
        r = evaluate_question(q, runner, online_planner)
        results.append(r)
        marker = "PASS" if r.passed else "FAIL"
        line = f"  [{marker}] {r.id}: {r.reason}  ({r.elapsed_ms:.1f}ms)"
        print(line)
        if args.verbose and not r.passed:
            print(f"       sql: {r.sql[:200]}")

    n_pass = sum(1 for r in results if r.passed)
    n_total = len(results)
    pct = 100.0 * n_pass / n_total if n_total else 0.0
    median_ms = sorted(r.elapsed_ms for r in results)[n_total // 2] if results else 0.0
    print()
    print(f"Results: {n_pass}/{n_total} passed ({pct:.1f}%)  "
          f"median latency: {median_ms:.1f}ms")
    return 0 if n_pass == n_total else 1


if __name__ == "__main__":
    raise SystemExit(main())
