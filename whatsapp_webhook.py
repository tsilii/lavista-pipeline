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

        # Holds AI-extracted invoice data waiting for owner confirmation.
        # One row per incoming invoice photo, keyed by the sender's WhatsApp number.
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

        # Line items for confirmed deliveries.
        # Linked to supplier_deliveries via delivery_id.
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

        # Inventory tables — created here so they exist as soon as the webhook runs.
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

    conn.commit()
    log.info("Invoice tables ready.")


# ── Claude Vision extraction ───────────────────────────────────────────────────

EXTRACTION_PROMPT = """
You are an invoice data extraction assistant for a restaurant in Cyprus.
Invoices may come from different suppliers with different layouts, in English or Greek or mixed.

First check: is this document a supplier invoice or delivery note with products and a total amount?
- YES → extract the data as instructed below
- A document IS an invoice if it contains: a list of products/items, quantities, prices, and a total amount.
- "Credit Invoice", "AR Invoice", "Sales Invoice", "Tax Invoice", "Delivery Note" are ALL valid invoices — extract them.
- A "Credit Note" or "Credit Memo" that CANCELS a previous invoice and shows NEGATIVE amounts or items returned is NOT an invoice.
- NO (it is a statement of account, bank statement, aged balance, or credit note cancelling a previous invoice) →
  return exactly this JSON: {"_not_invoice": true, "document_type": "describe what it is in one sentence"}

If it IS an invoice, extract the following and return ONLY valid JSON. No explanation, no markdown, no backticks.

Required JSON format:
{
  "supplier_name": "string or null",
  "invoice_date": "YYYY-MM-DD or null",
  "invoice_number": "string or null",
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

        # Strip markdown code fences if Claude added them despite instructions
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
    Twilio requires HTTP Basic Auth using Account SID + Auth Token.
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
    Accepts a single invoice dict or a list of invoice dicts (multi-image batch).
    Cancels any previous pending from the same number before inserting.
    Returns the new row id.
    """
    with conn.cursor() as cur:
        # Cancel any previous pending invoices from this number
        cur.execute("""
            UPDATE pending_invoices
            SET status = 'cancelled'
            WHERE from_number = %s AND status = 'pending'
        """, (from_number,))

        cur.execute("""
            INSERT INTO pending_invoices (from_number, extracted_data)
            VALUES (%s, %s)
            RETURNING id
        """, (from_number, json.dumps(data)))

        row_id = cur.fetchone()[0]
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

# Unit normalization map — handles Greek and English variants
UNIT_MAP = {
    # Weight
    "κιλ": "kg", "κιλά": "kg", "kilo": "kg", "kg": "kg",
    "gr": "gr", "γρ": "gr", "gram": "gr", "grams": "gr",
    "γραμμάρια": "gr", "γραμμάριο": "gr",
    "500gr": "gr", "250gr": "gr", "100gr": "gr", "80gr": "gr",
    # Volume
    "λίτρο": "L", "λίτρα": "L", "liter": "L", "litre": "L",
    "l": "L", "lt": "L", "λτ": "L",
    "ml": "ml", "milliliter": "ml", "millilitre": "ml",
    "1l": "L", "2l": "L", "5l": "L",
    # Pieces
    "τεμ": "pcs", "τεμάχιο": "pcs", "τεμάχια": "pcs",
    "pcs": "pcs", "pieces": "pcs", "each": "pcs", "piece": "pcs",
    "τμχ": "pcs", "τχ": "pcs",
    # Boxes / packs
    "box": "box", "boxes": "box", "κιβ": "box", "κιβώτιο": "box",
    "pack": "pack", "packs": "pack", "pkg": "pack",
    # Bunches
    "bunch": "bunch", "ματσάκι": "bunch", "ματσάκια": "bunch",
}

def normalize_unit(raw_unit: str) -> str:
    """Normalize a raw unit string to a standard form."""
    if not raw_unit:
        return "pcs"
    cleaned = raw_unit.strip().lower()
    return UNIT_MAP.get(cleaned, raw_unit.strip())


