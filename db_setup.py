"""
Database initialization and realistic retail dataset seeder.

Creates a local SQLite database (`retail_analytics.db`) with five relational
tables (`sales_reps`, `customers`, `products`, `orders`, `order_details`) and
seeds ~500 multi-month retail transactions when the database does not yet exist.
"""

from __future__ import annotations

import argparse
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent / "retail_analytics.db"

REGIONS = ["Metro Manila", "Cebu", "Davao", "Luzon", "Visayas"]
REGION_WEIGHTS = [0.38, 0.20, 0.14, 0.16, 0.12]

REGION_CITIES: dict[str, list[str]] = {
    "Metro Manila": ["Makati", "Taguig (BGC)", "Quezon City", "Pasig", "Mandaluyong"],
    "Cebu": ["Cebu City", "Mandaue", "Lapu-Lapu"],
    "Davao": ["Davao City", "Tagum", "Panabo"],
    "Luzon": ["Angeles", "Santa Rosa", "Baguio", "Batangas City"],
    "Visayas": ["Iloilo City", "Bacolod", "Tacloban"],
}

# Slight regional variance in return rates so analytics queries surface clear insights.
REGION_STATUS_WEIGHTS: dict[str, list[float]] = {
    "Metro Manila": [0.82, 0.10, 0.08],  # Completed, Returned, Pending
    "Cebu": [0.80, 0.12, 0.08],
    "Davao": [0.76, 0.16, 0.08],
    "Luzon": [0.81, 0.11, 0.08],
    "Visayas": [0.71, 0.21, 0.08],
}
ORDER_STATUSES = ["Completed", "Returned", "Pending"]

SALES_REPS_SEED = [
    ("Rep-01", "Camille Reyes", "camille.reyes@retailco.ph", "Metro Manila", "2022-03-15", 450000.0),
    ("Rep-02", "Marco Villanueva", "marco.villanueva@retailco.ph", "Metro Manila", "2021-11-01", 480000.0),
    ("Rep-03", "Bianca Santos", "bianca.santos@retailco.ph", "Cebu", "2023-01-10", 350000.0),
    ("Rep-04", "Miguel Mendoza", "miguel.mendoza@retailco.ph", "Davao", "2022-07-20", 320000.0),
    ("Rep-05", "Trisha Lim", "trisha.lim@retailco.ph", "Luzon", "2023-04-05", 340000.0),
    ("Rep-06", "Paolo Navarro", "paolo.navarro@retailco.ph", "Visayas", "2022-09-12", 300000.0),
    ("Rep-07", "Andrea Bautista", "andrea.bautista@retailco.ph", "Metro Manila", "2023-06-18", 420000.0),
    ("Rep-08", "Rafael Aquino", "rafael.aquino@retailco.ph", "Cebu", "2024-01-08", 310000.0),
]

PRODUCTS_SEED = [
    # Electronics
    ("ProBook 14 Laptop", "Electronics", 54990.0, 39500.0),
    ("UltraWide 34\" Monitor", "Electronics", 28500.0, 19800.0),
    ("27\" 4K IPS Display", "Electronics", 21900.0, 15200.0),
    ("Noise-Canceling Headset", "Electronics", 11490.0, 7200.0),
    ("4K Conference Webcam", "Electronics", 6890.0, 4100.0),
    # Office Gear
    ("Ergonomic Mesh Chair", "Office Gear", 14900.0, 9200.0),
    ("Dual-Motor Standing Desk", "Office Gear", 24500.0, 16100.0),
    ("Acoustic Desk Divider", "Office Gear", 4200.0, 2400.0),
    ("Heavy-Duty Monitor Arm", "Office Gear", 3850.0, 2100.0),
    ("Smart LED Desk Lamp", "Office Gear", 2650.0, 1450.0),
    # Software Licenses
    ("Team Productivity Suite (Annual)", "Software Licenses", 18500.0, 4500.0),
    ("Cloud Security Shield (Annual)", "Software Licenses", 12900.0, 3200.0),
    ("BI Dashboard Pro Seat (Annual)", "Software Licenses", 22000.0, 5100.0),
    ("Design Studio License (Annual)", "Software Licenses", 16400.0, 4000.0),
    # Accessories
    ("Mechanical Wireless Keyboard", "Accessories", 4950.0, 2700.0),
    ("Precision Ergonomic Mouse", "Accessories", 3290.0, 1750.0),
    ("Thunderbolt 4 Docking Station", "Accessories", 9800.0, 5900.0),
    ("100W GaN USB-C Charger", "Accessories", 2450.0, 1200.0),
    ("Laptop Protective Sleeve 14\"", "Accessories", 1350.0, 620.0),
    ("USB-C 8-in-1 Travel Hub", "Accessories", 2150.0, 1050.0),
]

