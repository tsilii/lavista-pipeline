"""
Seed script — inserts Lavista monthly expenses into the expenses table.
Run once with: python seed_expenses.py

To update expenses or add new months, edit the EXPENSES list and run again.
Existing entries are updated, not duplicated.
"""

import os
import psycopg2
from datetime import date

DATABASE_URL = os.getenv("DATABASE_URL")

# Change this to the current month you want to seed
MONTH = "2026-03-01"

EXPENSES = [
    {"category": "Rent",              "description": "Monthly rent",              "amount": 3500.00},
    {"category": "Utilities",         "description": "Electricity",               "amount":  800.00},
    {"category": "Utilities",         "description": "Water",                     "amount":  200.00},
    {"category": "Utilities",         "description": "Gas",                       "amount":  400.00},
    {"category": "Utilities",         "description": "Internet & Phone",          "amount":  100.00},
    {"category": "Supplies",          "description": "Cleaning supplies",         "amount":  300.00},
    {"category": "Supplies",          "description": "Packaging & disposables",   "amount":  250.00},
    {"category": "Software",          "description": "POS system & software",     "amount":  150.00},
    {"category": "Professional",      "description": "Accounting",                "amount":  200.00},
    {"category": "Insurance",         "description": "Business insurance",        "amount":  300.00},
]


def seed():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL environment variable is not set.")

    conn = psycopg2.connect(DATABASE_URL)

    with conn.cursor() as cur:
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

        for exp in EXPENSES:
            cur.execute("""
                INSERT INTO expenses (category, description, amount, frequency, month)
                VALUES (%s, %s, %s, 'monthly', %s)
                ON CONFLICT (description, month) DO UPDATE SET
                    category  = EXCLUDED.category,
                    amount    = EXCLUDED.amount
            """, (exp["category"], exp["description"], exp["amount"], MONTH))
            print(f"  ✓ {exp['category']} — {exp['description']} — €{exp['amount']:,.2f}")

    conn.commit()
    conn.close()

    total = sum(e["amount"] for e in EXPENSES)
    print(f"\nTotal monthly expenses: €{total:,.2f}")
    print(f"Month: {MONTH}")
    print("Done.")


if __name__ == "__main__":
    seed()