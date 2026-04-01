"""
WhatsApp webhook — receives invoice photos via WhatsApp (Twilio),
extracts structured data using Claude Vision, stores a pending confirmation
in the DB, and replies to the owner with a summary for approval.

Flow:
  Owner sends photo  →  extract via Claude  →  store as pending  →  reply with summary
  Owner replies ✅   →  save to supplier_deliveries + delivery_items  →  confirm

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
from fastapi import APIRouter, BackgroundTasks, Form
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

Extract the following and return ONLY valid JSON. No explanation, no markdown, no backticks.

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
- Use the invoice date (Ημερομηνία), not any delivery or due date.
- Format must be YYYY-MM-DD. If only month/year visible, use the 1st of that month.

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
- description: product name always in Greek. If the name is in English or Latin characters (e.g. "rigani", "RIGANI", "tomatoes"), translate or transliterate it to the standard Greek name (e.g. "Ριγανη", "Ντοματες"). If you are unsure of the Greek name, keep the original. Always use Title Case (first letter capital, rest lowercase).
- quantity: the QTY or Ποσότητα column value.
- unit_price: the Price or Τιμή column value.
- subtotal: use the Net or Σύνολο or Αξία column — this is the final line value after any discount.
- SKIP items where subtotal is 0.00 — these are free packaging or empty rows.
- If no unit price is visible, calculate it as subtotal / quantity.
- All numbers must be plain numbers — no currency symbols, no commas as thousands separators.

GENERAL rules:
- If a field is genuinely not visible or unclear, use null.
- Do not guess or invent values.
- Return ONLY the JSON object. Nothing else.
"""


def extract_invoice_data(image_bytes: bytes, content_type: str) -> dict | None:
    """
    Send invoice image to Claude Vision and return extracted structured data.
    Retries up to 3 times on overload errors (529) with exponential backoff.
    Returns None if extraction fails after all retries.
    """
    import time

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
            max_tokens=1024,
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

        raw_text  = message.content[0].text.strip()
        log.info("Claude raw response: %s", raw_text[:200])
        extracted = json.loads(raw_text)
        return extracted

    except json.JSONDecodeError as e:
        log.error("Claude returned invalid JSON: %s", e)
        return None

    except Exception as e:
        error_str = str(e)
        if "529" in error_str or "overloaded" in error_str.lower():
            log.warning("Anthropic API overloaded — returning overload signal")
            return {"_overloaded": True}  # special signal so we can give a better message
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

def store_pending(conn, from_number: str, data: dict) -> int:
    """Store extracted invoice data as pending confirmation. Returns the new row id."""
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

            # Upsert — on conflict update quantity but preserve the existing unit
            # so the first delivery sets the unit and subsequent ones respect it
            cur.execute("""
                INSERT INTO inventory_items (name, unit, quantity, updated_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (name) DO UPDATE SET
                    quantity   = inventory_items.quantity + EXCLUDED.quantity,
                    updated_at = NOW()
                RETURNING id, unit
            """, (name, unit, qty))

            row     = cur.fetchone()
            item_id = row[0]

            cur.execute("""
                INSERT INTO inventory_movements
                    (item_id, movement_type, quantity, source, source_id, note)
                VALUES (%s, 'in', %s, 'delivery', %s, %s)
            """, (item_id, qty, delivery_id, f"Delivery #{delivery_id} — {qty} {unit}"))

    conn.commit()


# ── Save confirmed delivery ────────────────────────────────────────────────────

def save_delivery(conn, data: dict) -> int:
    """
    Insert a confirmed invoice into supplier_deliveries + delivery_items.
    Returns the new delivery id.
    """
    supplier    = (data.get("supplier_name") or "Unknown Supplier").strip().title()
    raw_date    = data.get("invoice_date")
    total       = data.get("total") or 0.0
    inv_number  = data.get("invoice_number")
    items       = data.get("items") or []

    # Parse date — fall back to today if missing or invalid
    try:
        delivery_date = datetime.strptime(raw_date, "%Y-%m-%d").date() if raw_date else datetime.today().date()
    except ValueError:
        delivery_date = datetime.today().date()

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
    """Format extracted invoice data into a readable WhatsApp message."""
    from datetime import date as date_type, timedelta

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
            desc  = item.get("description", "—")
            qty   = item.get("quantity",    "?")
            price = item.get("unit_price",  0)
            sub   = item.get("subtotal",    0)
            lines.append(f"  • {desc} x{qty} — €{sub:.2f}")

    lines.append("")
    if total is not None:
        lines.append(f"*Total: €{total:.2f}*")

    lines.append("")
    lines.append("Reply *yes* or *ναι* or ✅ to save.")
    lines.append("Reply *no* or *cancel* to discard.")

    return "\n".join(lines)


