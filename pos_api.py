"""
Mock POS API — simulates a restaurant Point-of-Sale system.
Returns randomly generated sales transactions on GET /sales.
Run with: uvicorn pos_api:app --port 8000
"""

import random
import uuid
from datetime import datetime, timedelta

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
SERVERS         = ["Alice", "Bob", "Carlos", "Diana"]
TABLES          = list(range(1, 13))  # tables 1–12

# Weighted selections for realism
ITEM_WEIGHTS    = [5, 3, 1, 4, 3, 4, 3, 3, 2, 5, 4, 5]
PAYMENT_WEIGHTS = [1, 5, 4]   # cash rare, card and contactless dominate
SERVER_WEIGHTS  = [4, 2, 3, 4]  # Alice and Diana busier

# Operating hours: 08:30 - 17:00
OPEN_HOUR   = 8
OPEN_MINUTE = 30
CLOSE_HOUR  = 17
CLOSE_MINUTE = 0

# Hour weights within operating hours (8:30 - 17:00)
# Busier at lunch (12-13) and mid-afternoon (15-16)
# Keys are hours 8 through 16
HOUR_WEIGHTS = {
    8:  1,   # 08:30-09:00 just opening, quiet
    9:  2,   # breakfast coffees
    10: 2,   # mid morning
    11: 3,   # pre-lunch
    12: 6,   # lunch rush
    13: 6,   # lunch rush
    14: 3,   # post lunch
    15: 3,   # afternoon
    16: 2,   # winding down
}


def random_operating_time(base_date) -> datetime:
    """Return a random datetime within operating hours on base_date."""
    hour     = random.choices(list(HOUR_WEIGHTS.keys()), weights=list(HOUR_WEIGHTS.values()), k=1)[0]
    # For hour 8, minute must be >= 30 (we open at 08:30)
    if hour == 8:
        minute = random.randint(30, 59)
    elif hour == 16:
        # Last hour — stop taking orders by 16:45
        minute = random.randint(0, 45)
    else:
        minute = random.randint(0, 59)
    second = random.randint(0, 59)
    return datetime(base_date.year, base_date.month, base_date.day, hour, minute, second)


def random_past_date(days_back: int = 30):
    """Return a random date within the last N days."""
    offset = random.randint(0, days_back - 1)
    return (datetime.now() - timedelta(days=offset)).date()


def generate_transaction(spread_history: bool = True) -> dict:
    """Generate one realistic sales transaction."""
    # Pick date — spread across last 30 days or use today
    if spread_history:
        tx_date = random_past_date(90)
    else:
        tx_date = datetime.now().date()

    timestamp = random_operating_time(tx_date)

    # Number of items per table: 1-4, weighted towards 2-3
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
        "timestamp":      timestamp.isoformat(),
        "table":          random.choice(TABLES),
        "server":         random.choices(SERVERS, weights=SERVER_WEIGHTS, k=1)[0],
        "items":          items,
        "total":          total,
        "payment_method": random.choices(PAYMENT_METHODS, weights=PAYMENT_WEIGHTS, k=1)[0],
    }


@app.get("/sales")
def get_sales(
    n: int = Query(default=4, ge=1, le=200, description="Number of transactions to return"),
    history: bool = Query(default=True, description="Spread transactions across last 30 days"),
):
    """Return n randomly generated sales transactions within operating hours."""
    transactions = [generate_transaction(spread_history=history) for _ in range(n)]
    return {"count": len(transactions), "transactions": transactions}


@app.get("/health")
def health():
    return {"status": "ok"}