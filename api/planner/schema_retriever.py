"""Schema retrieval for the planner.

In production, this would do embedding-based retrieval across hundreds of
tables and return only the relevant slice. For the reference implementation,
the demo schema is small (5 tables) so we return all of them — the *interface*
is the same as the production retriever, so the planner code is unchanged.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    description: str = ""


@dataclass(frozen=True)
class TableSchema:
    name: str
    columns: list[Column]
    description: str = ""

    def to_prompt(self) -> str:
        """Render this table as a compact prompt fragment."""
        col_lines = []
        for c in self.columns:
            doc = f" -- {c.description}" if c.description else ""
            col_lines.append(f"    {c.name} {c.type}{doc}")
        head = f"-- {self.description}\n" if self.description else ""
        return f"{head}CREATE TABLE {self.name} (\n" + ",\n".join(col_lines) + "\n);"


# Column-level documentation for the demo e-commerce schema. In production this
# would live in a docstore alongside the embedding index.
_COLUMN_DOCS: dict[tuple[str, str], str] = {
    ("customers", "id"): "primary key",
    ("customers", "email"): "unique email",
    ("customers", "created_at"): "signup timestamp (ISO 8601)",
    ("customers", "country"): "ISO country code (e.g. ZA, US)",
    ("customers", "tier"): "subscription tier: free, pro, enterprise",
    ("products", "id"): "primary key",
    ("products", "name"): "human-readable product name",
    ("products", "category"): "category slug (electronics, apparel, home, books)",
    ("products", "price_cents"): "price in minor units (cents)",
    ("orders", "id"): "primary key",
    ("orders", "customer_id"): "FK to customers.id",
    ("orders", "status"): "one of: pending, paid, shipped, refunded, cancelled",
    ("orders", "total_cents"): "order total in cents (sum of line items)",
    ("orders", "created_at"): "order placement timestamp",
    ("order_items", "order_id"): "FK to orders.id",
    ("order_items", "product_id"): "FK to products.id",
    ("order_items", "quantity"): "number of units ordered",
    ("order_items", "unit_price_cents"): "snapshot of price at order time",
    ("support_tickets", "customer_id"): "FK to customers.id",
    ("support_tickets", "subject"): "short ticket subject",
    ("support_tickets", "status"): "open, pending, resolved",
    ("support_tickets", "created_at"): "ticket open timestamp",
    ("support_tickets", "resolved_at"): "ticket resolved timestamp (nullable)",
}

_TABLE_DOCS: dict[str, str] = {
    "customers": "Registered users of the store.",
    "products": "Catalog of items for sale.",
    "orders": "One row per customer order.",
    "order_items": "Line items belonging to an order.",
    "support_tickets": "Customer support inquiries.",
}


class SchemaRetriever:
    """Reads schema from a SQLite DB and annotates it with descriptions."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    def get_relevant_tables(self, question: str) -> list[TableSchema]:
        """Return tables relevant to ``question``.

        For the demo schema we return all tables. In production this would
        embed the question, look up the top-k tables in a vector store, and
        return just those.
        """
        del question  # unused in the reference implementation
        return self.get_all_tables()

    def get_all_tables(self) -> list[TableSchema]:
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"demo DB not found at {self.db_path}; run `make seed` first"
            )
        conn = sqlite3.connect(self.db_path)
        try:
            tables: list[TableSchema] = []
            rows = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            ).fetchall()
            for (table_name,) in rows:
                col_rows = conn.execute(
                    f"PRAGMA table_info({table_name})"
                ).fetchall()
                columns = [
                    Column(
                        name=row[1],
                        type=row[2],
                        description=_COLUMN_DOCS.get((table_name, row[1]), ""),
                    )
                    for row in col_rows
                ]
                tables.append(
                    TableSchema(
                        name=table_name,
                        columns=columns,
                        description=_TABLE_DOCS.get(table_name, ""),
                    )
                )
            return tables
        finally:
            conn.close()

    def to_prompt(self, tables: list[TableSchema]) -> str:
        """Render a list of tables as a schema block for the LLM prompt."""
        return "\n\n".join(t.to_prompt() for t in tables)
