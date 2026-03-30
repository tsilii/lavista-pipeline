# 🦉 Sova Bistrot — Business Intelligence Pipeline

A fully automated, cloud-deployed restaurant analytics pipeline. Ingests sales data every hour during operating hours from a mock POS API, stores it in PostgreSQL, and serves a live multi-page Streamlit dashboard with sales analytics, payroll, expenses, suppliers, P&L, and real-time inventory tracking.

Suppliers can send invoice photos via WhatsApp — Claude Vision extracts the data automatically, the owner confirms in one message, and the delivery is saved to the database with stock levels updated instantly.

**Live dashboard →** [lavista-pipeline-production.up.railway.app](https://lavista-pipeline-production.up.railway.app)

---

## Overview

```
POS API (FastAPI)  →  Ingest Scheduler  →  PostgreSQL  →  Streamlit Dashboard
  (mock POS)           (cron 08:30-16:30)   (Railway)       (live, public URL)

WhatsApp Photo  →  Claude Vision  →  Owner Confirms  →  PostgreSQL  →  Inventory Updated
  (invoice)         (extraction)      (replies yes)      (Railway)
```

---

## Stack

| Layer | Technology |
|-------|-----------|
| Data source | FastAPI mock POS API |
| Ingestion | Python + APScheduler (cron) |
| Invoice ingestion | WhatsApp (Twilio Sandbox) + Claude Vision API |
| Database | PostgreSQL (Railway) |
| Dashboard | Streamlit + Plotly |
| Deployment | Railway (3 services) |
| Testing | pytest (31 tests) |
| Language | Python 3.13 |

---

## Dashboard Pages

| Page | What it shows |
|------|--------------|
| **Home** | Live KPIs with month-over-month deltas — revenue, transactions, avg check, payroll, supplier balance |
| **Sales** | Plotly charts — revenue by day, transactions by hour, top items, category donut, server performance with dual axis |
| **Payroll** | Staff overview, salary by role, payroll vs revenue ratio |
| **Expenses** | Monthly costs by category, stacked historical chart, add/edit/delete from dashboard |
| **Suppliers** | Delivery log, monthly balance, carried-over unpaid amounts, historical pivot table |
| **P&L** | Full P&L statement, revenue trend vs break-even, cumulative profitability chart, capital runway tracker |
| **Inventory** | Current stock levels, low stock alerts, manual adjustments, reorder thresholds, movement log |

---

## Architecture

### Services on Railway

```
┌──────────────────────────────────────────────────────────────────┐
│                         Railway Cloud                            │
│                                                                  │
│  ┌──────────────┐    ┌──────────────┐                           │
│  │  pos_api.py  │    │  ingest.py   │                           │
│  │  (FastAPI)   │◄───│  (cron job)  │                           │
│  │  /sales      │    │  08:30-16:30 │                           │
│  │  /whatsapp   │    └──────┬───────┘                           │
│  └──────────────┘           │                                   │
│         ▲                   │                                   │
│         │            ┌──────▼───────┐                           │
│  WhatsApp/Twilio      │  PostgreSQL  │                           │
│  + Claude Vision      │  (Railway)   │                           │
│                       └──────┬───────┘                           │
│                              │                                   │
│                       ┌──────▼───────┐                           │
│                       │ dashboard.py │                           │
│                       │  (Streamlit) │                           │
│                       │  public URL  │                           │
│                       └──────────────┘                           │
└──────────────────────────────────────────────────────────────────┘
```

### WhatsApp Invoice Ingestion Flow

```
1. Owner photographs invoice at delivery
2. Sends photo to WhatsApp sandbox number
3. Twilio forwards to POST /whatsapp on pos_api
4. Claude Vision extracts: supplier, date, invoice #, line items, total
5. Bot replies with structured summary for owner review
6. Owner replies "yes" / "ναι" / ✅ to confirm
7. Delivery saved to supplier_deliveries + delivery_items
8. Inventory stock levels updated automatically per line item
9. Appears instantly in Suppliers and Inventory dashboard pages
```

### Database Schema

```
transactions          transaction_items       employees
─────────────         ─────────────────       ─────────────
id                    id                      id
transaction_id        transaction_id (FK)     name
timestamp             item_name               role
table_number          category                monthly_salary
server                unit_price              start_date
total                 quantity                active
payment_method        subtotal
ingested_at

expenses              supplier_deliveries     pipeline_runs
────────────          ───────────────────     ─────────────────
id                    id                      id
category              supplier_name           run_at
description           delivery_date           fetched
amount                amount                  cleaned
frequency             description             inserted
month                 paid                    skipped
                      created_at              filtered
                                              status
cursor_state                                  error_msg
────────────
id
last_seen

delivery_items        pending_invoices         inventory_items
───────────────       ────────────────         ────────────────
id                    id                       id
delivery_id (FK)      from_number              name
description           extracted_data (JSONB)   unit
quantity              created_at               quantity
unit_price            status                   reorder_threshold
subtotal                                       updated_at

inventory_movements
────────────────────
id
item_id (FK)
movement_type  (in / out)
quantity
source         (delivery / sale / manual)
source_id
note
created_at
```

---

## Pipeline Design

### Sales ingestion flow

```
1. fetch_sales()         → GET /sales?n=4 from POS API
2. filter_by_cursor()    → remove transactions older than last run timestamp
3. clean_transaction()   → validate fields, items, prices, quantities
4. load_transactions()   → INSERT with savepoints for per-transaction atomicity
5. deduct_inventory()    → subtract sold items from stock (1-1 name match)
6. log_run()             → record result in pipeline_runs audit table
7. set_cursor()          → advance high-water mark to current run time
```

### Invoice ingestion flow

```
1. Twilio POST /whatsapp  → receive image URL + sender number
2. download_twilio_image()→ fetch image with Basic Auth
3. extract_invoice_data() → Claude Vision API → structured JSON
4. store_pending()        → save to pending_invoices (status = pending)
5. twiml_reply()          → send formatted summary back to owner
6. Owner replies yes      → save_delivery() → supplier_deliveries + delivery_items
7. update_inventory()     → upsert inventory_items, log inventory_movements
8. confirm_pending()      → mark pending_invoices as confirmed
```

### High-water mark cursor

The cursor stores the timestamp of each successful pipeline run. On the next run, `filter_by_cursor()` discards any transaction whose timestamp is older than or equal to the cursor. This ensures only genuinely new transactions are processed.

### Data quality

Every transaction is validated before insertion:
- Required fields check (`transaction_id`, `timestamp`, `total`, `items`)
- Negative total rejection
- Timestamp format validation
- Item-level validation: empty items rejected, negative prices rejected, zero/negative quantity rejected
- Mixed valid/invalid items: valid items kept, invalid items dropped
- Server name and payment method normalisation
- Duplicate prevention via `ON CONFLICT DO NOTHING`

### Transaction atomicity

Each transaction in a batch is wrapped in a PostgreSQL savepoint. If one transaction fails to insert, only that transaction is rolled back — the rest of the batch commits successfully.

### Connection handling

All database connections use `try/finally` to guarantee the connection closes even if an exception occurs mid-function.

### Inventory tracking

Stock levels are updated in two directions automatically:
- **Deliveries confirmed via WhatsApp** → stock goes up per line item
- **Sales ingested from POS** → stock goes down for 1-1 matched items (by name)
- **Manual adjustments** available in the dashboard for corrections and waste

---

## Running Locally

### Prerequisites
- Python 3.11+
- PostgreSQL (local or Railway)
- Anthropic API key (for invoice extraction)
- Twilio account (for WhatsApp webhook)

### Setup

```bash
git clone https://github.com/tsilii/lavista-pipeline.git
cd lavista-pipeline
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Environment variables

```bash
export DATABASE_URL="postgresql://postgres:xxxx@host:port/railway"
export POS_API_URL="https://awake-vitality-production-2c01.up.railway.app/sales"
export ANTHROPIC_API_KEY="sk-ant-..."
export TWILIO_ACCOUNT_SID="ACxxxx..."
export TWILIO_AUTH_TOKEN="xxxx..."
```

### Start the pipeline

```bash
# Terminal 1 — POS API + WhatsApp webhook
uvicorn pos_api:app --port 8000

# Terminal 2 — Ingest scheduler
python ingest.py --schedule

# Terminal 3 — Dashboard
streamlit run dashboard.py
```

### Seed static data

```bash
python seed_employees.py   # insert staff — run once, update when salaries change
python seed_expenses.py    # insert monthly expenses — run once per month
```

---

## Deployment (Railway)

Three services deployed from the same GitHub repo:

| Service | Start command | Purpose |
|---------|--------------|---------|
| `lavista-pipeline` | `streamlit run dashboard.py --server.port $PORT --server.address 0.0.0.0` | Public dashboard |
| `awake-vitality` | `uvicorn pos_api:app --host 0.0.0.0 --port $PORT` | POS API + WhatsApp webhook |
| `stellar-benevolence` | `python ingest.py --schedule` | Ingest scheduler |

### Environment variables required per service

| Variable | lavista-pipeline | awake-vitality | stellar-benevolence |
|----------|-----------------|----------------|---------------------|
| `DATABASE_URL` | ✅ | ✅ | ✅ |
| `ANTHROPIC_API_KEY` | ✅ | ✅ | — |
| `TWILIO_ACCOUNT_SID` | — | ✅ | — |
| `TWILIO_AUTH_TOKEN` | — | ✅ | — |

### Deploy changes

```bash
git add .
git commit -m "your message"
git push
# Railway auto-redeploys on push to main
```

---

## WhatsApp Setup (Twilio Sandbox)

1. Create a Twilio account at [twilio.com](https://twilio.com)
2. Go to **Messaging → Try it out → Send a WhatsApp message**
3. Send `join strange-separate` to +1 415-523-8886 from your WhatsApp
4. Set the webhook URL to `https://awake-vitality-production-2c01.up.railway.app/whatsapp` (POST)
5. Add `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN` to `awake-vitality` environment variables

Supported confirmation replies: `yes`, `ναι`, `nai`, `✅`, `ok`
Supported cancellation replies: `no`, `cancel`, `όχι`

---

## Testing

```bash
pytest test_ingest.py -v
```

31 tests covering:
- Happy path validation
- Missing required fields
- Invalid values (negative totals, bad timestamps)
- Item-level validation (empty items, negative prices, zero quantity)
- Mixed valid/invalid items
- Edge cases (zero total, empty strings, extra fields)
- `filter_by_cursor()` — all scenarios including no cursor, older transactions, newer transactions, empty input

---

## Key Engineering Decisions

**High-water mark cursor** — each run stores its start timestamp. The next run filters out any transaction older than this mark, implementing true incremental ingestion without relying on the API to filter data.

**Transaction atomicity with savepoints** — each transaction in a batch is wrapped in a PostgreSQL savepoint. A failure in one transaction rolls back only that transaction, not the entire batch.

**try/finally connection handling** — all database connections are closed in `finally` blocks, guaranteeing no connection leaks even under exceptions.

**Pending invoice confirmation flow** — extracted invoice data is stored as `pending` before any write to supplier tables. Nothing enters the financial records without explicit owner confirmation. This prevents bad extractions from corrupting data silently.

**Inventory auto-creation** — when a delivery is confirmed, inventory items are upserted by name. If an item doesn't exist yet, it's created automatically. This means zero setup is required — the inventory populates itself as deliveries are confirmed.

**1-1 inventory deduction** — sales deduct stock by matching item name directly against inventory. No recipe mapping needed for bottled products, wines, and sodas — what's sold is what's deducted.

**Claude Vision extraction** — invoices in English and Greek are both handled. The extraction prompt enforces strict JSON output, normalises date formats, and falls back gracefully when fields are missing or unclear.

**SQLite → PostgreSQL** — migrated from SQLite to support concurrent reads/writes across three independently deployed cloud services.

**Separation of concerns** — three independent Railway services with single responsibilities. Failures don't cascade.

---

## Project Structure

```
lavista-pipeline/
├── pos_api.py            # POS API + WhatsApp webhook router
├── whatsapp_webhook.py   # Invoice ingestion — extract, confirm, save, update inventory
├── ingest.py             # Pipeline — fetch, filter, clean, load, deduct inventory
├── dashboard.py          # Streamlit dashboard — 7 pages including Inventory
├── seed_employees.py     # One-time script — insert staff into DB
├── seed_expenses.py      # Monthly script — insert expenses into DB
├── test_ingest.py        # 31 pytest tests
├── Procfile              # Railway process definitions
├── requirements.txt      # Python dependencies
└── README.md
```

---

## Requirements

```
fastapi>=0.110.0
uvicorn[standard]>=0.29.0
requests>=2.31.0
apscheduler>=3.10.4
streamlit>=1.33.0
pandas>=2.2.0
psycopg2-binary>=2.9.9
plotly>=5.20.0
pytest>=8.0.0
anthropic>=0.25.0
python-multipart>=0.0.9
```