def update_inventory_for_delivery(conn, delivery_id: int, items: list[dict]) -> None:
    """
    For each delivery item, upsert into inventory_items and record an 'in' movement.
    - Name normalized to Title Case for consistent matching across suppliers/languages
    - Unit extracted and normalized (kg, pcs, L etc.)
    - If item already exists, quantity is added and unit is preserved from first delivery
    """
    with conn.cursor() as cur:
        for item in items:
            name = (item.get("description") or "").strip().title()
            qty  = float(item.get("quantity") or 0)
            unit = normalize_unit(item.get("unit") or "pcs")
            if not name or qty <= 0:
                continue

            # Case-insensitive lookup first — prevents duplicates from spelling variations
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
    supplier    = (data.get("supplier_name") or "Unknown Supplier").strip().title()
    raw_date    = data.get("invoice_date")
    total       = data.get("total") or 0.0
    inv_number  = data.get("invoice_number")
    items       = data.get("items") or []

    # Parse date — fall back to today if missing or invalid
    try:
        delivery_date = datetime.strptime(raw_date, "%Y-%m-%d").date() if raw_date else datetime.today().date()
        # Auto-correct year if date is suspiciously far in the past (likely a misread year)
        today = datetime.today().date()
        if (today - delivery_date).days > 60:
            corrected = delivery_date.replace(year=today.year)
            # Only apply correction if it makes the date reasonable
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


# ── TwiML response helper ──────────────────────────────────────────────────────

