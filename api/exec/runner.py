"""Read-only query execution against the demo SQLite database.

We open the SQLite database in read-only mode using a URI connection string
(``file:...?mode=ro``). This is defence-in-depth: even if the SQL guard is
bypassed, the database connection itself rejects writes at the driver level.

A statement-level timeout is enforced via SQLite's progress handler.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


class QueryTimeoutError(RuntimeError):
    """Raised when a query exceeds the configured timeout."""


@dataclass(frozen=True)
class ExecResult:
    """Result of executing a query."""

    columns: list[str]
    rows: list[tuple]
    elapsed_ms: float

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def to_records(self) -> list[dict]:
        """Return rows as a list of column-name -> value dicts."""
        return [dict(zip(self.columns, r)) for r in self.rows]


class QueryRunner:
    """Runs SELECT queries against a read-only SQLite database."""

    def __init__(self, db_path: str | Path, timeout_seconds: float = 8.0):
        self.db_path = Path(db_path)
        self.timeout_seconds = timeout_seconds

    def run(self, sql: str) -> ExecResult:
        """Execute ``sql`` and return columns + rows.

        Raises :class:`QueryTimeoutError` if the query exceeds the timeout, or
        :class:`sqlite3.DatabaseError` for other DB errors.
        """
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"demo DB not found at {self.db_path}; run `make seed` first"
            )
        uri = f"file:{self.db_path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            self._install_timeout(conn)
            start = time.perf_counter()
            try:
                cursor = conn.execute(sql)
            except sqlite3.OperationalError as e:
                if "interrupted" in str(e).lower():
                    raise QueryTimeoutError(
                        f"query exceeded {self.timeout_seconds}s timeout"
                    ) from e
                raise
            rows = cursor.fetchall()
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            columns = [d[0] for d in cursor.description] if cursor.description else []
            return ExecResult(columns=columns, rows=rows, elapsed_ms=elapsed_ms)
        finally:
            conn.close()

    def _install_timeout(self, conn: sqlite3.Connection) -> None:
        """Install a progress handler that interrupts long-running queries."""
        deadline = time.perf_counter() + self.timeout_seconds

        def _progress() -> int:
            # Returning non-zero aborts the running query.
            return 1 if time.perf_counter() > deadline else 0

        # 1000 VM instructions ~= a few microseconds; cheap enough to poll.
        conn.set_progress_handler(_progress, 1000)