# ── Webhook endpoint ───────────────────────────────────────────────────────────

CONFIRM_WORDS = {"yes", "ναι", "nai", "✅", "y", "confirm", "ok", "okay", "ок"}
CANCEL_WORDS  = {"no",  "cancel", "όχι", "oxi", "discard", "n"}


def process_invoice_background(from_number: str, media_url: str, content_type: str) -> None:
    """
    Background task — runs after Twilio's 15s window has closed.
    Downloads image, extracts with Claude, stores as pending, sends result via Twilio API.
    """
    log.info("Background extraction starting for %s", from_number)

    result = download_twilio_image(media_url)
    if not result:
        send_whatsapp_message(from_number,
            "❌ Could not download the image. Please try sending it again.")
        return

    image_bytes, detected_type = result
    log.info("Downloaded image — %d bytes — %s", len(image_bytes), detected_type)

    extracted = extract_invoice_data(image_bytes, content_type)
    if not extracted:
        send_whatsapp_message(from_number,
            "❌ Could not read the invoice. "
            "Please make sure the photo is clear and well-lit, then try again.")
        return

    if extracted.get("_overloaded"):
        send_whatsapp_message(from_number,
            "⏳ The system is busy right now. Please send the photo again in a moment.")
        return

    try:
        conn = get_conn()
        init_invoice_tables(conn)
        store_pending(conn, from_number, extracted)
        conn.close()
    except Exception as e:
        log.error("DB error in background task: %s", e)
        send_whatsapp_message(from_number, "⚠️ System error — please try again.")
        return

    summary = format_summary(extracted)
    send_whatsapp_message(from_number, summary)
    log.info("Background extraction complete for %s", from_number)


@router.post("/whatsapp", response_class=PlainTextResponse)
async def whatsapp_webhook(
    background_tasks:  BackgroundTasks,
    From:              str           = Form(...),
    Body:              str           = Form(default=""),
    NumMedia:          Optional[str] = Form(default="0"),
    MediaUrl0:         Optional[str] = Form(default=None),
    MediaContentType0: Optional[str] = Form(default="image/jpeg"),
):
    """
    Twilio calls this endpoint every time a WhatsApp message arrives.

    Two cases:
      1. Image → reply instantly to Twilio, extract in background, send result via API
      2. Text  → check for confirmation or cancellation of a pending invoice
    """
    from_number = From.strip()
    body_text   = Body.strip().lower()
    num_media   = int(NumMedia or "0")

    log.info("WhatsApp message from %s | media=%s | body='%s'", from_number, num_media, Body[:50])

    # ── Case 1: Image received → reply immediately, process in background ─────
    if num_media > 0 and MediaUrl0:
        background_tasks.add_task(
            process_invoice_background,
            from_number,
            MediaUrl0,
            MediaContentType0 or "image/jpeg",
        )
        return twiml_reply("📸 Got your invoice! Reading it now, I'll message you back in a moment...")

    # ── Case 2: Text message → confirmation or cancellation ───────────────────
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

    # Confirmation
    if body_text in CONFIRM_WORDS:
        try:
            delivery_id = save_delivery(conn, pending["data"])
            update_inventory_for_delivery(conn, delivery_id, pending["data"].get("items") or [])
            confirm_pending(conn, pending["id"])
            conn.close()
            supplier = pending["data"].get("supplier_name") or "supplier"
            total    = pending["data"].get("total") or 0
            return twiml_reply(
                f"✅ Saved!\n\n"
                f"Delivery from *{supplier}* — €{total:.2f} — recorded in the dashboard."
            )
        except Exception as e:
            log.error("Failed to save delivery: %s", e)
            conn.close()
            return twiml_reply("⚠️ Error saving the delivery. Please try again.")

    # Cancellation
    if body_text in CANCEL_WORDS:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE pending_invoices SET status = 'cancelled' WHERE id = %s",
                (pending["id"],)
            )
        conn.commit()
        conn.close()
        return twiml_reply("🗑️ Invoice discarded. Send a new photo when ready.")

    # Unrecognised text while a pending invoice exists
    conn.close()
    return twiml_reply(
        "Reply *yes* to save or *no* to discard the last invoice.\n"
        "Or send a new photo to replace it."
    )