"""
🦉 Sova Bistrot — Batch Invoice Agent
Standalone tool for bulk invoice ingestion via Claude Vision.

Upload 1-100 invoice photos → Claude extracts structured data → review → confirm → saved to DB.

Run with:  streamlit run invoice_agent.py
Requires:  DATABASE_URL, ANTHROPIC_API_KEY environment variables
"""

import base64
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date
from typing import Optional

import anthropic
import pandas as pd
import psycopg2
import streamlit as st

# ── Config ─────────────────────────────────────────────────────────────────────

DATABASE_URL      = os.getenv("DATABASE_URL")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
MODEL             = os.getenv("INVOICE_MODEL", "claude-sonnet-4-20250514")
MAX_PARALLEL      = int(os.getenv("INVOICE_PARALLEL", "5"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s %(message)s")
log = logging.getLogger(__name__)

st.set_page_config(
    page_title="Sova — Invoice Agent",
    page_icon="🦉",
    layout="wide",
)

# ── Custom CSS ─────────────────────────────────────────────────────────────────

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;700&family=JetBrains+Mono:wght@400;500&display=swap');

    /* Global */
    .stApp { font-family: 'DM Sans', sans-serif; }

    /* Header */
    .agent-header {
        display: flex;
        align-items: center;
        gap: 16px;
        padding: 20px 0 10px 0;
    }
    .agent-header h1 {
        font-size: 32px;
        font-weight: 700;
        letter-spacing: 1px;
        margin: 0;
    }
    .agent-header .subtitle {
        font-size: 14px;
        color: #8b6914;
        letter-spacing: 4px;
        text-transform: uppercase;
        margin-top: -4px;
    }

    /* Status cards */
    .status-card {
        background: rgba(44, 62, 122, 0.08);
        border: 1px solid rgba(44, 62, 122, 0.15);
        border-radius: 10px;
        padding: 16px 20px;
        margin: 6px 0;
    }
    .status-card.success {
        background: rgba(26, 107, 58, 0.08);
        border-color: rgba(26, 107, 58, 0.2);
    }
    .status-card.warning {
        background: rgba(139, 105, 20, 0.08);
        border-color: rgba(139, 105, 20, 0.2);
    }
    .status-card.error {
        background: rgba(163, 45, 45, 0.08);
        border-color: rgba(163, 45, 45, 0.2);
    }

    /* Progress area */
    .progress-item {
        font-family: 'JetBrains Mono', monospace;
        font-size: 13px;
        padding: 4px 0;
        color: #aaa;
    }
    .progress-item.done { color: #1a6b3a; }
    .progress-item.fail { color: #a32d2d; }
    .progress-item.skip { color: #8b6914; }
</style>
""", unsafe_allow_html=True)


# ── Database ───────────────────────────────────────────────────────────────────

def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set.")
    return psycopg2.connect(DATABASE_URL)


def ensure_tables(conn):
    """Create all required tables if they don't exist (same schema as main pipeline)."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS supplier_deliveries (
                id              SERIAL PRIMARY KEY,
                supplier_name   TEXT    NOT NULL,
                delivery_date   DATE    NOT NULL,
                amount          NUMERIC(10, 2) NOT NULL,
                description     TEXT,
                paid            BOOLEAN NOT NULL DEFAULT FALSE,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS delivery_items (
                id              SERIAL PRIMARY KEY,
                delivery_id     INTEGER NOT NULL
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


# ── Claude Vision extraction (same prompt as whatsapp_webhook.py) ──────────────

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


def extract_invoice(image_bytes: bytes, content_type: str, filename: str) -> dict:
    """
    Send one invoice image to Claude Vision.
    Returns extracted dict, or dict with _error/_not_invoice/_overloaded keys.
    """
    client    = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")

    media_map = {
        "image/jpeg": "image/jpeg", "image/jpg": "image/jpeg",
        "image/png": "image/png",   "image/webp": "image/webp",
        "image/gif": "image/gif",
    }
    media_type = media_map.get(content_type.lower(), "image/jpeg")

    try:
        message = client.messages.create(
            model=MODEL,
            max_tokens=2048,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
                    {"type": "text",  "text": EXTRACTION_PROMPT},
                ],
            }],
        )

        raw_text = message.content[0].text.strip()
        if not raw_text:
            return {"_error": True, "filename": filename, "reason": "Empty response"}

        if raw_text.startswith("```"):
            raw_text = raw_text.split("```")[1]
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]
            raw_text = raw_text.strip()

        extracted = json.loads(raw_text)
        extracted["_filename"] = filename
        return extracted

    except json.JSONDecodeError:
        return {"_error": True, "filename": filename, "reason": "Invalid JSON from Claude"}
    except Exception as e:
        if "529" in str(e) or "overloaded" in str(e).lower():
            return {"_overloaded": True, "filename": filename}
        return {"_error": True, "filename": filename, "reason": str(e)[:200]}


# ── Duplicate detection (same logic as whatsapp_webhook.py) ────────────────────

def is_duplicate(conn, supplier_name: str, delivery_date, total: float) -> bool:
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
    except Exception:
        return False


# ── Unit normalisation (same as whatsapp_webhook.py) ───────────────────────────

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
    return UNIT_MAP.get(raw_unit.strip().lower(), raw_unit.strip())


# ── Save delivery + inventory (same logic as whatsapp_webhook.py) ──────────────

def save_delivery(conn, data: dict) -> tuple[int | None, str]:
    """
    Insert confirmed invoice into supplier_deliveries + delivery_items.
    Returns (delivery_id, status_message) — delivery_id is None if skipped.
    """
    supplier   = (data.get("supplier_name") or "Unknown Supplier").strip().title()
    raw_date   = data.get("invoice_date")
    total      = float(data.get("total") or 0.0)
    inv_number = data.get("invoice_number")
    items      = data.get("items") or []

    try:
        delivery_date = datetime.strptime(raw_date, "%Y-%m-%d").date() if raw_date else date.today()
        today = date.today()
        if (today - delivery_date).days > 60:
            corrected = delivery_date.replace(year=today.year)
            if abs((today - corrected).days) <= 60:
                delivery_date = corrected
    except ValueError:
        delivery_date = date.today()

    if is_duplicate(conn, supplier, delivery_date, total):
        return None, f"Duplicate — {supplier} on {delivery_date} €{total:.2f}"

    description = f"Invoice {inv_number}" if inv_number else "Invoice via Agent"

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
    return delivery_id, f"Saved — {supplier} — €{total:.2f} — {len(items)} items"


def save_return(conn, data: dict) -> tuple[int | None, str]:
    """
    Insert a confirmed return invoice into supplier_returns + return_items.
    Returns (return_id, status_message) — return_id is None if skipped as duplicate.
    """
    supplier   = (data.get("supplier_name") or "Unknown Supplier").strip().title()
    raw_date   = data.get("invoice_date")
    total      = float(data.get("total") or 0.0)
    inv_number = data.get("invoice_number")
    items      = data.get("items") or []

    try:
        return_date = datetime.strptime(raw_date, "%Y-%m-%d").date() if raw_date else date.today()
        today = date.today()
        if (today - return_date).days > 60:
            corrected = return_date.replace(year=today.year)
            if abs((today - corrected).days) <= 60:
                return_date = corrected
    except ValueError:
        return_date = date.today()

    # Duplicate check against supplier_returns
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id FROM supplier_returns
            WHERE LOWER(TRIM(supplier_name)) = LOWER(TRIM(%s))
              AND return_date = %s
              AND ABS(amount - %s) < 0.01
            LIMIT 1
        """, (supplier, return_date, total))
        if cur.fetchone():
            return None, f"Duplicate return — {supplier} on {return_date} €{total:.2f}"

    description = f"Return Invoice {inv_number}" if inv_number else "Return Invoice via Agent"

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
    return return_id, f"Return saved — {supplier} — €{total:.2f} — {len(items)} items"


def update_inventory(conn, delivery_id: int, items: list[dict]) -> None:
    """Upsert inventory items and record movements for a confirmed delivery."""
    with conn.cursor() as cur:
        for item in items:
            name = (item.get("description") or "").strip().title()
            qty  = float(item.get("quantity") or 0)
            unit = normalize_unit(item.get("unit") or "pcs")
            if not name or qty <= 0:
                continue

            cur.execute("SELECT id FROM inventory_items WHERE LOWER(name) = LOWER(%s)", (name,))
            existing = cur.fetchone()

            if existing:
                item_id = existing[0]
                cur.execute("""
                    UPDATE inventory_items SET quantity = quantity + %s, updated_at = NOW()
                    WHERE id = %s
                """, (qty, item_id))
            else:
                cur.execute("""
                    INSERT INTO inventory_items (name, unit, quantity, updated_at)
                    VALUES (%s, %s, %s, NOW()) RETURNING id
                """, (name, unit, qty))
                item_id = cur.fetchone()[0]

            cur.execute("""
                INSERT INTO inventory_movements
                    (item_id, movement_type, quantity, source, source_id, note)
                VALUES (%s, 'in', %s, 'delivery', %s, %s)
            """, (item_id, qty, delivery_id, f"Delivery #{delivery_id} — {qty} {unit}"))
    conn.commit()


# ── Batch processing ───────────────────────────────────────────────────────────

def process_batch(files, progress_bar, status_text) -> list[dict]:
    """
    Process all uploaded files through Claude Vision.
    Uses ThreadPoolExecutor for parallel extraction.
    Returns list of result dicts (successful extractions + errors).
    """
    total   = len(files)
    results = []
    done    = 0

    def process_one(file):
        content_type = file.type or "image/jpeg"
        return extract_invoice(file.getvalue(), content_type, file.name)

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as executor:
        futures = {executor.submit(process_one, f): f.name for f in files}

        for future in as_completed(futures):
            fname  = futures[future]
            done  += 1
            progress_bar.progress(done / total, text=f"Processing {done}/{total} — {fname}")

            try:
                result = future.result()
                results.append(result)

                if result.get("_error"):
                    status_text.markdown(
                        f'<div class="progress-item fail">✗ {fname} — {result.get("reason", "unknown error")}</div>',
                        unsafe_allow_html=True)
                elif result.get("_not_invoice"):
                    status_text.markdown(
                        f'<div class="progress-item skip">⊘ {fname} — not an invoice ({result.get("document_type", "")})</div>',
                        unsafe_allow_html=True)
                elif result.get("_overloaded"):
                    status_text.markdown(
                        f'<div class="progress-item fail">⏳ {fname} — API overloaded, try again</div>',
                        unsafe_allow_html=True)
                else:
                    supplier = result.get("supplier_name") or "Unknown"
                    total_amt = result.get("total") or 0
                    status_text.markdown(
                        f'<div class="progress-item done">✓ {fname} — {supplier} — €{total_amt:.2f}</div>',
                        unsafe_allow_html=True)

            except Exception as e:
                results.append({"_error": True, "filename": fname, "reason": str(e)[:200]})
                status_text.markdown(
                    f'<div class="progress-item fail">✗ {fname} — {str(e)[:100]}</div>',
                    unsafe_allow_html=True)

    return results


# ══════════════════════════════════════════════════════════════════════════════
# UI
# ══════════════════════════════════════════════════════════════════════════════

# Header
st.markdown("""
<div class="agent-header">
    <div>
        <h1>🦉 Invoice Agent</h1>
        <div class="subtitle">Sova Bistrot — Batch Invoice Ingestion</div>
    </div>
</div>
""", unsafe_allow_html=True)

st.markdown("Upload invoice photos → Claude extracts the data → review → confirm → saved to database & inventory.")
st.divider()

# ── Preflight checks ──────────────────────────────────────────────────────────

missing = []
if not DATABASE_URL:
    missing.append("DATABASE_URL")
if not ANTHROPIC_API_KEY:
    missing.append("ANTHROPIC_API_KEY")

if missing:
    st.error(f"Missing environment variables: {', '.join(missing)}")
    st.info("Set these in your terminal or Railway before running the agent.")
    st.stop()

# Test DB connection
try:
    _conn = get_conn()
    ensure_tables(_conn)
    _conn.close()
except Exception as e:
    st.error(f"Cannot connect to database: {e}")
    st.stop()

# ── Upload ─────────────────────────────────────────────────────────────────────

uploaded = st.file_uploader(
    "Drop invoice photos here",
    type=["jpg", "jpeg", "png", "webp"],
    accept_multiple_files=True,
    help="Select all photos at once — supports JPG, PNG, WebP",
)

if uploaded:
    st.markdown(f'<div class="status-card">📎 **{len(uploaded)} file{"s" if len(uploaded) > 1 else ""}** ready to process</div>',
                unsafe_allow_html=True)

# ── Session state ──────────────────────────────────────────────────────────────

if "extraction_results" not in st.session_state:
    st.session_state.extraction_results = None
if "confirmed" not in st.session_state:
    st.session_state.confirmed = False

# ── Step 1: Extract ───────────────────────────────────────────────────────────

if uploaded and st.session_state.extraction_results is None:
    if st.button(f"🔍 Extract all {len(uploaded)} invoices", type="primary", use_container_width=True):
        st.divider()
        st.subheader("Extraction Progress")

        progress_bar = st.progress(0, text="Starting...")
        status_area  = st.container()

        start_time = time.time()
        results    = process_batch(uploaded, progress_bar, status_area)
        elapsed    = time.time() - start_time

        st.session_state.extraction_results = results

        # Summary counts
        invoices      = [r for r in results if not r.get("_error") and not r.get("_not_invoice") and not r.get("_overloaded")]
        errors        = [r for r in results if r.get("_error")]
        not_invoices  = [r for r in results if r.get("_not_invoice")]
        overloaded    = [r for r in results if r.get("_overloaded")]

        st.divider()
        k1, k2, k3, k4, k5 = st.columns(5)
        k1.metric("Total files",     len(results))
        k2.metric("Invoices found",  len(invoices))
        k3.metric("Not invoices",    len(not_invoices))
        k4.metric("Errors",          len(errors))
        k5.metric("Time",            f"{elapsed:.0f}s")

        st.rerun()

# ── Step 2: Review & Confirm ──────────────────────────────────────────────────

if st.session_state.extraction_results is not None and not st.session_state.confirmed:
    results  = st.session_state.extraction_results
    invoices = [r for r in results if not r.get("_error") and not r.get("_not_invoice") and not r.get("_overloaded")]
    errors   = [r for r in results if r.get("_error")]
    skipped  = [r for r in results if r.get("_not_invoice")]

    st.divider()
    st.subheader(f"Review — {len(invoices)} invoices extracted")

    if not invoices:
        st.warning("No valid invoices were extracted. Check your photos and try again.")
        if st.button("🔄 Start over"):
            st.session_state.extraction_results = None
            st.rerun()
        st.stop()

    # Check for duplicates ahead of time
    conn = get_conn()
    for inv in invoices:
        supplier = (inv.get("supplier_name") or "Unknown").strip().title()
        raw_date = inv.get("invoice_date")
        total    = float(inv.get("total") or 0)
        try:
            d_date = datetime.strptime(raw_date, "%Y-%m-%d").date() if raw_date else date.today()
        except ValueError:
            d_date = date.today()
        inv["_is_duplicate"] = is_duplicate(conn, supplier, d_date, total)
    conn.close()

    # Build summary table
    table_rows = []
    for i, inv in enumerate(invoices):
        table_rows.append({
            "#":        i + 1,
            "File":     inv.get("_filename", "—"),
            "Supplier": (inv.get("supplier_name") or "Unknown").strip(),
            "Date":     inv.get("invoice_date") or "—",
            "Invoice #": inv.get("invoice_number") or "—",
            "Items":    len(inv.get("items") or []),
            "Total":    f"€{float(inv.get('total') or 0):,.2f}",
            "Status":   "⚠️ Duplicate" if inv.get("_is_duplicate") else ("↩️ Return" if inv.get("_is_return") else "✅ New"),
        })

    df = pd.DataFrame(table_rows)
    st.dataframe(df, use_container_width=True, hide_index=True)

    # Grand total
    new_invoices   = [inv for inv in invoices if not inv.get("_is_duplicate")]
    dup_invoices   = [inv for inv in invoices if inv.get("_is_duplicate")]
    grand_total    = sum(float(inv.get("total") or 0) for inv in new_invoices)

    col_summary = st.columns(4)
    col_summary[0].metric("New invoices",  len(new_invoices))
    col_summary[1].metric("Duplicates",    len(dup_invoices))
    col_summary[2].metric("Errors/Skipped", len(errors) + len(skipped))
    col_summary[3].metric("Grand Total",   f"€{grand_total:,.2f}")

    if dup_invoices:
        st.info(f"{len(dup_invoices)} duplicate invoice(s) will be skipped automatically.")
    if errors:
        with st.expander(f"⚠️ {len(errors)} file(s) failed"):
            for e in errors:
                st.markdown(f"- **{e.get('filename', '?')}** — {e.get('reason', 'unknown')}")
    if skipped:
        with st.expander(f"📄 {len(skipped)} file(s) were not invoices"):
            for s in skipped:
                st.markdown(f"- **{s.get('_filename', '?')}** — {s.get('document_type', '')}")

    # Expandable detail per invoice
    with st.expander("📋 View all extracted line items"):
        for i, inv in enumerate(invoices):
            supplier = inv.get("supplier_name") or "Unknown"
            items    = inv.get("items") or []
            dup_tag  = " ⚠️ DUPLICATE" if inv.get("_is_duplicate") else ""
            st.markdown(f"**{i+1}. {supplier}**{dup_tag}")
            if items:
                item_rows = []
                for item in items:
                    item_rows.append({
                        "Description": item.get("description", "—"),
                        "Qty":         item.get("quantity", "?"),
                        "Unit":        item.get("unit", ""),
                        "Unit Price":  f"€{float(item.get('unit_price') or 0):.2f}",
                        "Subtotal":    f"€{float(item.get('subtotal') or 0):.2f}",
                    })
                st.dataframe(pd.DataFrame(item_rows), use_container_width=True, hide_index=True)
            st.markdown("---")

    # Action buttons
    st.divider()
    col_confirm, col_cancel = st.columns(2)

    with col_confirm:
        if new_invoices:
            if st.button(f"✅ Confirm & Save {len(new_invoices)} invoice(s)", type="primary", use_container_width=True):
                conn    = get_conn()
                saved   = 0
                skipped_count = 0
                err_count     = 0

                save_progress = st.progress(0, text="Saving...")

                for idx, inv in enumerate(new_invoices):
                    try:
                        if inv.get("_is_return"):
                            result_id, msg = save_return(conn, inv)
                            if result_id:
                                saved += 1
                            else:
                                skipped_count += 1
                        else:
                            delivery_id, msg = save_delivery(conn, inv)
                            if delivery_id:
                                items = inv.get("items") or []
                                if items:
                                    try:
                                        update_inventory(conn, delivery_id, items)
                                    except Exception as e:
                                        log.error("Inventory update failed for delivery %d: %s", delivery_id, e)
                                saved += 1
                            else:
                                skipped_count += 1
                    except Exception as e:
                        log.error("Save failed: %s", e)
                        err_count += 1

                    save_progress.progress((idx + 1) / len(new_invoices),
                                           text=f"Saving {idx + 1}/{len(new_invoices)}...")

                conn.close()
                st.session_state.confirmed = True

                st.divider()
                if saved:
                    st.success(f"✅ **{saved} invoice(s) saved** to database. Inventory updated.")
                if skipped_count:
                    st.warning(f"⚠️ {skipped_count} skipped as duplicates.")
                if err_count:
                    st.error(f"❌ {err_count} failed to save — check logs.")
        else:
            st.info("All invoices are duplicates — nothing to save.")

    with col_cancel:
        if st.button("🗑️ Discard all & start over", use_container_width=True):
            st.session_state.extraction_results = None
            st.session_state.confirmed = False
            st.rerun()

# ── Post-confirmation ─────────────────────────────────────────────────────────

if st.session_state.confirmed:
    st.divider()
    if st.button("🔄 Process another batch", type="primary", use_container_width=True):
        st.session_state.extraction_results = None
        st.session_state.confirmed = False
        st.rerun()

# ── Footer ─────────────────────────────────────────────────────────────────────
st.divider()
st.caption(f"Model: {MODEL} | Max parallel: {MAX_PARALLEL} | Connected to: {'✅ PostgreSQL' if DATABASE_URL else '❌ No DB'}")