def twiml_reply(message: str) -> PlainTextResponse:
    """Wrap a text message in TwiML XML — the format Twilio expects back."""
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Message>{message}</Message>
</Response>"""
    return PlainTextResponse(content=xml, media_type="application/xml")


def send_whatsapp_message(to: str, message: str) -> None:
    """
    Send a WhatsApp message via Twilio REST API.
    Used for background task responses that happen after Twilio's 15s timeout.
    """
    try:
        client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        client.messages.create(
            from_="whatsapp:+14155238886",
            to=to,
            body=message,
        )
        log.info("Sent background WhatsApp message to %s", to)
    except Exception as e:
        log.error("Failed to send WhatsApp message to %s: %s", to, e)


# ── Format summary for WhatsApp ────────────────────────────────────────────────

def format_summary(data: dict) -> str:
    """Format a single extracted invoice into a readable WhatsApp message."""
    from datetime import date as date_type

    supplier = data.get("supplier_name") or "Unknown supplier"
    date_str = data.get("invoice_date")   or "Date not found"
    inv_num  = data.get("invoice_number")
    total    = data.get("total")
    items    = data.get("items") or []

    # Date validation — warn if date looks wrong
    date_warning = None
    if date_str and date_str != "Date not found":
        try:
            parsed_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            today       = date_type.today()
            delta       = (today - parsed_date).days
            if delta > 60:
                date_warning = f"⚠️ WARNING: Date is {delta} days in the past ({date_str}). Please check this is correct before confirming."
            elif delta < -7:
                date_warning = f"⚠️ WARNING: Date is in the future ({date_str}). Please check this is correct before confirming."
        except ValueError:
            date_warning = f"⚠️ WARNING: Could not parse date '{date_str}'. Please verify."

    lines = ["📦 *Invoice detected:*", ""]

    if date_warning:
        lines.append(date_warning)
        lines.append("")

    lines.append(f"*Supplier:* {supplier}")
    lines.append(f"*Date:* {date_str}")
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

    return "\n".join(lines)


def format_multi_summary(invoices: list[dict], failed: int = 0) -> str:
    """Format multiple extracted invoices into one consolidated WhatsApp message."""
    from datetime import date as date_type

    count = len(invoices)
    lines = [f"📦 *Found {count} invoice{'s' if count > 1 else ''}:*", ""]

    grand_total  = 0.0
    date_warning = None

    for i, data in enumerate(invoices, 1):
        supplier = (data.get("supplier_name") or "Unknown supplier").strip()
        date_str = data.get("invoice_date") or "Date unknown"
        total    = float(data.get("total") or 0.0)
        items    = data.get("items") or []

        grand_total += total

        # Flag suspicious dates — report the first one found
        if date_str and date_str != "Date unknown" and not date_warning:
            try:
                parsed_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                delta       = (date_type.today() - parsed_date).days
                if delta > 60:
                    date_warning = f"⚠️ WARNING: Invoice {i} date is {delta} days in the past ({date_str}). Please verify."
                elif delta < -7:
                    date_warning = f"⚠️ WARNING: Invoice {i} date is in the future ({date_str}). Please verify."
            except ValueError:
                pass

        prefix = f"*{i}. {supplier}*" if count > 1 else f"*{supplier}*"
        lines.append(f"{prefix} — {date_str}")

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

    return "\n".join(lines)


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
      4. Collect valid results

    Then store the whole batch as a single pending row and send one consolidated reply.
    Single image batches work identically — the list just has one entry.
    """
    total = len(media_urls)
    log.info("Background extraction starting — %d image(s) for %s", total, from_number)

    if total > 1:
        send_whatsapp_message(from_number,
            f"⏳ Processing {total} invoices, give me a moment...")

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

    # All images failed or were non-invoices
    if not extracted_list:
        if not_invoices > 0 and failed == 0:
            send_whatsapp_message(from_number,
                f"❌ None of the {total} photos appear to be invoices.\n\n"
                f"Please send supplier invoices or delivery notes with products and a total amount.")
        else:
            send_whatsapp_message(from_number,
                "❌ Could not read any of the invoices. "
                "Please make sure the photos are clear and well-lit, then try again.")
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

    # Use single-invoice format for clean display when only one came through
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
                    process all images in background, send result via REST API.
      2. Text     → check for yes/no confirmation of a pending invoice batch.
    """
    from_number = From.strip()
    body_text   = Body.strip().lower()
    num_media   = int(NumMedia or "0")

    log.info("WhatsApp from %s | media=%d | body='%s'", from_number, num_media, Body[:60])

    # ── Case 1: Image(s) received ─────────────────────────────────────────────
    if num_media > 0:
        # Read all media URLs from the raw form data dynamically.
        # Works for 1 image (MediaUrl0 only) or up to 10 (Twilio's WhatsApp limit).
        form_data  = await request.form()
        media_urls = [
            str(form_data[f"MediaUrl{i}"])
            for i in range(num_media)
            if f"MediaUrl{i}" in form_data
        ]

        if not media_urls:
            return twiml_reply("❌ No images found in the message. Please try again.")

        background_tasks.add_task(process_invoices_background, from_number, media_urls)

        count_word = f"{len(media_urls)} invoice{'s' if len(media_urls) > 1 else ''}"
        return twiml_reply(f"📸 Got your {count_word}! Reading now, I'll message you back shortly...")

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

    # Normalise — always work with a list, even if stored as a single dict (legacy rows)
    invoices = extracted_data if isinstance(extracted_data, list) else [extracted_data]

    # ── Confirmed ─────────────────────────────────────────────────────────────
    if body_text in CONFIRM_WORDS:
        saved   = 0
        skipped = 0
        errors  = 0

        for data in invoices:
            try:
                delivery_id = save_delivery(conn, data)

                if delivery_id is None:
                    # is_duplicate returned True — already in supplier_deliveries
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
                log.error("Failed to save delivery: %s", e)
                errors += 1

        confirm_pending(conn, pending_id)
        conn.close()

        parts = []
        if saved:
            parts.append(f"✅ {saved} invoice{'s' if saved > 1 else ''} saved successfully.")
        if skipped:
            parts.append(f"⚠️ {skipped} skipped — already exist in the system (same supplier, date and total).")
        if errors:
            parts.append(f"❌ {errors} failed to save — check the logs.")

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

    # ── Unrecognised text while a pending invoice exists ──────────────────────
    conn.close()
    return twiml_reply(
        "Reply *yes* to save or *no* to discard the last invoice.\n"
        "Or send a new photo to replace it."
    )