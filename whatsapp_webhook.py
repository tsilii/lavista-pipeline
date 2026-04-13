"""
WhatsApp webhook — receives invoice photos via WhatsApp (Twilio),
extracts structured data using Claude Vision, stores a pending confirmation
in the DB, and replies to the owner with a summary for approval.

Flow:
  Owner sends 1–10 photos  →  extract all via Claude  →  store batch as pending
                           →  reply with consolidated summary
  Owner replies ✅          →  save each to supplier_deliveries + delivery_items
                           →  update inventory
  Duplicate check           →  same supplier + date + total already in DB → skip
  3-min window              →  photos sent as separate messages are accumulated

Included as a router in pos_api.py.
Run the full API with: uvicorn pos_api:app --port 8000
"""

import base64
import json
import logging
import os
from datetime import datetime
from typing import Optional

import anthropic
import psycopg2
import requests
from fastapi import APIRouter, BackgroundTasks, Form, Request
from fastapi.responses import PlainTextResponse
from twilio.rest import Client as TwilioClient

log = logging.getLogger(__name__)

DATABASE_URL       = os.getenv("DATABASE_URL")
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN  = os.getenv("TWILIO_AUTH_TOKEN")

router = APIRouter()


# ── DB helpers ─────────────────────────────────────────────────────────────────

def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set.")
    return psycopg2.connect(DATABASE_URL)


def init_invoice_tables(conn) -> None:
    """Create pending_invoices and delivery_items tables if they don't exist."""
    with conn.cursor() as cur:

        cur.execute("""
            CREATE TABLE IF NOT EXISTS pending_invoices (
                id              SERIAL PRIMARY KEY,
                from_number     TEXT        NOT NULL,
                extracted_data  JSONB       NOT NULL,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                status          TEXT        NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'confirmed', 'cancelled'))
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS delivery_items (
                id              SERIAL PRIMARY KEY,
                delivery_id     INTEGER     NOT NULL
                    REFERENCES supplier_deliveries(id) ON DELETE CASCADE,
                description     TEXT,
                quantity        NUMERIC(10, 3),
                unit_price      NUMERIC(10, 2),
                subtotal        NUMERIC(10, 2)
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS inventory_items (
                id                  SERIAL PRIMARY KEY,
                name                TEXT    UNIQUE NOT NULL,
                unit                TEXT    NOT NULL DEFAULT 'pieces',
                quantity            NUMERIC(10, 3) NOT NULL DEFAULT 0,
                reorder_threshold   NUMERIC(10, 3) NOT NULL DEFAULT 0,
                updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS inventory_movements (
                id              SERIAL PRIMARY KEY,
                item_id         INTEGER NOT NULL
                    REFERENCES inventory_items(id) ON DELETE CASCADE,
                movement_type   TEXT    NOT NULL CHECK (movement_type IN ('in', 'out')),
                quantity        NUMERIC(10, 3) NOT NULL,
                source          TEXT    NOT NULL CHECK (source IN ('delivery', 'sale', 'manual')),
                source_id       INTEGER,
                note            TEXT,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS supplier_returns (
                id              SERIAL PRIMARY KEY,
                supplier_name   TEXT           NOT NULL,
                return_date     DATE           NOT NULL,
                amount          NUMERIC(10,2)  NOT NULL,
                description     TEXT,
                invoice_number  TEXT,
                created_at      TIMESTAMPTZ    NOT NULL DEFAULT NOW()
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS return_items (
                id          SERIAL PRIMARY KEY,
                return_id   INTEGER        NOT NULL
                    REFERENCES supplier_returns(id) ON DELETE CASCADE,
                description TEXT,
                quantity    NUMERIC(10,3),
                unit_price  NUMERIC(10,2),
                subtotal    NUMERIC(10,2)
            );
        """)

    conn.commit()
    log.info("Invoice tables ready.")


# ── Claude Vision extraction ───────────────────────────────────────────────────

