"""
Ingestion script — fetch → clean → load into PostgreSQL.

Modes:
  python ingest.py          # run once immediately
  python ingest.py --schedule  # run on cron during operating hours (08:30-16:30)
"""

import argparse
import logging
import os
from datetime import datetime
from typing import Optional

import psycopg2
import psycopg2.extras
import requests
from apscheduler.schedulers.blocking import BlockingScheduler

# ── Config ────────────────────────────────────────────────────────────────────
POS_API_URL            = os.getenv("POS_API_URL", "http://127.0.0.1:8000/sales")
TRANSACTIONS_PER_FETCH = int(os.getenv("TRANSACTIONS_PER_FETCH", "4"))
DATABASE_URL           = os.getenv("DATABASE_URL")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Database connection ────────────────────────────────────────────────────────

def get_connection():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL environment variable is not set.")
    return psycopg2.connect(DATABASE_URL)


# ── Database setup ─────────────────────────────────────────────────────────────

def init_db(conn) -> None:
    """Create tables if they don't exist."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id               SERIAL PRIMARY KEY,
                transaction_id   TEXT    UNIQUE NOT NULL,
                timestamp        TIMESTAMPTZ NOT NULL,
                table_number     INTEGER,
                server           TEXT,
                total            NUMERIC(10, 2),
                payment_method   TEXT,
                ingested_at      TIMESTAMPTZ NOT NULL
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS transaction_items (
                id               SERIAL PRIMARY KEY,
                transaction_id   TEXT    NOT NULL,
                item_name        TEXT,
                category         TEXT,
                unit_price       NUMERIC(10, 2),
                quantity         INTEGER,
                subtotal         NUMERIC(10, 2),
                FOREIGN KEY (transaction_id) REFERENCES transactions(transaction_id)
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS cursor_state (
                id          INTEGER PRIMARY KEY CHECK (id = 1),
                last_seen   TIMESTAMPTZ NOT NULL
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS pipeline_runs (
                id          SERIAL PRIMARY KEY,
                run_at      TIMESTAMPTZ NOT NULL,
                fetched     INTEGER NOT NULL DEFAULT 0,
                cleaned     INTEGER NOT NULL DEFAULT 0,
                inserted    INTEGER NOT NULL DEFAULT 0,
                skipped     INTEGER NOT NULL DEFAULT 0,
                status      TEXT    NOT NULL DEFAULT 'success',
                error_msg   TEXT
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS employees (
                id              SERIAL PRIMARY KEY,
                name            TEXT    UNIQUE NOT NULL,
                role            TEXT    NOT NULL,
                monthly_salary  NUMERIC(10, 2) NOT NULL,
                start_date      DATE,
                active          BOOLEAN NOT NULL DEFAULT TRUE
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS expenses (
                id          SERIAL PRIMARY KEY,
                category    TEXT    NOT NULL,
                description TEXT    NOT NULL,
                amount      NUMERIC(10, 2) NOT NULL,
                frequency   TEXT    NOT NULL DEFAULT 'monthly',
                month       DATE    NOT NULL,
                UNIQUE (description, month)
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS supplier_deliveries (
                id              SERIAL PRIMARY KEY,
                supplier_name   TEXT    NOT NULL,
                delivery_date   DATE    NOT NULL,
                amount          NUMERIC(10, 2) NOT NULL,
                description     TEXT,
                paid            BOOLEAN NOT NULL DEFAULT FALSE,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
    conn.commit()


# ── Cursor ─────────────────────────────────────────────────────────────────────

def get_cursor(conn) -> Optional[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT last_seen FROM cursor_state WHERE id = 1")
        row = cur.fetchone()
    return row[0].isoformat() if row else None


def set_cursor(conn, timestamp: str) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO cursor_state (id, last_seen) VALUES (1, %s)
            ON CONFLICT (id) DO UPDATE SET last_seen = EXCLUDED.last_seen
        """, (timestamp,))
    conn.commit()


# ── Pipeline run logging ───────────────────────────────────────────────────────

def log_run(conn, run_at, fetched, cleaned, inserted, skipped,
            status="success", error_msg=None):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO pipeline_runs
                (run_at, fetched, cleaned, inserted, skipped, status, error_msg)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (run_at, fetched, cleaned, inserted, skipped, status, error_msg))
    conn.commit()


# ── Fetch ──────────────────────────────────────────────────────────────────────

def fetch_sales(n: int) -> list[dict]:
    resp = requests.get(POS_API_URL, params={"n": n}, timeout=10)
    resp.raise_for_status()
    return resp.json()["transactions"]


# ── Clean ──────────────────────────────────────────────────────────────────────

