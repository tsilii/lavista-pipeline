"""
Mock POS API — simulates a restaurant Point-of-Sale system.
Returns randomly generated sales transactions on GET /sales.
Run with: uvicorn pos_api:app --port 8000
"""

import random
import uuid
from datetime import datetime

from fastapi import FastAPI, Query

app = FastAPI(title="Sova Bistrot Mock POS API")

MENU_ITEMS = [
    {"name": "Margherita Pizza",    "category": "Pizza",    "price": 12.50},
    {"name": "Pepperoni Pizza",     "category": "Pizza",    "price": 14.00},
    {"name": "BBQ Chicken Pizza",   "category": "Pizza",    "price": 15.50},
    {"name": "Caesar Salad",        "category": "Salad",    "price":  8.00},
    {"name": "Greek Salad",         "category": "Salad",    "price":  9.00},
    {"name": "Spaghetti Bolognese", "category": "Pasta",    "price": 13.00},
    {"name": "Penne Arrabbiata",    "category": "Pasta",    "price": 11.50},
    {"name": "Tiramisu",            "category": "Dessert",  "price":  6.50},
    {"name": "Panna Cotta",         "category": "Dessert",  "price":  5.50},
    {"name": "Sparkling Water",     "category": "Beverage", "price":  2.50},
    {"name": "House Wine (Glass)",  "category": "Beverage", "price":  7.00},
    {"name": "Espresso",            "category": "Beverage", "price":  2.00},
]

PAYMENT_METHODS = ["cash", "card", "contactless"]
SERVERS = ["Άννα", "Γκερντούδη", "Κωνσταντίνα", "Γκίκα", "Βασίλη Ποπόβ", "Μιχαέλα"]
TABLES          = list(range(1, 13))

ITEM_WEIGHTS    = [5, 3, 1, 4, 3, 4, 3, 3, 2, 5, 4, 5]
PAYMENT_WEIGHTS = [1, 5, 4]
SERVER_WEIGHTS = [3, 3, 4, 3, 3, 3]  # adjust these to match real workload distribution


def generate_transaction() -> dict:
    """Generate one realistic transaction with current timestamp."""
    now = datetime.now()

    num_items = random.choices([1, 2, 3, 4], weights=[1, 3, 4, 2], k=1)[0]
    items = []
    for _ in range(num_items):
        item = random.choices(MENU_ITEMS, weights=ITEM_WEIGHTS, k=1)[0]
        qty  = random.choices([1, 2, 3], weights=[6, 3, 1], k=1)[0]
        items.append({
            "name":       item["name"],
            "category":   item["category"],
            "unit_price": item["price"],
            "quantity":   qty,
            "subtotal":   round(item["price"] * qty, 2),
        })

    total = round(sum(i["subtotal"] for i in items), 2)

    return {
        "transaction_id": f"TXN-{uuid.uuid4().hex[:8].upper()}",
        "timestamp":      now.isoformat(),
        "table":          random.choice(TABLES),
        "server":         random.choices(SERVERS, weights=SERVER_WEIGHTS, k=1)[0],
        "items":          items,
        "total":          total,
        "payment_method": random.choices(PAYMENT_METHODS, weights=PAYMENT_WEIGHTS, k=1)[0],
    }


@app.get("/sales")
def get_sales(
    n: int = Query(default=4, ge=1, le=200, description="Number of transactions to return"),
):
    """Return n transactions with current timestamps."""
    transactions = [generate_transaction() for _ in range(n)]
    return {"count": len(transactions), "transactions": transactions}


@app.get("/health")
def health():
    return {"status": "ok"}