EXTRACTION_PROMPT = """
You are an invoice data extraction assistant for a restaurant in Cyprus.
Invoices may come from different suppliers with different layouts, in English or Greek or mixed.

First check: is this document a supplier invoice or delivery note with products and a total amount?
- YES → extract the data as instructed below
- A document IS an invoice if it contains: a list of products/items, quantities, prices, and a total amount.
- A document titled "Return Invoice" or "Επιστροφή Τιμολόγιο" → extract it AND set "_is_return": true.
- "Credit Invoice", "AR Invoice", "Sales Invoice", "Tax Invoice", "Delivery Note" are ALL regular invoices — set "_is_return": false.
- A statement of account, bank statement, or aged balance → return {"_not_invoice": true, "document_type": "describe what it is in one sentence"}

If it IS an invoice, extract the following and return ONLY valid JSON. No explanation, no markdown, no backticks.

Required JSON format:
{
  "supplier_name": "string or null",
  "invoice_date": "YYYY-MM-DD or null",
  "invoice_number": "string or null",
  "_is_return": false,
  "items": [
    {
      "description": "string",
      "quantity": number,
      "unit": "string",
      "unit_price": number,
      "subtotal": number
    }
  ],
  "total": number or null
}

For return invoices:
{
  "supplier_name": "string or null",
  "invoice_date": "YYYY-MM-DD or null",
  "invoice_number": "string or null",
  "_is_return": true,
  "items": [
    {
      "description": "string",
      "quantity": number,
      "unit": "string",
      "unit_price": number,
      "subtotal": number
    }
  ],
  "total": number or null
}
SUPPLIER NAME rules:
- The supplier is the company SELLING the goods — their name is usually at the top of the invoice in large text.
- Ignore the buyer name (e.g. "KSENOS FOOD", "SOVA") — that is the restaurant receiving the goods.

RETURN INVOICE rules:
- "_is_return": true only when the document title is literally "Return Invoice" or "Επιστροφή Τιμολόγιο".
- total is always POSITIVE (e.g. 12.50, not -12.50) — it represents the credit amount.
- Extract line items exactly as on a regular invoice.

DATE rules:
- Use the invoice date (Ημερομηνία / Date), not any delivery or due date.
- Format must be YYYY-MM-DD. If only month/year visible, use the 1st of that month.
- The current year is 2026. Always expand 2-digit years: "26" → 2026, "25" → 2025.
- Date formats you may encounter: DD/MM/YYYY, DD.MM.YYYY, DD/MM/YY, DD.MM.YY, YYYY-MM-DD.
- If the year on the invoice looks like 2025 but the date is recent, it is almost certainly 2026.
- Only use a past year if the invoice is clearly and unambiguously dated in the past.

INVOICE NUMBER rules:
- Look for labels like: Invoice #, Αρ. Παραστατικού, Αρ. Τιμολογίου, Invoice No, Ref.
- Extract the number value only, not the label.

TOTAL rules:
- Use the FINAL total — the amount actually owed after VAT.
- Look for labels like: Συνολική Αξία EUR, Total, Grand Total, Σύνολο Πληρωτέο, Amount Due.
- NEVER use pre-VAT subtotals, net values (Καθαρή Αξία), or goods value (Αξία Εμπορευμάτων).
- If VAT (Φ.Π.Α.) is shown separately, the total must INCLUDE it.

LINE ITEM rules:
- Extract every product row from the main table.
- unit: extract from the U/M or unit column. Normalize as follows:
    κιλ / kg / kilo / κιλά → "kg"
    τεμ / τεμάχιο / pcs / pieces / each → "pcs"
    λίτρο / λίτρα / L / liter / litre → "L"
    ml / milliliter → "ml"
    γρ / gr / gram / γραμμάρια → "gr"
    If no unit column exists or unit is unclear, use "pcs" as default.
    If the quantity column contains a combined value like "2kg", "500gr", "1.5L",
    split them: quantity = the number, unit = the unit suffix (normalized).
    If the description contains a size like "500GR" or "80GR" or "1L",
    this is the pack size — keep the U/M column unit for tracking (e.g. pcs).
- description: product name always in proper Greek. Rules:
    * Use the correct standard Greek word — NOT a phonetic transliteration of the English.
      Examples: RASBERRIES → Σμέουρα, TOMATOES → Ντομάτες, POTATOES → Πατάτες,
      MUSHROOMS → Μανιτάρια, ASPARAGUS → Σπαράγγια, DILL → Άνηθος,
      OREGANO/RIGANI → Ρίγανη, THYME/THYMARI → Θυμάρι, SPRING ONIONS → Κρεμμυδάκια,
      BANANAS → Μπανάνες, ORANGES → Πορτοκάλια, LEMONS → Λεμόνια,
      STRAWBERRIES → Φράουλες, BLUEBERRIES → Βατόμουρα, WATERMELON → Καρπούζι,
      MILK → Γάλα, CHEESE → Τυρί, YOGURT → Γιαούρτι.
    * If you do not know the Greek word with certainty, keep the original English name.
    * Do NOT transliterate English sounds into Greek letters.
    * Keep size suffixes as-is (e.g. "500GR", "125GR", "1L") in the original format.
    * Always use Title Case (first letter capital, rest lowercase).
- quantity: the QTY or Ποσότητα column value.
- unit_price: the Price or Τιμή column value.
- subtotal: use the Net or Σύνολο or Αξία column — this is the final line value after any discount.
- SKIP items where subtotal is 0.00 — these are free packaging or empty rows.
- If no unit price is visible, calculate it as subtotal / quantity.
- All numbers must be plain numbers — no currency symbols, no currency codes (EUR, USD etc.), no commas as thousands separators.
  Examples: "EUR 11.00" → 11.00, "€44.00" → 44.00, "1,250.00" → 1250.00

READABILITY rules:
- The image may be a photo taken at an angle, with shadows, or partially blurred.
  Do your best to read all visible text — do not give up on a document just because
  some parts are hard to read.
- If a number is partially obscured, make your best reasonable estimate based on context
  and mark it with a note in the description if needed.
- Read the entire document carefully before extracting — don't stop at the first section.

GENERAL rules:
- If a field is genuinely not visible or unclear, use null.
- Do not guess or invent values.
- Return ONLY the JSON object. Nothing else.
"""


