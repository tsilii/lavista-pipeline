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

import anthropic
import psycopg2
import requests
from fastapi import APIRouter, Form
from fastapi.responses import PlainTextResponse

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

    conn.commit()
    log.info("Invoice tables ready.")


# ── Claude Vision extraction ───────────────────────────────────────────────────

EXTRACTION_PROMPT = """
You are an invoice data extraction assistant for a restaurant.

Extract the following from the invoice image and return ONLY valid JSON, no explanation, no markdown, no backticks.

Required JSON format:
{
  "supplier_name": "string or null",
  "invoice_date": "YYYY-MM-DD or null",
  "invoice_number": "string or null",
  "items": [
    {
      "description": "string",
      "quantity": number,
      "unit_price": number,
      "subtotal": number
    }
  ],
  "total": number or null
}

Rules:
- invoice_date must be in YYYY-MM-DD format. If only month/year visible, use the 1st of that month.
- All prices must be numbers (no currency symbols).
- If an item has no quantity visible, use 1.
- If a field is not visible or unclear, use null.
- The invoice may be in English or Greek — extract correctly from both.
- Return ONLY the JSON object. Nothing else.
"""


def extract_invoice_data(image_bytes: bytes, content_type: str) -> dict | None:
    """
    Send invoice image to Claude Vision and return extracted structured data.
    Returns None if extraction fails.
    """
    if not ANTHROPIC_API_KEY:
        log.error("ANTHROPIC_API_KEY is not set — cannot extract invoice.")
        return None

    try:
        client    = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")

        # Map Twilio content types to Anthropic accepted media types
        media_type_map = {
            "image/jpeg": "image/jpeg",
            "image/jpg":  "image/jpeg",
            "image/png":  "image/png",
            "image/webp": "image/webp",
            "image/gif":  "image/gif",
        }
        media_type = media_type_map.get(content_type.lower(), "image/jpeg")

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

        raw_text = message.content[0].text.strip()
        log.info("Claude raw response: %s", raw_text[:200])

        extracted = json.loads(raw_text)
        return extracted

    except json.JSONDecodeError as e:
        log.error("Claude returned invalid JSON: %s", e)
        return None
    except Exception as e:
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


# ── Save confirmed delivery ────────────────────────────────────────────────────

def save_delivery(conn, data: dict) -> int:
    """
    Insert a confirmed invoice into supplier_deliveries + delivery_items.
    Returns the new delivery id.
    """
    supplier    = data.get("supplier_name") or "Unknown Supplier"
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


# ── Format summary for WhatsApp ────────────────────────────────────────────────

def format_summary(data: dict) -> str:
    """Format extracted invoice data into a readable WhatsApp message."""
    supplier = data.get("supplier_name") or "Unknown supplier"
    date_str = data.get("invoice_date")   or "Date not found"
    inv_num  = data.get("invoice_number")
    total    = data.get("total")
    items    = data.get("items") or []

    lines = ["📦 *Invoice detected:*", ""]
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
CANCEL_WORDS  = {"no",  "cancel", "no", "όχι", "oxi", "discard", "n"}


@router.post("/whatsapp", response_class=PlainTextResponse)
async def whatsapp_webhook(
    From:              str = Form(...),
    Body:              str = Form(default=""),
    NumMedia:          int = Form(default=0),
    MediaUrl0:         str = Form(default=None),
    MediaContentType0: str = Form(default="image/jpeg"),
):
    """
    Twilio calls this endpoint every time a WhatsApp message arrives.

    Two cases:
      1. Message contains an image → extract invoice, store pending, reply with summary
      2. Message is text → check if it's a confirmation or cancellation of a pending invoice
    """
    from_number = From.strip()
    body_text   = Body.strip().lower()

    log.info("WhatsApp message from %s | media=%d | body='%s'", from_number, NumMedia, Body[:50])

    try:
        conn = get_conn()
        init_invoice_tables(conn)
    except Exception as e:
        log.error("DB connection failed: %s", e)
        return twiml_reply("⚠️ System error — please try again in a moment.")

    # ── Case 1: Image received → extract and store as pending ─────────────────
    if NumMedia > 0 and MediaUrl0:
        result = download_twilio_image(MediaUrl0)
        if not result:
            conn.close()
            return twiml_reply("❌ Could not download the image. Please try sending it again.")

        image_bytes, content_type = result
        log.info("Downloaded image — %d bytes — %s", len(image_bytes), content_type)

        extracted = extract_invoice_data(image_bytes, content_type)
        if not extracted:
            conn.close()
            return twiml_reply(
                "❌ Could not read the invoice. "
                "Please make sure the photo is clear and well-lit, then try again."
            )

        store_pending(conn, from_number, extracted)
        conn.close()

        summary = format_summary(extracted)
        return twiml_reply(summary)

    # ── Case 2: Text message → check for confirmation or cancellation ─────────
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

    # Unrecognised text while a pending invoice exists — remind the owner
    conn.close()
    return twiml_reply(
        "Reply *yes* to save or *no* to discard the last invoice.\n"
        "Or send a new photo to replace it."
    )