COMPANY_PREFIXES = [
    "Pacific", "Archipelago", "Vanguard", "Summit", "Crestline", "Meridian",
    "Solstice", "Harbor", "Metro", "Pinnacle", "Horizon", "Vertex",
    "Catalyst", "Bluewave", "Northstar", "Bayview", "Keystone", "Prime",
]
COMPANY_SUFFIXES = [
    "Ventures", "Logistics", "Solutions", "Technologies", "Retail Group",
    "Holdings", "Labs", "Commerce", "Systems", "Digital", "Partners", "Industries",
]
FIRST_NAMES = [
    "Carlos", "Elena", "Daniel", "Sofia", "Gabriel", "Isabella", "Mateo",
    "Patricia", "Lorenzo", "Clarisse", "Enrique", "Katrina", "Adrian", "Nicole",
]
LAST_NAMES = [
    "Cruz", "Garcia", "Torres", "Ramos", "Flores", "Castillo", "Rivera",
    "Gonzales", "Del Rosario", "Salazar", "Soriano", "Tan", "Sy", "Ocampo",
]
SEGMENTS = ["Enterprise", "Mid-Market", "SMB", "Startup"]
SEGMENT_WEIGHTS = [0.20, 0.30, 0.32, 0.18]


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sales_reps (
    rep_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    rep_code        TEXT NOT NULL UNIQUE,
    rep_name        TEXT NOT NULL,
    email           TEXT NOT NULL UNIQUE,
    region          TEXT NOT NULL,
    hire_date       TEXT NOT NULL,
    monthly_quota   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS customers (
    customer_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    company_name    TEXT NOT NULL,
    contact_name    TEXT NOT NULL,
    segment         TEXT NOT NULL,
    region          TEXT NOT NULL,
    city            TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    product_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    product_name    TEXT NOT NULL,
    category        TEXT NOT NULL,
    unit_price      REAL NOT NULL,
    unit_cost       REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    order_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id     INTEGER NOT NULL,
    rep_id          INTEGER NOT NULL,
    order_date      TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('Completed', 'Returned', 'Pending')),
    region          TEXT NOT NULL,
    shipping_fee    REAL NOT NULL DEFAULT 0.0,
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id),
    FOREIGN KEY (rep_id) REFERENCES sales_reps(rep_id)
);

CREATE TABLE IF NOT EXISTS order_details (
    detail_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id        INTEGER NOT NULL,
    product_id      INTEGER NOT NULL,
    quantity        INTEGER NOT NULL CHECK (quantity > 0),
    unit_price      REAL NOT NULL,
    discount_pct    REAL NOT NULL DEFAULT 0.0 CHECK (discount_pct >= 0.0 AND discount_pct <= 0.5),
    line_total      REAL NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(order_id),
    FOREIGN KEY (product_id) REFERENCES products(product_id)
);

