"""Create and seed the demo e-commerce SQLite database.

Produces ~500 rows across 5 tables:
    customers, products, orders, order_items, support_tickets

The data is deterministic — we seed Faker and ``random`` so reruns produce
identical results, which is important for the eval harness.

Usage:
    python -m demo.seed
    # or: make seed
"""

from __future__ import annotations

import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from faker import Faker

DB_PATH = Path(__file__).resolve().parent / "demo.db"

SEED = 42
N_CUSTOMERS = 200
N_PRODUCTS = 40
N_ORDERS = 350
N_TICKETS = 80
DAYS_OF_HISTORY = 540  # ~18 months

COUNTRIES = ["ZA", "US", "GB", "DE", "AU", "KE", "NG", "IN", "BR", "JP"]
TIERS = ["free", "pro", "enterprise"]
TIER_WEIGHTS = [0.6, 0.3, 0.1]
ORDER_STATUSES = ["pending", "paid", "shipped", "refunded", "cancelled"]
ORDER_STATUS_WEIGHTS = [0.05, 0.35, 0.50, 0.05, 0.05]
TICKET_STATUSES = ["open", "pending", "resolved"]
TICKET_SUBJECTS = [
    "Where is my order?",
    "Refund request",
    "Wrong item received",
    "Damaged on arrival",
    "Cannot log in",
    "Discount code not working",
    "Change shipping address",
    "Cancel my order",
    "Product question",
    "Invoice request",
]
CATEGORIES = ["electronics", "apparel", "home", "books"]

SCHEMA_SQL = """
CREATE TABLE customers (
    id           INTEGER PRIMARY KEY,
    email        TEXT NOT NULL UNIQUE,
    name         TEXT NOT NULL,
    country      TEXT NOT NULL,
    tier         TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE INDEX idx_customers_created_at ON customers(created_at);
CREATE INDEX idx_customers_tier ON customers(tier);

CREATE TABLE products (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    category     TEXT NOT NULL,
    price_cents  INTEGER NOT NULL
);
CREATE INDEX idx_products_category ON products(category);

CREATE TABLE orders (
    id           INTEGER PRIMARY KEY,
    customer_id  INTEGER NOT NULL REFERENCES customers(id),
    status       TEXT NOT NULL,
    total_cents  INTEGER NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE INDEX idx_orders_customer ON orders(customer_id);
CREATE INDEX idx_orders_created_at ON orders(created_at);
CREATE INDEX idx_orders_status ON orders(status);

CREATE TABLE order_items (
    id               INTEGER PRIMARY KEY,
    order_id         INTEGER NOT NULL REFERENCES orders(id),
    product_id       INTEGER NOT NULL REFERENCES products(id),
    quantity         INTEGER NOT NULL,
    unit_price_cents INTEGER NOT NULL
);
CREATE INDEX idx_order_items_order ON order_items(order_id);
CREATE INDEX idx_order_items_product ON order_items(product_id);

CREATE TABLE support_tickets (
    id           INTEGER PRIMARY KEY,
    customer_id  INTEGER NOT NULL REFERENCES customers(id),
    subject      TEXT NOT NULL,
    status       TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    resolved_at  TEXT
);
CREATE INDEX idx_tickets_customer ON support_tickets(customer_id);
CREATE INDEX idx_tickets_status ON support_tickets(status);
"""


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def seed(db_path: Path = DB_PATH) -> None:
    """Create and populate the demo database. Overwrites any existing file."""
    rng = random.Random(SEED)
    fake = Faker()
    Faker.seed(SEED)

    if db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL)
        now = datetime(2026, 6, 24, 12, 0, 0)
        history_start = now - timedelta(days=DAYS_OF_HISTORY)

        # ---- customers ---------------------------------------------------
        customers = []
        used_emails: set[str] = set()
        for cid in range(1, N_CUSTOMERS + 1):
            email = fake.unique.email()
            used_emails.add(email)
            customers.append(
                (
                    cid,
                    email,
                    fake.name(),
                    rng.choice(COUNTRIES),
                    rng.choices(TIERS, weights=TIER_WEIGHTS, k=1)[0],
                    _iso(_random_dt(rng, history_start, now)),
                )
            )
        conn.executemany(
            "INSERT INTO customers (id,email,name,country,tier,created_at) "
            "VALUES (?,?,?,?,?,?)",
            customers,
        )

        # ---- products ----------------------------------------------------
        products = []
        for pid in range(1, N_PRODUCTS + 1):
            cat = rng.choice(CATEGORIES)
            products.append(
                (
                    pid,
                    f"{cat.title()} item {pid:02d}",
                    cat,
                    rng.randint(500, 50_000),  # $5 - $500
                )
            )
        conn.executemany(
            "INSERT INTO products (id,name,category,price_cents) VALUES (?,?,?,?)",
            products,
        )

        # ---- orders + order_items ---------------------------------------
        orders = []
        items = []
        item_id = 1
        for oid in range(1, N_ORDERS + 1):
            customer_id = rng.randint(1, N_CUSTOMERS)
            status = rng.choices(ORDER_STATUSES, weights=ORDER_STATUS_WEIGHTS, k=1)[0]
            order_dt = _random_dt(rng, history_start, now)
            n_items = rng.randint(1, 4)
            chosen_products = rng.sample(products, k=n_items)
            total = 0
            order_items_buf = []
            for prod in chosen_products:
                qty = rng.randint(1, 3)
                unit_price = prod[3]
                total += qty * unit_price
                order_items_buf.append((item_id, oid, prod[0], qty, unit_price))
                item_id += 1
            orders.append((oid, customer_id, status, total, _iso(order_dt)))
            items.extend(order_items_buf)

        conn.executemany(
            "INSERT INTO orders (id,customer_id,status,total_cents,created_at) "
            "VALUES (?,?,?,?,?)",
            orders,
        )
        conn.executemany(
            "INSERT INTO order_items (id,order_id,product_id,quantity,unit_price_cents) "
            "VALUES (?,?,?,?,?)",
            items,
        )

        # ---- support_tickets --------------------------------------------
        tickets = []
        for tid in range(1, N_TICKETS + 1):
            customer_id = rng.randint(1, N_CUSTOMERS)
            opened = _random_dt(rng, history_start, now)
            status = rng.choice(TICKET_STATUSES)
            resolved = None
            if status == "resolved":
                resolved = _iso(opened + timedelta(hours=rng.randint(1, 240)))
            tickets.append(
                (
                    tid,
                    customer_id,
                    rng.choice(TICKET_SUBJECTS),
                    status,
                    _iso(opened),
                    resolved,
                )
            )
        conn.executemany(
            "INSERT INTO support_tickets "
            "(id,customer_id,subject,status,created_at,resolved_at) "
            "VALUES (?,?,?,?,?,?)",
            tickets,
        )

        conn.commit()
    finally:
        conn.close()

    print(
        f"seeded {db_path}: "
        f"{N_CUSTOMERS} customers, {N_PRODUCTS} products, "
        f"{N_ORDERS} orders, {len(items)} order_items, {N_TICKETS} tickets"
    )


def _random_dt(rng: random.Random, start: datetime, end: datetime) -> datetime:
    delta = end - start
    seconds = rng.randint(0, int(delta.total_seconds()))
    return start + timedelta(seconds=seconds)


if __name__ == "__main__":
    seed()
