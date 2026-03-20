"""
Mock POS API — simulates a restaurant Point-of-Sale system.
Returns randomly generated sales transactions on GET /sales.
Run with: uvicorn pos_api:app --port 8000
"""

import random
import uuid
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Query

app = FastAPI(title="Lavista Mock POS API")

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
SERVERS         = ["Alice", "Bob", "Carlos", "Diana"]
TABLES          = list(range(1, 13))  # tables 1–12

ITEM_WEIGHTS = [5, 3, 1, 4, 3, 4, 3, 3, 2, 5, 4, 5]
HOUR_WEIGHTS = [0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 2, 2, 5, 5, 3, 1, 1, 2, 5, 5, 5, 3, 2, 1]


def generate_transaction(base_time: datetime) -> dict:
    """Generate one fake sales transaction with a unique ID."""
    num_items = random.randint(1, 5)
    items = []
    for _ in range(num_items):
        item = random.choices(MENU_ITEMS, weights=ITEM_WEIGHTS, k=1)[0]
        qty  = random.randint(1, 3)
        items.append({
            "name":       item["name"],
            "category":   item["category"],
            "unit_price": item["price"],
            "quantity":   qty,
            "subtotal":   round(item["price"] * qty, 2),
        })

    total  = round(sum(i["subtotal"] for i in items), 2)

    hour      = random.choices(range(24), weights=HOUR_WEIGHTS, k=1)[0]
    minute    = random.randint(0, 59)
    second    = random.randint(0, 59)
    today     = base_time.date()
    timestamp = datetime(today.year, today.month, today.day, hour, minute, second)

    return {
        "transaction_id": f"TXN-{uuid.uuid4().hex[:8].upper()}",
        "timestamp":      timestamp.isoformat(),
        "table":          random.choice(TABLES),
        "server":         random.choices(SERVERS, weights=[4, 2, 3, 4], k=1)[0],
        "items":          items,
        "total":          total,
        "payment_method": random.choices(PAYMENT_METHODS, weights=[1, 5, 4], k=1)[0],
    }


@app.get("/sales")
def get_sales(
    n: int = Query(default=10, ge=1, le=200, description="Number of transactions to return"),
):
    """Return n randomly generated sales transactions."""
    now          = datetime.now()
    transactions = [generate_transaction(now) for _ in range(n)]
    return {"count": len(transactions), "transactions": transactions}


@app.get("/health")
def health():
    return {"status": "ok"}