def extract_invoice_data(image_bytes: bytes, content_type: str) -> dict | None:
    """
    Send invoice image to Claude Vision and return extracted structured data.
    Returns None if extraction fails.
    Returns {"_overloaded": True} if API is overloaded.
    Returns {"_not_invoice": True, ...} if the document is not an invoice.
    """
    if not ANTHROPIC_API_KEY:
        log.error("ANTHROPIC_API_KEY is not set — cannot extract invoice.")
        return None

    client    = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")

    media_type_map = {
        "image/jpeg": "image/jpeg",
        "image/jpg":  "image/jpeg",
        "image/png":  "image/png",
        "image/webp": "image/webp",
        "image/gif":  "image/gif",
    }
    media_type = media_type_map.get(content_type.lower(), "image/jpeg")

    try:
        message = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=2048,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type":   "image",
                            "source": {
                                "type":       "base64",
                                "media_type": media_type,
                                "data":       image_b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": EXTRACTION_PROMPT,
                        },
                    ],
                }
            ],
        )

        raw_text = message.content[0].text.strip()
        log.info("Claude raw response: %s", raw_text[:200])

        if not raw_text:
            log.warning("Claude returned empty response — document may be unreadable or unsupported")
            return {"_not_invoice": True, "document_type": "unreadable or unsupported document"}

        if raw_text.startswith("```"):
            raw_text = raw_text.split("```")[1]
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]
            raw_text = raw_text.strip()

        extracted = json.loads(raw_text)
        return extracted

    except json.JSONDecodeError as e:
        log.error("Claude returned invalid JSON: %s", e)
        return {"_not_invoice": True, "document_type": "could not parse the document"}

    except Exception as e:
        error_str = str(e)
        if "529" in error_str or "overloaded" in error_str.lower():
            log.warning("Anthropic API overloaded — returning overload signal")
            return {"_overloaded": True}
        log.error("Claude Vision extraction failed: %s", e)
        return None


# ── Image download ─────────────────────────────────────────────────────────────

def download_twilio_image(url: str) -> tuple[bytes, str] | None:
    """
    Download a media file from Twilio's servers.
    Returns (image_bytes, content_type) or None on failure.
    """
    try:
        resp = requests.get(
            url,
            auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
            timeout=30,
        )
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "image/jpeg")
        return resp.content, content_type
    except Exception as e:
        log.error("Failed to download Twilio media: %s", e)
        return None


# ── Pending invoice helpers ────────────────────────────────────────────────────

def store_pending(conn, from_number: str, data: dict | list) -> int:
    """
    Store extracted invoice data as pending confirmation.

    Key behaviour — 3-minute accumulation window:
    If a pending row exists within the last 3 minutes from the same number,
    append to it instead of replacing it. This handles the common case where
    the owner sends multiple photos as separate WhatsApp messages rather than
    as a single album — each message triggers a separate webhook call, but all
    photos within the window are collected into one pending batch.

    After 3 minutes of inactivity, the next photo starts a fresh batch and
    any older stale pending is cancelled.
    """
    new_items = data if isinstance(data, list) else [data]

    with conn.cursor() as cur:
        # Check for a recent pending from this number (within 3 minutes)
        cur.execute("""
            SELECT id, extracted_data FROM pending_invoices
            WHERE from_number = %s AND status = 'pending'
              AND created_at > NOW() - INTERVAL '3 minutes'
            ORDER BY created_at DESC
            LIMIT 1
        """, (from_number,))
        existing = cur.fetchone()

        if existing:
            # Append new invoices to the existing pending batch
            existing_id   = existing[0]
            existing_data = existing[1]
            existing_list = existing_data if isinstance(existing_data, list) else [existing_data]
            merged        = existing_list + new_items
            cur.execute("""
                UPDATE pending_invoices SET extracted_data = %s
                WHERE id = %s
            """, (json.dumps(merged), existing_id))
            row_id = existing_id
            log.info("Appended to existing pending batch %d — now %d invoices", existing_id, len(merged))
        else:
            # Cancel any stale pending and start fresh
            cur.execute("""
                UPDATE pending_invoices SET status = 'cancelled'
                WHERE from_number = %s AND status = 'pending'
            """, (from_number,))
            cur.execute("""
                INSERT INTO pending_invoices (from_number, extracted_data)
                VALUES (%s, %s)
                RETURNING id
            """, (from_number, json.dumps(new_items)))
            row_id = cur.fetchone()[0]
            log.info("Created new pending batch %d", row_id)

    conn.commit()
    return row_id


