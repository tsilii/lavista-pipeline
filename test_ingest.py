"""
Tests for ingest.py — run with: pytest test_ingest.py -v
"""

import pytest
from datetime import datetime, timedelta
from ingest import clean_transaction, filter_by_cursor

# ── Fixtures ───────────────────────────────────────────────────────────────────

def valid_item(**overrides) -> dict:
    """Return a valid item, with optional field overrides."""
    base = {
        "name":       "Margherita Pizza",
        "category":   "Pizza",
        "unit_price": 12.50,
        "quantity":   2,
        "subtotal":   25.00,
    }
    base.update(overrides)
    return base


def valid_transaction(**overrides) -> dict:
    """Return a valid raw transaction, with optional field overrides."""
    base = {
        "transaction_id": "TXN-ABCD1234",
        "timestamp":      "2026-03-20T19:30:00",
        "table":          5,
        "server":         "Alice",
        "total":          42.50,
        "payment_method": "card",
        "items":          [valid_item()],
    }
    base.update(overrides)
    return base


# ══════════════════════════════════════════════════════════════════════════════
# TESTS: clean_transaction — happy path
# ══════════════════════════════════════════════════════════════════════════════

def test_valid_transaction_passes():
    result = clean_transaction(valid_transaction())
    assert result is not None
    assert result["transaction_id"] == "TXN-ABCD1234"
    assert result["total"] == 42.50
    assert result["server"] == "Alice"
    assert result["payment_method"] == "card"


def test_timestamp_normalised_to_iso():
    result = clean_transaction(valid_transaction(timestamp="2026-03-20T19:30:00"))
    assert result is not None
    assert result["timestamp"] == "2026-03-20T19:30:00"


def test_total_rounded_to_two_decimals():
    result = clean_transaction(valid_transaction(total=42.555555))
    assert result is not None
    assert result["total"] == 42.56


def test_payment_method_lowercased():
    result = clean_transaction(valid_transaction(payment_method="CARD"))
    assert result is not None
    assert result["payment_method"] == "card"


def test_whitespace_stripped_from_transaction_id():
    result = clean_transaction(valid_transaction(transaction_id="  TXN-ABCD1234  "))
    assert result is not None
    assert result["transaction_id"] == "TXN-ABCD1234"


def test_whitespace_stripped_from_server():
    result = clean_transaction(valid_transaction(server="  Alice  "))
    assert result is not None
    assert result["server"] == "Alice"


def test_extra_fields_ignored():
    txn = valid_transaction()
    txn["unexpected_field"] = "some_value"
    result = clean_transaction(txn)
    assert result is not None


def test_optional_fields_can_be_missing():
    txn = valid_transaction()
    del txn["table"]
    del txn["server"]
    del txn["payment_method"]
    result = clean_transaction(txn)
    assert result is not None
    assert result["table_number"] is None
    assert result["server"] is None
    assert result["payment_method"] is None


# ══════════════════════════════════════════════════════════════════════════════
# TESTS: clean_transaction — missing required fields
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("missing_field", ["transaction_id", "timestamp", "total", "items"])
def test_missing_required_field_rejected(missing_field):
    txn = valid_transaction()
    del txn[missing_field]
    assert clean_transaction(txn) is None


# ══════════════════════════════════════════════════════════════════════════════
# TESTS: clean_transaction — invalid values
# ══════════════════════════════════════════════════════════════════════════════

def test_negative_total_rejected():
    assert clean_transaction(valid_transaction(total=-10.00)) is None


def test_zero_total_is_allowed():
    result = clean_transaction(valid_transaction(total=0))
    assert result is not None
    assert result["total"] == 0.0


def test_string_total_rejected():
    assert clean_transaction(valid_transaction(total="not_a_number")) is None


def test_bad_timestamp_rejected():
    assert clean_transaction(valid_transaction(timestamp="not-a-date")) is None


def test_empty_server_becomes_none():
    result = clean_transaction(valid_transaction(server=""))
    assert result is not None
    assert result["server"] is None


def test_empty_payment_method_becomes_none():
    result = clean_transaction(valid_transaction(payment_method=""))
    assert result is not None
    assert result["payment_method"] is None


# ══════════════════════════════════════════════════════════════════════════════
# TESTS: clean_transaction — item-level validation
# ══════════════════════════════════════════════════════════════════════════════

def test_empty_items_list_rejected():
    """A transaction with no items at all should be rejected."""
    assert clean_transaction(valid_transaction(items=[])) is None


def test_items_not_a_list_rejected():
    """Items field must be a list."""
    assert clean_transaction(valid_transaction(items="not a list")) is None


def test_negative_unit_price_item_rejected():
    """An item with a negative price should be filtered out."""
    bad_item = valid_item(unit_price=-5.00, subtotal=-5.00)
    result   = clean_transaction(valid_transaction(items=[bad_item]))
    assert result is None  # all items invalid → transaction rejected


