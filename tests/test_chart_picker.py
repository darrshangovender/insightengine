"""Tests for the chart-shape heuristic."""

from __future__ import annotations

from api.charts.picker import pick_chart


def test_empty_result_is_table():
    spec = pick_chart(["x"], [])
    assert spec.type == "table"
    assert spec.meta["empty"] is True


def test_single_scalar_is_kpi():
    spec = pick_chart(["total"], [(42,)])
    assert spec.type == "kpi"
    assert spec.label == "total"
    assert spec.value == 42


def test_date_column_with_metric_is_line():
    spec = pick_chart(["month", "revenue"], [("2026-01", 1234.5), ("2026-02", 999.0)])
    assert spec.type == "line"
    assert spec.x == "month"
    assert spec.y == "revenue"


def test_created_at_column_is_line():
    spec = pick_chart(["created_at", "n"], [("2026-01-01", 5), ("2026-01-02", 8)])
    assert spec.type == "line"


def test_categorical_with_metric_is_bar():
    spec = pick_chart(["tier", "n"], [("free", 100), ("pro", 50)])
    assert spec.type == "bar"
    assert spec.x == "tier"
    assert spec.y == "n"


def test_two_numerics_is_scatter():
    spec = pick_chart(["x", "y"], [(1, 2), (3, 4)])
    assert spec.type == "scatter"


def test_many_columns_is_table():
    spec = pick_chart(
        ["a", "b", "c", "d"],
        [("x", "y", "z", "w")],
    )
    assert spec.type == "table"
