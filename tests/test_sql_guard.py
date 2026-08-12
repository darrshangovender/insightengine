"""Tests for the sqlglot AST whitelist guard.

The guard is the security-critical component of InsightEngine. These tests
cover both well-formed read queries (which should pass) and adversarial
inputs (which must be rejected). Each adversarial case is taken from a
class of bypass that string-based filters typically miss.
"""

from __future__ import annotations

import pytest

from api.guard.sql_guard import GuardError, SqlGuard, guard_sql


# ---------------------------------------------------------------------------
# Well-formed SELECT queries — should pass and be returned with a LIMIT.
# ---------------------------------------------------------------------------

class TestAllowedQueries:
    def test_simple_select_passes(self):
        out = guard_sql("SELECT id, name FROM customers")
        assert "SELECT" in out.upper()
        assert "customers" in out.lower()

    def test_select_with_where(self):
        out = guard_sql("SELECT * FROM orders WHERE status = 'paid'")
        assert "paid" in out

    def test_select_with_join(self):
        sql = (
            "SELECT c.name, o.total_cents FROM customers c "
            "JOIN orders o ON o.customer_id = c.id"
        )
        out = guard_sql(sql)
        assert "JOIN" in out.upper()

    def test_select_with_cte(self):
        sql = (
            "WITH recent AS (SELECT * FROM orders WHERE status='paid') "
            "SELECT COUNT(*) FROM recent"
        )
        out = guard_sql(sql)
        assert "WITH" in out.upper()

    def test_select_with_subquery(self):
        sql = (
            "SELECT name FROM customers "
            "WHERE id IN (SELECT customer_id FROM orders WHERE status='paid')"
        )
        out = guard_sql(sql)
        assert "SELECT" in out.upper()

    def test_union_select(self):
        sql = (
            "SELECT id FROM customers "
            "UNION SELECT customer_id FROM orders"
        )
        out = guard_sql(sql)
        assert "UNION" in out.upper()

    def test_aggregate_with_group_by(self):
        sql = "SELECT tier, COUNT(*) FROM customers GROUP BY tier"
        out = guard_sql(sql)
        assert "GROUP BY" in out.upper()

    def test_trailing_semicolon_ok(self):
        out = guard_sql("SELECT 1;")
        assert "SELECT" in out.upper()


# ---------------------------------------------------------------------------
# Limit enforcement — the guard rewrites/caps LIMIT.
# ---------------------------------------------------------------------------

class TestLimitEnforcement:
    def test_missing_limit_is_added(self):
        out = guard_sql("SELECT * FROM customers")
        assert "LIMIT" in out.upper()

    def test_explicit_limit_under_max_preserved(self):
        out = guard_sql("SELECT * FROM customers LIMIT 10")
        assert "10" in out

    def test_excessive_limit_capped(self):
        guard = SqlGuard(max_limit=100, default_limit=50)
        out = guard.check("SELECT * FROM customers LIMIT 999999")
        assert "100" in out
        assert "999999" not in out


# ---------------------------------------------------------------------------
# Forbidden top-level statements.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM customers",
        "DELETE FROM customers WHERE id = 1",
        "UPDATE customers SET tier='enterprise' WHERE id=1",
        "INSERT INTO customers (id,email) VALUES (1,'x@y.z')",
        "DROP TABLE customers",
        "TRUNCATE TABLE customers",
        "ALTER TABLE customers ADD COLUMN evil TEXT",
        "CREATE TABLE evil (id INTEGER)",
        "GRANT ALL ON customers TO public",
        "REVOKE ALL ON customers FROM public",
    ],
)
def test_top_level_writes_are_rejected(sql):
    with pytest.raises(GuardError):
        guard_sql(sql)


# ---------------------------------------------------------------------------
# Adversarial inputs — these are the ones a regex filter would miss.
# ---------------------------------------------------------------------------

class TestAdversarialBypassAttempts:
    def test_cte_hiding_a_delete_is_rejected(self):
        """A CTE wrapping a DELETE…RETURNING reads like a SELECT to regex."""
        sql = (
            "WITH x AS (DELETE FROM customers RETURNING id) "
            "SELECT * FROM x"
        )
        with pytest.raises(GuardError):
            guard_sql(sql)

    def test_cte_hiding_an_update_is_rejected(self):
        sql = (
            "WITH x AS (UPDATE customers SET tier='free' RETURNING id) "
            "SELECT * FROM x"
        )
        with pytest.raises(GuardError):
            guard_sql(sql, dialect="postgres")

    def test_cte_hiding_an_insert_is_rejected(self):
        sql = (
            "WITH x AS (INSERT INTO customers (id,email) VALUES (99,'a@b.c') "
            "RETURNING id) SELECT * FROM x"
        )
        with pytest.raises(GuardError):
            guard_sql(sql, dialect="postgres")

    def test_two_statements_rejected(self):
        """Common SQL-injection shape: trailing piggyback statement."""
        sql = "SELECT * FROM customers; DROP TABLE customers"
        with pytest.raises(GuardError):
            guard_sql(sql)

    def test_two_selects_also_rejected(self):
        """Multi-statement is rejected even when both are SELECTs."""
        with pytest.raises(GuardError):
            guard_sql("SELECT 1; SELECT 2")

    def test_subquery_with_delete_rejected(self):
        sql = (
            "SELECT * FROM customers "
            "WHERE id IN (DELETE FROM orders RETURNING customer_id)"
        )
        with pytest.raises(GuardError):
            guard_sql(sql, dialect="postgres")

    def test_unparseable_garbage_rejected(self):
        with pytest.raises(GuardError):
            guard_sql("not even close to valid SQL ;;;;")

    def test_empty_input_rejected(self):
        with pytest.raises(GuardError):
            guard_sql("")

    def test_whitespace_only_rejected(self):
        with pytest.raises(GuardError):
            guard_sql("   \n   ")

    def test_call_or_exec_rejected(self):
        """Stored-proc invocations parse as Command, not Select."""
        with pytest.raises(GuardError):
            guard_sql("CALL do_evil()")

    def test_commented_out_write_is_safe(self):
        """Comments are inert — a commented DELETE is just a SELECT."""
        sql = "SELECT 1 /* DELETE FROM customers */"
        out = guard_sql(sql)
        assert "SELECT" in out.upper()
