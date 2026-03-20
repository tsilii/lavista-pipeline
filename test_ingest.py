"""
Tests for ingest.py — run with: pytest test_ingest.py -v
"""

import pytest
from ingest import clean_transaction

# ── Fixtures ───────────────────────────────────────────────────────────────────

def valid_transaction(**overrides) -> dict:
    """Return a valid raw transaction, with optional field overrides."""
    base = {
        "transaction_id": "TXN-ABCD1234",
        "timestamp":      "2026-03-20T19:30:00",
        "table":          5,
        "server":         "Alice",
        "total":          42.50,
        "payment_method": "card",
        "items": [
            {
                "name":       "Margherita Pizza",
                "category":   "Pizza",
                "unit_price": 12.50,
                "quantity":   2,
                "subtotal":   25.00,
            }
        ],
    }
    base.update(overrides)
    return base


# ── Happy path ─────────────────────────────────────────────────────────────────

def test_valid_transaction_passes():
    result = clean_transaction(valid_transaction())
    assert result is not None
    assert result["transaction_id"] == "TXN-ABCD1234"
    assert result["total"] == 42.50
    assert result["server"] == "Alice"
    assert result["payment_method"] == "card"


def test_timestamp_normalised_to_iso():
    """Timestamp should come back as a valid ISO string."""
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
    """Unknown fields in the raw payload should not cause errors."""
    txn = valid_transaction()
    txn["unexpected_field"] = "some_value"
    result = clean_transaction(txn)
    assert result is not None


def test_optional_fields_can_be_missing():
    """table, server, payment_method are optional — should not reject."""
    txn = valid_transaction()
    del txn["table"]
    del txn["server"]
    del txn["payment_method"]
    result = clean_transaction(txn)
    assert result is not None
    assert result["table_number"] is None
    assert result["server"] is None
    assert result["payment_method"] is None


# ── Missing required fields ────────────────────────────────────────────────────

@pytest.mark.parametrize("missing_field", ["transaction_id", "timestamp", "total", "items"])
def test_missing_required_field_rejected(missing_field):
    txn = valid_transaction()
    del txn[missing_field]
    assert clean_transaction(txn) is None


# ── Invalid values ─────────────────────────────────────────────────────────────

def test_negative_total_rejected():
    assert clean_transaction(valid_transaction(total=-10.00)) is None


def test_zero_total_is_allowed():
    """A zero total is valid — e.g. a comped table."""
    result = clean_transaction(valid_transaction(total=0))
    assert result is not None
    assert result["total"] == 0.0


def test_string_total_rejected():
    assert clean_transaction(valid_transaction(total="not_a_number")) is None


def test_bad_timestamp_rejected():
    assert clean_transaction(valid_transaction(timestamp="not-a-date")) is None


def test_empty_server_becomes_none():
    """An empty string server should be stored as None, not empty string."""
    result = clean_transaction(valid_transaction(server=""))
    assert result is not None
    assert result["server"] is None


def test_empty_payment_method_becomes_none():
    result = clean_transaction(valid_transaction(payment_method=""))
    assert result is not None
    assert result["payment_method"] is None