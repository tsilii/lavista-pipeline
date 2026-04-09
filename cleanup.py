import os
import psycopg2

conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()

cur.execute("""
    DELETE FROM transaction_items
    WHERE transaction_id IN (
        SELECT transaction_id FROM transactions
        WHERE server IN ('Alice','Bob','Carlos','Diana','Άννα')
    )
""")
print(f"Deleted {cur.rowcount} items")

cur.execute("""
    DELETE FROM transactions
    WHERE server IN ('Alice','Bob','Carlos','Diana','Άννα')
""")
print(f"Deleted {cur.rowcount} transactions")

conn.commit()
conn.close()
print("Done!")