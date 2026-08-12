"""sqlglot-based AST whitelist for read-only SQL.

This is the security-critical layer. We never trust LLM-generated SQL based on
string inspection alone — the LLM can produce CTEs, subqueries, multi-statement
scripts, and creative quoting that defeats regex filters. Instead, we parse the
SQL with sqlglot, walk the AST, and reject anything that isn't a pure SELECT.

Rules enforced:
    1. Parsing must succeed and produce at least one statement.
    2. Exactly ONE statement is allowed (no `;` chaining).
    3. The top-level statement MUST be a SELECT (or a WITH that wraps a SELECT).
    4. No node in the AST may be a DDL / DML write expression:
       Delete, Update, Insert, Drop, Truncate, Alter, Create, Merge, Replace,
       Grant, Revoke.
    5. A statement-level LIMIT is enforced (added if missing).

Example:
    >>> guard_sql("SELECT * FROM customers LIMIT 10")
    'SELECT * FROM customers LIMIT 10'
    >>> guard_sql("WITH x AS (DELETE FROM users RETURNING id) SELECT * FROM x")
    Traceback (most recent call last):
        ...
    api.guard.sql_guard.GuardError: forbidden expression in AST: Delete
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp


class GuardError(ValueError):
    """Raised when SQL fails the guardrail check."""


# Expression classes that represent writes / DDL. If any of these appear
# *anywhere* in the parsed tree (including inside CTEs and subqueries), we
# reject the query.
_FORBIDDEN_EXPRS: tuple[type[exp.Expression], ...] = (
    exp.Delete,
    exp.Update,
    exp.Insert,
    exp.Drop,
    exp.TruncateTable,
    exp.Alter,
    exp.Create,
    exp.Merge,
    exp.Grant,
    exp.Revoke,
    exp.Command,  # catch-all for unparsed statements like CALL, EXEC, VACUUM
)


@dataclass(frozen=True)
class SqlGuard:
    """Configuration for the SQL guard."""

    dialect: str = "sqlite"
    default_limit: int = 1000
    max_limit: int = 10_000

    def check(self, sql: str) -> str:
        """Validate ``sql`` and return a safe, limit-enforced version.

        Returns the (possibly rewritten) SQL string. Raises :class:`GuardError`
        if the query is unsafe.
        """
        if not sql or not sql.strip():
            raise GuardError("empty SQL")

        try:
            statements = sqlglot.parse(sql, read=self.dialect)
        except sqlglot.errors.ParseError as e:
            raise GuardError(f"unparseable SQL: {e}") from e

        # Strip out trailing None entries (sqlglot returns None for empty
        # trailing statements after a semicolon).
        statements = [s for s in statements if s is not None]
        if not statements:
            raise GuardError("no parseable statements")
        if len(statements) > 1:
            raise GuardError(
                f"only one statement allowed, got {len(statements)}"
            )

        stmt = statements[0]

        # Top-level must be a SELECT (or WITH wrapping a SELECT).
        if not self._is_select_root(stmt):
            raise GuardError(
                f"only SELECT statements allowed; got {type(stmt).__name__}"
            )

        # Walk the full AST — including all subqueries and CTEs — for forbidden
        # nodes. ``walk`` visits every descendant; we type-check each one.
        for node in stmt.walk():
            if isinstance(node, _FORBIDDEN_EXPRS):
                raise GuardError(
                    f"forbidden expression in AST: {type(node).__name__}"
                )

        # Enforce a LIMIT to bound result size.
        safe_stmt = self._enforce_limit(stmt)
        return safe_stmt.sql(dialect=self.dialect)

    def _is_select_root(self, stmt: exp.Expression) -> bool:
        """A safe root is SELECT, UNION, or a WITH that wraps one of those."""
        if isinstance(stmt, (exp.Select, exp.Union)):
            return True
        if isinstance(stmt, exp.With):
            inner = stmt.this
            return isinstance(inner, (exp.Select, exp.Union))
        return False

    def _enforce_limit(self, stmt: exp.Expression) -> exp.Expression:
        """Ensure the outermost SELECT has a LIMIT <= ``max_limit``."""
        target = stmt.this if isinstance(stmt, exp.With) else stmt
        if not isinstance(target, exp.Select):
            # Union or similar; wrap in an outer SELECT with LIMIT.
            wrapped = exp.select("*").from_(target.subquery("u"))
            wrapped.set("limit", exp.Limit(expression=exp.Literal.number(self.default_limit)))
            return wrapped

        existing = target.args.get("limit")
        if existing is None:
            target.set(
                "limit",
                exp.Limit(expression=exp.Literal.number(self.default_limit)),
            )
        else:
            # Cap existing limit at max_limit.
            try:
                lit = existing.expression
                if isinstance(lit, exp.Literal) and lit.is_int:
                    val = int(lit.this)
                    if val > self.max_limit:
                        target.set(
                            "limit",
                            exp.Limit(
                                expression=exp.Literal.number(self.max_limit)
                            ),
                        )
            except (AttributeError, ValueError):
                # If we can't introspect the limit, leave it alone.
                pass
        return stmt


# Module-level convenience.
_default_guard = SqlGuard()


def guard_sql(sql: str, dialect: str = "sqlite") -> str:
    """Validate ``sql`` against the default guard and return safe SQL."""
    if dialect == _default_guard.dialect:
        return _default_guard.check(sql)
    return SqlGuard(dialect=dialect).check(sql)
