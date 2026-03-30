"""
Seed script — inserts Lavista staff into the employees table.
Run once with: python seed_employees.py

To update salaries or add staff, edit the EMPLOYEES list and run again.
Existing employees are updated, not duplicated.
"""

import os
import psycopg2

DATABASE_URL = os.getenv("DATABASE_URL")

EMPLOYEES = [
    {"name": "Νίκος",       "role": "Σεφ",            "monthly_salary": 5830.00, "start_date": "2023-03-01", "active": True},
    {"name": "Γιάννης",     "role": "Σεφ",            "monthly_salary": 3000.00, "start_date": "2023-03-01", "active": True},
    {"name": "Ανδρέας",     "role": "Μάγειρας",       "monthly_salary": 1900.00, "start_date": "2023-03-01", "active": True},
    {"name": "Άντρα",       "role": "Μαγείρισσα",     "monthly_salary": 2000.00, "start_date": "2023-03-01", "active": True},
    {"name": "Σακίρα",      "role": "Μάγειρας",       "monthly_salary": 1600.00, "start_date": "2023-03-01", "active": True},
    {"name": "Ουράνια",     "role": "Μαγείρισσα",     "monthly_salary": 2100.00, "start_date": "2023-03-01", "active": True},
    {"name": "Executive",   "role": "Chef",           "monthly_salary": 700.00,  "start_date": "2023-03-01", "active": True},

    {"name": "Μιχαέλα",     "role": "Σέρβις",         "monthly_salary": 1400.00, "start_date": "2023-03-01", "active": True},
    {"name": "Γκερντούδη",  "role": "Σέρβις",         "monthly_salary": 1650.00, "start_date": "2023-03-01", "active": True},
    {"name": "Κωνσταντίνα", "role": "Σέρβις",         "monthly_salary": 1800.00, "start_date": "2023-03-01", "active": True},
    {"name": "Γκίκα",       "role": "Σέρβις",         "monthly_salary": 1700.00, "start_date": "2023-03-01", "active": True},
    {"name": "Βασίλη Ποπόβ","role": "Σέρβις",         "monthly_salary": 1700.00, "start_date": "2023-03-01", "active": True},
    {"name": "Μιχαέλα",     "role": "Σέρβις",         "monthly_salary": 1400.00, "start_date": "2023-03-01", "active": True},
    {"name": "Κωνσταντίνα", "role": "Σέρβις-Σομελιέ", "monthly_salary": 1800.00, "start_date": "2023-03-01", "active": True},
    {"name": "Ντάνι",       "role": "Βοηθός-Σέρβις",  "monthly_salary": 1150.00, "start_date": "2023-03-01", "active": True},



    {"name": "Βαλάντης",     "role": "Μπάρμαν",        "monthly_salary": 1600.00, "start_date": "2023-03-01", "active": True},
]



def seed():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL environment variable is not set.")

    conn = psycopg2.connect(DATABASE_URL)

    with conn.cursor() as cur:
        # Create table if it doesn't exist
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

        # Upsert each employee — insert or update if name already exists
        for emp in EMPLOYEES:
            cur.execute("""
                INSERT INTO employees (name, role, monthly_salary, start_date, active)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (name) DO UPDATE SET
                    role           = EXCLUDED.role,
                    monthly_salary = EXCLUDED.monthly_salary,
                    start_date     = EXCLUDED.start_date,
                    active         = EXCLUDED.active
            """, (
                emp["name"],
                emp["role"],
                emp["monthly_salary"],
                emp["start_date"],
                emp["active"],
            ))
            print(f"  ✓ {emp['name']} — {emp['role']} — €{emp['monthly_salary']:,.2f}/month")

    conn.commit()
    conn.close()

    total = sum(e["monthly_salary"] for e in EMPLOYEES if e["active"])
    print(f"\nTotal monthly payroll: €{total:,.2f}")
    print("Done.")


if __name__ == "__main__":
    seed()