def clean_transaction(raw: dict) -> dict | None:
    required = {"transaction_id", "timestamp", "total", "items"}
    if not required.issubset(raw):
        log.warning("Skipping transaction missing fields: %s", raw.get("transaction_id"))
        return None

    if not isinstance(raw["total"], (int, float)) or raw["total"] < 0:
        log.warning("Skipping transaction with invalid total: %s", raw["transaction_id"])
        return None

    try:
        ts = datetime.fromisoformat(raw["timestamp"])
    except ValueError:
        log.warning("Skipping transaction with bad timestamp: %s", raw["transaction_id"])
        return None

    return {
        "transaction_id": raw["transaction_id"].strip(),
        "timestamp":      ts.isoformat(),
        "table_number":   raw.get("table"),
        "server":         raw.get("server", "").strip() or None,
        "total":          round(float(raw["total"]), 2),
        "payment_method": raw.get("payment_method", "").strip().lower() or None,
        "items":          raw["items"],
    }


# ── Load ───────────────────────────────────────────────────────────────────────

def load_transactions(conn, transactions: list[dict]) -> int:
    now      = datetime.now().isoformat()
    inserted = 0

    with conn.cursor() as cur:
        for txn in transactions:
            try:
                cur.execute("""
                    INSERT INTO transactions
                        (transaction_id, timestamp, table_number, server,
                         total, payment_method, ingested_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (transaction_id) DO NOTHING
                """, (
                    txn["transaction_id"], txn["timestamp"], txn["table_number"],
                    txn["server"], txn["total"], txn["payment_method"], now,
                ))

                if cur.rowcount == 0:
                    continue

                for item in txn["items"]:
                    cur.execute("""
                        INSERT INTO transaction_items
                            (transaction_id, item_name, category, unit_price, quantity, subtotal)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (
                        txn["transaction_id"],
                        item.get("name"),
                        item.get("category"),
                        item.get("unit_price"),
                        item.get("quantity"),
                        item.get("subtotal"),
                    ))
                inserted += 1

            except psycopg2.Error as e:
                log.error("DB error for %s: %s", txn["transaction_id"], e)
                conn.rollback()

    conn.commit()
    return inserted


# ── Pipeline run ───────────────────────────────────────────────────────────────

def run_pipeline() -> None:
    log.info("── Pipeline run starting ──")
    run_at = datetime.now().isoformat()

    try:
        conn = get_connection()
        init_db(conn)
        cursor = get_cursor(conn)
        conn.close()
    except Exception as e:
        log.error("Failed to connect to database: %s", e)
        return

    if cursor:
        log.info("Cursor found — last run at %s", cursor)
    else:
        log.info("No cursor found — first run")

    try:
        raw = fetch_sales(TRANSACTIONS_PER_FETCH)
        log.info("Fetched %d transactions from POS API", len(raw))
    except requests.RequestException as e:
        log.error("Failed to fetch from POS API: %s", e)
        conn = get_connection()
        log_run(conn, run_at, fetched=0, cleaned=0, inserted=0,
                skipped=0, status="error", error_msg=str(e))
        conn.close()
        return

    cleaned = [c for r in raw if (c := clean_transaction(r)) is not None]
    log.info("Cleaned: %d valid / %d total", len(cleaned), len(raw))

    if not cleaned:
        log.info("Nothing new to insert — pipeline run complete\n")
        conn = get_connection()
        log_run(conn, run_at, fetched=len(raw), cleaned=0,
                inserted=0, skipped=0, status="success")
        conn.close()
        return

    conn      = get_connection()
    new_rows  = load_transactions(conn, cleaned)
    skipped   = len(cleaned) - new_rows

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM transactions")
        total_rows = cur.fetchone()[0]

    set_cursor(conn, datetime.now().isoformat())
    log_run(conn, run_at, fetched=len(raw), cleaned=len(cleaned),
            inserted=new_rows, skipped=skipped, status="success")
    conn.close()

    log.info("Inserted %d new rows  |  Skipped %d duplicates  |  DB total: %d",
             new_rows, skipped, total_rows)
    log.info("── Pipeline run complete ──\n")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sova Bistrot ingestion script")
    parser.add_argument(
        "--schedule",
        action="store_true",
        help="Run on cron schedule during operating hours (08:30-16:30 every hour)",
    )
    args = parser.parse_args()

    if args.schedule:
        log.info("Scheduler mode: running at :30 past each hour from 08:30 to 16:30.")
        scheduler = BlockingScheduler()

        # Fire at 08:30, 09:30, 10:30, 11:30, 12:30, 13:30, 14:30, 15:30, 16:30
        scheduler.add_job(
            run_pipeline,
            "cron",
            hour="8-16",
            minute="30",
        )

        run_pipeline()  # run immediately on start
        try:
            scheduler.start()
        except KeyboardInterrupt:
            log.info("Scheduler stopped.")
    else:
        run_pipeline()