def get_pending(conn, from_number: str) -> dict | None:
    """Fetch the latest pending invoice for a given WhatsApp number."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, extracted_data
            FROM pending_invoices
            WHERE from_number = %s AND status = 'pending'
            ORDER BY created_at DESC
            LIMIT 1
        """, (from_number,))
        row = cur.fetchone()
    if not row:
        return None
    return {"id": row[0], "data": row[1]}


def confirm_pending(conn, pending_id: int) -> None:
    """Mark a pending invoice as confirmed."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE pending_invoices SET status = 'confirmed' WHERE id = %s",
            (pending_id,)
        )
    conn.commit()


# ── Duplicate detection ────────────────────────────────────────────────────────

def is_duplicate(conn, supplier_name: str, delivery_date, total: float) -> bool:
    """
    Return True if a delivery with the same supplier, date, and total already
    exists in supplier_deliveries.

    Matching rules:
      - supplier_name: case-insensitive, stripped
      - delivery_date: exact date match
      - total: within €0.01 (float rounding tolerance)
    """
    if not supplier_name or delivery_date is None or total is None:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id FROM supplier_deliveries
                WHERE LOWER(TRIM(supplier_name)) = LOWER(TRIM(%s))
                  AND delivery_date = %s
                  AND ABS(amount - %s) < 0.01
                LIMIT 1
            """, (supplier_name, delivery_date, total))
            return cur.fetchone() is not None
    except Exception as e:
        log.error("Duplicate check failed: %s", e)
        return False


# ── Inventory: add stock on delivery ──────────────────────────────────────────

UNIT_MAP = {
    "κιλ": "kg", "κιλά": "kg", "kilo": "kg", "kg": "kg",
    "gr": "gr", "γρ": "gr", "gram": "gr", "grams": "gr",
    "γραμμάρια": "gr", "γραμμάριο": "gr",
    "500gr": "gr", "250gr": "gr", "100gr": "gr", "80gr": "gr",
    "λίτρο": "L", "λίτρα": "L", "liter": "L", "litre": "L",
    "l": "L", "lt": "L", "λτ": "L",
    "ml": "ml", "milliliter": "ml", "millilitre": "ml",
    "1l": "L", "2l": "L", "5l": "L",
    "τεμ": "pcs", "τεμάχιο": "pcs", "τεμάχια": "pcs",
    "pcs": "pcs", "pieces": "pcs", "each": "pcs", "piece": "pcs",
    "τμχ": "pcs", "τχ": "pcs",
    "box": "box", "boxes": "box", "κιβ": "box", "κιβώτιο": "box",
    "pack": "pack", "packs": "pack", "pkg": "pack",
    "bunch": "bunch", "ματσάκι": "bunch", "ματσάκια": "bunch",
}

def normalize_unit(raw_unit: str) -> str:
    if not raw_unit:
        return "pcs"
    cleaned = raw_unit.strip().lower()
    return UNIT_MAP.get(cleaned, raw_unit.strip())


def update_inventory_for_delivery(conn, delivery_id: int, items: list[dict]) -> None:
    """
    For each delivery item, upsert into inventory_items and record an 'in' movement.
    """
    with conn.cursor() as cur:
        for item in items:
            name = (item.get("description") or "").strip().title()
            qty  = float(item.get("quantity") or 0)
            unit = normalize_unit(item.get("unit") or "pcs")
            if not name or qty <= 0:
                continue

            cur.execute("""
                SELECT id, name FROM inventory_items
                WHERE LOWER(name) = LOWER(%s)
                LIMIT 1
            """, (name,))
            existing = cur.fetchone()

            if existing:
                item_id = existing[0]
                cur.execute("""
                    UPDATE inventory_items
                    SET quantity   = quantity + %s,
                        updated_at = NOW()
                    WHERE id = %s
                """, (qty, item_id))
            else:
                cur.execute("""
                    INSERT INTO inventory_items (name, unit, quantity, updated_at)
                    VALUES (%s, %s, %s, NOW())
                    RETURNING id
                """, (name, unit, qty))
                item_id = cur.fetchone()[0]

            cur.execute("""
                INSERT INTO inventory_movements
                    (item_id, movement_type, quantity, source, source_id, note)
                VALUES (%s, 'in', %s, 'delivery', %s, %s)
            """, (item_id, qty, delivery_id, f"Delivery #{delivery_id} — {qty} {unit}"))

    conn.commit()