def test_zero_quantity_item_rejected():
    """An item with zero quantity should be filtered out."""
    bad_item = valid_item(quantity=0, subtotal=0)
    result   = clean_transaction(valid_transaction(items=[bad_item]))
    assert result is None  # all items invalid → transaction rejected


def test_negative_quantity_item_rejected():
    """An item with negative quantity should be filtered out."""
    bad_item = valid_item(quantity=-1, subtotal=-12.50)
    result   = clean_transaction(valid_transaction(items=[bad_item]))
    assert result is None


def test_mixed_items_keeps_valid_ones():
    """If some items are valid and some invalid, keep only the valid ones."""
    good_item = valid_item(name="Espresso", unit_price=2.00, quantity=1, subtotal=2.00)
    bad_item  = valid_item(name="Bad Item", unit_price=-5.00, quantity=1, subtotal=-5.00)
    result    = clean_transaction(valid_transaction(items=[good_item, bad_item]))
    assert result is not None
    assert len(result["items"]) == 1
    assert result["items"][0]["name"] == "Espresso"


def test_all_items_invalid_rejects_transaction():
    """If all items are invalid, the entire transaction is rejected."""
    bad1 = valid_item(unit_price=-1.00, subtotal=-1.00)
    bad2 = valid_item(quantity=0, subtotal=0)
    result = clean_transaction(valid_transaction(items=[bad1, bad2]))
    assert result is None


def test_valid_item_with_multiple_quantity_passes():
    """Multiple quantity on a single item is valid."""
    item   = valid_item(quantity=3, subtotal=37.50)
    result = clean_transaction(valid_transaction(items=[item]))
    assert result is not None
    assert result["items"][0]["quantity"] == 3


# ══════════════════════════════════════════════════════════════════════════════
# TESTS: filter_by_cursor
# ══════════════════════════════════════════════════════════════════════════════

def make_raw_txn(timestamp: str) -> dict:
    """Helper — make a minimal raw transaction dict with a given timestamp."""
    return {
        "transaction_id": f"TXN-{timestamp.replace(':', '').replace('-', '')}",
        "timestamp":      timestamp,
        "total":          25.00,
        "items":          [valid_item()],
    }


def test_filter_no_cursor_keeps_all():
    """With no cursor, all transactions pass through."""
    raw = [
        make_raw_txn("2026-03-24T10:00:00"),
        make_raw_txn("2026-03-24T11:00:00"),
    ]
    result, removed = filter_by_cursor(raw, None)
    assert len(result) == 2
    assert removed == 0


def test_filter_removes_older_transactions():
    """Transactions at or before the cursor timestamp are filtered out."""
    cursor = datetime(2026, 3, 24, 10, 30, 0)
    raw = [
        make_raw_txn("2026-03-24T09:00:00"),   # before cursor → filtered
        make_raw_txn("2026-03-24T10:30:00"),   # equal to cursor → filtered
        make_raw_txn("2026-03-24T11:00:00"),   # after cursor → kept
    ]
    result, removed = filter_by_cursor(raw, cursor)
    assert len(result) == 1
    assert removed == 2
    assert result[0]["timestamp"] == "2026-03-24T11:00:00"


def test_filter_keeps_newer_transactions():
    """Transactions after the cursor timestamp pass through."""
    cursor = datetime(2026, 3, 24, 8, 30, 0)
    raw = [
        make_raw_txn("2026-03-24T09:00:00"),
        make_raw_txn("2026-03-24T10:00:00"),
        make_raw_txn("2026-03-24T12:00:00"),
    ]
    result, removed = filter_by_cursor(raw, cursor)
    assert len(result) == 3
    assert removed == 0


def test_filter_removes_all_when_all_old():
    """If all transactions are older than cursor, result is empty."""
    cursor = datetime(2026, 3, 24, 17, 0, 0)
    raw = [
        make_raw_txn("2026-03-24T09:00:00"),
        make_raw_txn("2026-03-24T12:00:00"),
    ]
    result, removed = filter_by_cursor(raw, cursor)
    assert len(result) == 0
    assert removed == 2


def test_filter_empty_input():
    """Empty input returns empty output."""
    cursor = datetime(2026, 3, 24, 10, 0, 0)
    result, removed = filter_by_cursor([], cursor)
    assert result == []
    assert removed == 0


def test_filter_returns_correct_removed_count():
    """The removed count matches exactly how many were filtered."""
    cursor = datetime(2026, 3, 24, 11, 0, 0)
    raw = [
        make_raw_txn("2026-03-24T09:00:00"),  # filtered
        make_raw_txn("2026-03-24T10:00:00"),  # filtered
        make_raw_txn("2026-03-24T12:00:00"),  # kept
        make_raw_txn("2026-03-24T13:00:00"),  # kept
    ]
    result, removed = filter_by_cursor(raw, cursor)
    assert len(result) == 2
    assert removed == 2