CREATE INDEX IF NOT EXISTS idx_orders_date ON orders(order_date);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_region ON orders(region);
CREATE INDEX IF NOT EXISTS idx_order_details_order ON order_details(order_id);
CREATE INDEX IF NOT EXISTS idx_order_details_product ON order_details(product_id);
"""


def _database_is_populated(db_path: Path) -> bool:
    """Return True if the SQLite database exists and already has seeded orders."""
    if not db_path.exists() or db_path.stat().st_size == 0:
        return False
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='orders';"
            )
            if cursor.fetchone()[0] == 0:
                return False
            order_count = conn.execute("SELECT count(*) FROM orders;").fetchone()[0]
            return order_count > 0
    except sqlite3.Error:
        return False


def _seed_database(conn: sqlite3.Connection, num_orders: int = 500, seed: int = 42) -> None:
    """Populate all five retail tables with realistic 12-month transaction data."""
    rng = random.Random(seed)
    today = date.today()
    start_date = today - timedelta(days=364)

    # 1. Insert sales reps
    conn.executemany(
        """
        INSERT INTO sales_reps (rep_code, rep_name, email, region, hire_date, monthly_quota)
        VALUES (?, ?, ?, ?, ?, ?);
        """,
        SALES_REPS_SEED,
    )
    rep_rows = conn.execute("SELECT rep_id, region FROM sales_reps;").fetchall()
    reps_by_region: dict[str, list[int]] = {}
    for rep_id, region in rep_rows:
        reps_by_region.setdefault(region, []).append(rep_id)
    all_rep_ids = [r[0] for r in rep_rows]

    # 2. Insert products
    conn.executemany(
        """
        INSERT INTO products (product_name, category, unit_price, unit_cost)
        VALUES (?, ?, ?, ?);
        """,
        PRODUCTS_SEED,
    )
    product_rows = conn.execute("SELECT product_id, category, unit_price FROM products;").fetchall()

    # 3. Insert customers (80 unique B2B / retail accounts)
    customers_data = []
    used_companies: set[str] = set()
    for _ in range(80):
        while True:
            company = f"{rng.choice(COMPANY_PREFIXES)} {rng.choice(COMPANY_SUFFIXES)}"
            if company not in used_companies:
                used_companies.add(company)
                break
        contact = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
        segment = rng.choices(SEGMENTS, weights=SEGMENT_WEIGHTS, k=1)[0]
        region = rng.choices(REGIONS, weights=REGION_WEIGHTS, k=1)[0]
        city = rng.choice(REGION_CITIES[region])
        created_days_ago = rng.randint(120, 730)
        created_at = (today - timedelta(days=created_days_ago)).isoformat()
        customers_data.append((company, contact, segment, region, city, created_at))

    conn.executemany(
        """
        INSERT INTO customers (company_name, contact_name, segment, region, city, created_at)
        VALUES (?, ?, ?, ?, ?, ?);
        """,
        customers_data,
    )
    customer_rows = conn.execute("SELECT customer_id, segment, region FROM customers;").fetchall()

    # Give Enterprise / Mid-Market accounts slightly higher repeat order likelihood
    segment_order_weight = {"Enterprise": 2.4, "Mid-Market": 1.6, "SMB": 1.0, "Startup": 0.9}
    customer_weights = [segment_order_weight[c[1]] for c in customer_rows]

    # 4. Insert ~500 orders spanning the past 12 months with realistic seasonality
    discount_choices = [0.0, 0.0, 0.0, 0.05, 0.10, 0.15, 0.20]

    for _ in range(num_orders):
        customer_id, segment, region = rng.choices(customer_rows, weights=customer_weights, k=1)[0]

        # 85% chance the order is handled by a rep in the customer's region
        if region in reps_by_region and rng.random() < 0.85:
            rep_id = rng.choice(reps_by_region[region])
        else:
            rep_id = rng.choice(all_rep_ids)

        # Mild upward growth trend across the 12-month window
        day_offset = int((rng.random() ** 0.88) * 364)
        order_dt = start_date + timedelta(days=day_offset)

        # Recent orders (last 10 days) are more likely to be Pending
        if (today - order_dt).days <= 10 and rng.random() < 0.45:
            status = "Pending"
        else:
            status = rng.choices(ORDER_STATUSES, weights=REGION_STATUS_WEIGHTS[region], k=1)[0]

        shipping_fee = 0.0 if segment == "Enterprise" else rng.choice([0.0, 150.0, 250.0, 350.0])

        cursor = conn.execute(
            """
            INSERT INTO orders (customer_id, rep_id, order_date, status, region, shipping_fee)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            (customer_id, rep_id, order_dt.isoformat(), status, region, shipping_fee),
        )
        order_id = cursor.lastrowid

        # 1 to 4 distinct line items per order
        num_items = rng.choices([1, 2, 3, 4], weights=[0.35, 0.35, 0.20, 0.10], k=1)[0]
        chosen_products = rng.sample(product_rows, k=num_items)

        detail_rows = []
        for product_id, category, unit_price in chosen_products:
            if segment == "Enterprise":
                qty = rng.randint(2, 8)
            elif segment == "Mid-Market":
                qty = rng.randint(1, 5)
            else:
                qty = rng.randint(1, 3)

            # Software licenses rarely have discounts above 10%
            if category == "Software Licenses":
                discount_pct = rng.choice([0.0, 0.0, 0.05, 0.10])
            else:
                discount_pct = rng.choice(discount_choices)

            line_total = round(qty * unit_price * (1.0 - discount_pct), 2)
            detail_rows.append((order_id, product_id, qty, unit_price, discount_pct, line_total))

        conn.executemany(
            """
            INSERT INTO order_details (order_id, product_id, quantity, unit_price, discount_pct, line_total)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            detail_rows,
        )


def init_db(db_path: Path | str = DEFAULT_DB_PATH, force: bool = False, num_orders: int = 500) -> Path:
    """
    Initialize and seed `retail_analytics.db` if it does not already exist.

    Args:
        db_path: Target SQLite file path.
        force: If True, recreates and re-seeds the database from scratch.
        num_orders: Number of retail orders to generate.

    Returns:
        Resolved Path to the SQLite database file.
    """
    resolved_path = Path(db_path).resolve()
    resolved_path.parent.mkdir(parents=True, exist_ok=True)

    if force and resolved_path.exists():
        resolved_path.unlink()

    if _database_is_populated(resolved_path):
        return resolved_path

    with sqlite3.connect(resolved_path) as conn:
        conn.executescript(SCHEMA_SQL)
        _seed_database(conn, num_orders=num_orders)
        conn.commit()

    return resolved_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Initialize and seed retail_analytics.db")
    parser.add_argument("--db", type=str, default=str(DEFAULT_DB_PATH), help="Path to SQLite database")
    parser.add_argument("--force", action="store_true", help="Recreate database even if it exists")
    parser.add_argument("--orders", type=int, default=500, help="Number of orders to seed")
    args = parser.parse_args()

    path = init_db(db_path=args.db, force=args.force, num_orders=args.orders)
    with sqlite3.connect(path) as connection:
        tables = ["sales_reps", "customers", "products", "orders", "order_details"]
        counts = {t: connection.execute(f"SELECT COUNT(*) FROM {t};").fetchone()[0] for t in tables}
    print(f"Database ready at: {path}")
    for table_name, count in counts.items():
        print(f"  - {table_name}: {count:,} rows")