# ── Save confirmed delivery ────────────────────────────────────────────────────

def save_delivery(conn, data: dict) -> int | None:
    """
    Insert a confirmed invoice into supplier_deliveries + delivery_items.
    Returns the new delivery id, or None if skipped as a duplicate.
    """
    supplier   = (data.get("supplier_name") or "Unknown Supplier").strip().title()
    raw_date   = data.get("invoice_date")
    total      = data.get("total") or 0.0
    inv_number = data.get("invoice_number")
    items      = data.get("items") or []

    try:
        delivery_date = datetime.strptime(raw_date, "%Y-%m-%d").date() if raw_date else datetime.today().date()
        today = datetime.today().date()
        if (today - delivery_date).days > 60:
            corrected = delivery_date.replace(year=today.year)
            if abs((today - corrected).days) <= 60:
                log.warning("Auto-corrected year from %s to %s", delivery_date, corrected)
                delivery_date = corrected
    except ValueError:
        delivery_date = datetime.today().date()

    # Duplicate check — same supplier, date, total already in DB
    if is_duplicate(conn, supplier, delivery_date, float(total)):
        log.info("Duplicate skipped — %s on %s €%.2f", supplier, delivery_date, total)
        return None

    description = f"Invoice {inv_number}" if inv_number else "Invoice via WhatsApp"

    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO supplier_deliveries
                (supplier_name, delivery_date, amount, description, paid, created_at)
            VALUES (%s, %s, %s, %s, FALSE, NOW())
            RETURNING id
        """, (supplier, delivery_date, total, description))

        delivery_id = cur.fetchone()[0]

        for item in items:
            cur.execute("""
                INSERT INTO delivery_items
                    (delivery_id, description, quantity, unit_price, subtotal)
                VALUES (%s, %s, %s, %s, %s)
            """, (
                delivery_id,
                item.get("description"),
                item.get("quantity"),
                item.get("unit_price"),
                item.get("subtotal"),
            ))

    conn.commit()
    log.info("Saved delivery %d — %s — €%.2f — %d items", delivery_id, supplier, total, len(items))
    return delivery_id

def save_return(conn, data: dict) -> int | None:
    """
    Insert a confirmed return invoice into supplier_returns + return_items.
    Returns the new return id, or None if duplicate.
    """
    supplier   = (data.get("supplier_name") or "Unknown Supplier").strip().title()
    raw_date   = data.get("invoice_date")
    total      = data.get("total") or 0.0
    inv_number = data.get("invoice_number")
    items      = data.get("items") or []

    try:
        return_date = datetime.strptime(raw_date, "%Y-%m-%d").date() if raw_date else datetime.today().date()
        today = datetime.today().date()
        if (today - return_date).days > 60:
            corrected = return_date.replace(year=today.year)
            if abs((today - corrected).days) <= 60:
                log.warning("Auto-corrected return date year from %s to %s", return_date, corrected)
                return_date = corrected
    except ValueError:
        return_date = datetime.today().date()

    # Duplicate check
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id FROM supplier_returns
            WHERE LOWER(TRIM(supplier_name)) = LOWER(TRIM(%s))
              AND return_date = %s
              AND ABS(amount - %s) < 0.01
            LIMIT 1
        """, (supplier, return_date, float(total)))
        if cur.fetchone():
            log.info("Duplicate return skipped — %s on %s €%.2f", supplier, return_date, total)
            return None

    description = f"Return Invoice {inv_number}" if inv_number else "Return Invoice via WhatsApp"

    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO supplier_returns
                (supplier_name, return_date, amount, description, invoice_number, created_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
            RETURNING id
        """, (supplier, return_date, total, description, inv_number))
        return_id = cur.fetchone()[0]

        for item in items:
            cur.execute("""
                INSERT INTO return_items
                    (return_id, description, quantity, unit_price, subtotal)
                VALUES (%s, %s, %s, %s, %s)
            """, (
                return_id,
                item.get("description"),
                item.get("quantity"),
                item.get("unit_price"),
                item.get("subtotal"),
            ))

    conn.commit()
    log.info("Saved return %d — %s — €%.2f — %d items", return_id, supplier, total, len(items))
    return return_id

# ── TwiML response helper ──────────────────────────────────────────────────────

def twiml_reply(message: str) -> PlainTextResponse:
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Message>{message}</Message>
</Response>"""
    return PlainTextResponse(content=xml, media_type="application/xml")


def send_whatsapp_message(to: str, message: str) -> None:
    """Send a WhatsApp message via Twilio REST API (used in background tasks)."""
    try:
        client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        client.messages.create(
            from_="whatsapp:+14155238886",
            to=to,
            body=message,
        )
        log.info("Sent WhatsApp message to %s", to)
    except Exception as e:
        log.error("Failed to send WhatsApp message to %s: %s", to, e)


# ── Format summaries for WhatsApp ──────────────────────────────────────────────

def format_summary(data: dict) -> str:
    """Format a single extracted invoice into a readable WhatsApp message."""
    from datetime import date as date_type

    supplier = data.get("supplier_name") or "Unknown supplier"
    date_str = data.get("invoice_date")   or "Date not found"
    inv_num  = data.get("invoice_number")
    total    = data.get("total")
    items    = data.get("items") or []

    date_warning = None
    if date_str and date_str != "Date not found":
        try:
            parsed_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            today       = date_type.today()
            delta       = (today - parsed_date).days
            if delta > 60:
                date_warning = f"⚠️ WARNING: Date is {delta} days in the past ({date_str}). Please check before confirming."
            elif delta < -7:
                date_warning = f"⚠️ WARNING: Date is in the future ({date_str}). Please check before confirming."
        except ValueError:
            date_warning = f"⚠️ WARNING: Could not parse date '{date_str}'. Please verify."

    is_return = data.get("_is_return", False)
    lines = ["↩️ *Return Invoice detected:*" if is_return else "📦 *Invoice detected:*", ""]

    if date_warning:
        lines.append(date_warning)
        lines.append("")

    lines.append(f"*Supplier:* {supplier}")
    try:
        date_display = datetime.strptime(date_str, "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        date_display = date_str
    lines.append(f"*Date:* {date_display}")
    if inv_num:
        lines.append(f"*Invoice #:* {inv_num}")

    if items:
        lines.append("")
        lines.append("*Items:*")
        for item in items:
            desc = item.get("description", "—")
            qty  = item.get("quantity",    "?")
            sub  = item.get("subtotal",    0)
            lines.append(f"  • {desc} x{qty} — €{sub:.2f}")

    lines.append("")
    if total is not None:
        lines.append(f"*Total: €{total:.2f}*")

    lines.append("")
    lines.append("Reply *yes* or *ναι* or ✅ to save.")
    lines.append("Reply *no* or *cancel* to discard.")
    lines.append("")
    lines.append("✏️ To correct a field reply:")
    lines.append("  date 06/04/2026")
    lines.append("  total 62.47")
    lines.append("  supplier Name Here")

    return "\n".join(lines)


def format_multi_summary(invoices: list[dict], failed: int = 0) -> str:
    """Format multiple extracted invoices into one consolidated WhatsApp message."""
    from datetime import date as date_type

    count       = len(invoices)
    grand_total = 0.0
    date_warning = None
    lines = [f"📦 *Found {count} invoice{'s' if count > 1 else ''}:*", ""]

    for i, data in enumerate(invoices, 1):
        supplier = (data.get("supplier_name") or "Unknown supplier").strip()
        date_str = data.get("invoice_date") or "Date unknown"
        total    = float(data.get("total") or 0.0)
        items    = data.get("items") or []

        grand_total += total

        if date_str and date_str != "Date unknown" and not date_warning:
            try:
                parsed_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                delta       = (date_type.today() - parsed_date).days
                if delta > 60:
                    date_warning = f"⚠️ Invoice {i}: date is {delta} days in the past ({date_str}). Please verify."
                elif delta < -7:
                    date_warning = f"⚠️ Invoice {i}: date is in the future ({date_str}). Please verify."
            except ValueError:
                pass

        is_return = data.get("_is_return", False)
        return_tag = " ↩️ RETURN" if is_return else ""
        prefix = f"*{i}. {supplier}{return_tag}*" if count > 1 else f"*{supplier}{return_tag}*"
        try:
            date_display = datetime.strptime(date_str, "%Y-%m-%d").strftime("%d/%m/%Y")
        except ValueError:
            date_display = date_str
        lines.append(f"{prefix} — {date_display}")

        for item in items:
            desc = item.get("description") or "—"
            qty  = item.get("quantity")    or "?"
            unit = item.get("unit")        or ""
            sub  = float(item.get("subtotal") or 0)
            lines.append(f"     • {desc} x{qty}{' ' + unit if unit else ''} — €{sub:.2f}")

        lines.append(f"     *Subtotal: €{total:.2f}*")
        lines.append("")

    if count > 1:
        lines.append(f"*Grand Total: €{grand_total:.2f}*")
        lines.append("")

    if date_warning:
        lines.append(date_warning)
        lines.append("")

    if failed > 0:
        lines.append(f"⚠️ {failed} photo{'s' if failed > 1 else ''} could not be read.")
        lines.append("")

    lines.append("Reply *yes* / *ναι* / ✅ to save all.")
    lines.append("Reply *no* / *cancel* to discard all.")
    lines.append("")
    lines.append("✏️ To correct a field reply:")
    lines.append("  date 06/04/2026")
    lines.append("  total 62.47")
    lines.append("  supplier Name Here")

    return "\n".join(lines)



# ── Correction helpers ─────────────────────────────────────────────────────────

CORRECTION_FIELDS = {
    "date":     "invoice_date",
    "total":    "total",
    "supplier": "supplier_name",
    "invoice":  "invoice_number",
}


def parse_correction(keyword: str, raw_value: str):
    """Parse and validate a correction value. Returns the cleaned value or None on failure."""
    raw_value = raw_value.strip()

    if keyword == "date":
        # Accept DD/MM/YYYY, DD/MM/YY, DD.MM.YYYY, DD.MM.YY
        for fmt in ("%d/%m/%Y", "%d/%m/%y", "%d.%m.%Y", "%d.%m.%y"):
            try:
                parsed = datetime.strptime(raw_value, fmt)
                return parsed.strftime("%Y-%m-%d")
            except ValueError:
                continue
        return None

    if keyword == "total":
        try:
            return float(raw_value.replace(",", ".").replace("€", "").strip())
        except ValueError:
            return None

    # supplier / invoice — free text, just return as-is
    return raw_value if raw_value else None


def apply_correction(conn, pending_id: int, invoices: list, json_field: str, value) -> None:
    """Apply a field correction to all invoices in the pending batch."""
    for inv in invoices:
        inv[json_field] = value
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE pending_invoices SET extracted_data = %s WHERE id = %s",
            (json.dumps(invoices), pending_id)
        )
    conn.commit()

# ── Webhook endpoint ───────────────────────────────────────────────────────────

CONFIRM_WORDS = {"yes", "ναι", "nai", "✅", "y", "confirm", "ok", "okay", "ок"}
CANCEL_WORDS  = {"no",  "cancel", "όχι", "oxi", "discard", "n"}


def process_invoices_background(from_number: str, media_urls: list[str]) -> None:
    """
    Background task — runs after Twilio's 15s window has closed.

    For each URL:
      1. Download image from Twilio
      2. Extract with Claude Vision
      3. Skip non-invoices and failed reads
      4. Append to the pending batch (3-minute window handles separate messages)

    Then send one reply per photo processed.
    """
    total = len(media_urls)
    log.info("Background extraction starting — %d image(s) for %s", total, from_number)

    extracted_list = []
    failed         = 0
    not_invoices   = 0

    for i, url in enumerate(media_urls):
        log.info("Processing image %d/%d", i + 1, total)

        result = download_twilio_image(url)
        if not result:
            log.warning("Failed to download image %d", i + 1)
            failed += 1
            continue

        image_bytes, content_type = result
        log.info("Downloaded image %d — %d bytes — %s", i + 1, len(image_bytes), content_type)

        extracted = extract_invoice_data(image_bytes, content_type)

        if not extracted:
            log.warning("Extraction failed for image %d", i + 1)
            failed += 1
            continue

        if extracted.get("_overloaded"):
            send_whatsapp_message(from_number,
                "⏳ The system is busy right now. Please send the photos again in a moment.")
            return

        if extracted.get("_not_invoice"):
            doc_type = extracted.get("document_type", "unknown document")
            log.info("Image %d is not an invoice: %s", i + 1, doc_type)
            not_invoices += 1
            continue

        extracted_list.append(extracted)

    if not extracted_list:
        if not_invoices > 0 and failed == 0:
            send_whatsapp_message(from_number,
                f"❌ The photo doesn't appear to be an invoice.\n\n"
                f"Please send a supplier invoice or delivery note with products and a total amount.")
        else:
            send_whatsapp_message(from_number,
                "❌ Could not read the invoice. "
                "Please make sure the photo is clear and well-lit, then try again.")
        return

    try:
        conn = get_conn()
        init_invoice_tables(conn)
        store_pending(conn, from_number, extracted_list)
        conn.close()
    except Exception as e:
        log.error("DB error storing pending invoices: %s", e)
        send_whatsapp_message(from_number, "⚠️ System error — please try again.")
        return

    # Single invoice → clean single format; multiple → consolidated format
    if len(extracted_list) == 1 and not_invoices == 0 and failed == 0:
        summary = format_summary(extracted_list[0])
    else:
        summary = format_multi_summary(extracted_list, failed)

    send_whatsapp_message(from_number, summary)
    log.info("Background extraction complete — %d ok, %d failed, %d non-invoices",
             len(extracted_list), failed, not_invoices)


@router.post("/whatsapp", response_class=PlainTextResponse)
async def whatsapp_webhook(
    request:          Request,
    background_tasks: BackgroundTasks,
    From:             str           = Form(...),
    Body:             str           = Form(default=""),
    NumMedia:         Optional[str] = Form(default="0"),
):
    """
    Twilio calls this endpoint every time a WhatsApp message arrives.

    Two cases:
      1. Image(s) → reply to Twilio instantly (beat 15s timeout),
                    process in background, send result via REST API.
      2. Text     → check for yes/no confirmation of pending invoice batch.
    """
    from_number = From.strip()
    body_text   = Body.strip().lower()
    num_media   = int(NumMedia or "0")

    log.info("WhatsApp from %s | media=%d | body='%s'", from_number, num_media, Body[:60])

    # ── Case 1: Image(s) received ─────────────────────────────────────────────
    if num_media > 0:
        form_data  = await request.form()
        media_urls = [
            str(form_data[f"MediaUrl{i}"])
            for i in range(num_media)
            if f"MediaUrl{i}" in form_data
        ]

        if not media_urls:
            return twiml_reply("❌ No images found in the message. Please try again.")

        background_tasks.add_task(process_invoices_background, from_number, media_urls)

        return twiml_reply("📸 Got your invoice! Reading it now, I'll message you back in a moment...")

    # ── Case 2: Text message — confirmation or cancellation ───────────────────
    try:
        conn = get_conn()
        init_invoice_tables(conn)
    except Exception as e:
        log.error("DB connection failed: %s", e)
        return twiml_reply("⚠️ System error — please try again in a moment.")

    pending = get_pending(conn, from_number)

    if not pending:
        conn.close()
        return twiml_reply(
            "👋 Send me a photo of an invoice and I'll extract the details for you."
        )

    pending_id     = pending["id"]
    extracted_data = pending["data"]

    # Always work with a list
    invoices = extracted_data if isinstance(extracted_data, list) else [extracted_data]

    # ── Correction command? (e.g. "date 06/04/2026" or "total 62.47") ─────────
    for keyword, json_field in CORRECTION_FIELDS.items():
        if body_text.startswith(keyword + " "):
            raw_value = Body.strip()[len(keyword):].strip()
            corrected = parse_correction(keyword, raw_value)
            if corrected is None:
                conn.close()
                return twiml_reply(
                    f"⚠️ Couldn't parse that value for *{keyword}*.\n"
                    f"Examples:\n"
                    f"  date 06/04/2026\n"
                    f"  total 62.47\n"
                    f"  supplier Mouzouros Trading"
                )
            apply_correction(conn, pending_id, invoices, json_field, corrected)
            display = corrected if keyword != "date" else datetime.strptime(corrected, "%Y-%m-%d").strftime("%d/%m/%Y")
            conn.close()
            return twiml_reply(
                f"✏️ *{keyword.capitalize()}* updated to: {display}\n\n"
                f"Reply *yes* to save or *no* to discard."
            )

# ── Confirmed ─────────────────────────────────────────────────────────────
    if body_text in CONFIRM_WORDS:
        saved   = 0
        skipped = 0
        errors  = 0

        for data in invoices:
            try:
                if data.get("_is_return"):
                    result_id = save_return(conn, data)
                    if result_id is None:
                        skipped += 1
                    else:
                        saved += 1
                else:
                    delivery_id = save_delivery(conn, data)
                    if delivery_id is None:
                        skipped += 1
                        continue
                    items = data.get("items") or []
                    if items:
                        try:
                            update_inventory_for_delivery(conn, delivery_id, items)
                        except Exception as e:
                            log.error("Inventory update failed for delivery %d: %s", delivery_id, e)
                    saved += 1
            except Exception as e:
                log.error("Failed to save invoice: %s", e)
                errors += 1

        confirm_pending(conn, pending_id)
        conn.close()

        total_processed = saved + skipped + errors
        parts = []

        if saved:
            parts.append(f"✅ {saved} invoice{'s' if saved > 1 else ''} saved.")
        if skipped:
            parts.append(f"⚠️ {skipped} skipped — already in system.")
        if errors:
            parts.append(f"❌ {errors} failed to save — check the logs.")

        parts.append(f"\n_{total_processed} invoice{'s' if total_processed > 1 else ''} processed in total._")

        return twiml_reply("\n\n".join(parts) if parts else "✅ Done.")

    # ── Cancelled ─────────────────────────────────────────────────────────────
    if body_text in CANCEL_WORDS:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE pending_invoices SET status = 'cancelled' WHERE id = %s",
                (pending_id,)
            )
        conn.commit()
        conn.close()
        return twiml_reply("🗑️ Invoice discarded. Send a new photo when ready.")

    # ── Unrecognised text ─────────────────────────────────────────────────────
    conn.close()
    return twiml_reply(
        "Reply *yes* to save or *no* to discard the last invoice.\n"
        "Or send a new photo to replace it."
    )