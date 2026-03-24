# 🦉 Sova Bistrot — Business Intelligence Pipeline

A fully automated, cloud-deployed restaurant analytics pipeline. Ingests sales data every hour during operating hours from a mock POS API, stores it in PostgreSQL, and serves a live multi-page Streamlit dashboard with sales analytics, payroll, expenses, suppliers, and a full P&L statement.

**Live dashboard →** [lavista-pipeline-production.up.railway.app](https://lavista-pipeline-production.up.railway.app)

---

## Overview

```
POS API (FastAPI)  →  Ingest Scheduler  →  PostgreSQL  →  Streamlit Dashboard
  (mock POS)           (cron 08:30-16:30)   (Railway)       (live, public URL)
```

---

## Stack

| Layer | Technology |
|-------|-----------|
| Data source | FastAPI mock POS API |
| Ingestion | Python + APScheduler (cron) |
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
| **Inventory** | Coming soon |

---

## Architecture

### Services on Railway

```
┌─────────────────────────────────────────────────────┐
│                    Railway Cloud                     │
│                                                      │
│  ┌──────────────┐    ┌──────────────┐               │
│  │  pos_api.py  │    │  ingest.py   │               │
│  │  (FastAPI)   │◄───│  (cron job)  │               │
│  │  Port 8000   │    │  08:30-16:30 │               │
│  └──────────────┘    └──────┬───────┘               │
│                             │                        │
│                      ┌──────▼───────┐               │
│                      │  PostgreSQL  │               │
│                      │  (Railway)   │               │
│                      └──────┬───────┘               │
│                             │                        │
│                      ┌──────▼───────┐               │
│                      │ dashboard.py │               │
│                      │  (Streamlit) │               │
│                      │  public URL  │               │
│                      └──────────────┘               │
└─────────────────────────────────────────────────────┘
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
```

---

## Pipeline Design

### Ingestion flow

```
1. fetch_sales()         → GET /sales?n=4 from POS API
2. filter_by_cursor()    → remove transactions older than last run timestamp
3. clean_transaction()   → validate fields, items, prices, quantities
4. load_transactions()   → INSERT with savepoints for per-transaction atomicity
5. log_run()             → record result in pipeline_runs audit table
6. set_cursor()          → advance high-water mark to current run time
```

### High-water mark cursor

The cursor stores the timestamp of each successful pipeline run. On the next run, `filter_by_cursor()` discards any transaction whose timestamp is older than or equal to the cursor. This ensures only genuinely new transactions are processed — the POS API generates transactions with the current timestamp, so each hourly run produces 4 new transactions that pass the cursor filter.

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

Each transaction in a batch is wrapped in a PostgreSQL savepoint. If one transaction fails to insert, only that transaction is rolled back — the rest of the batch commits successfully. This prevents a single bad record from silently discarding an entire batch.

### Connection handling

All database connections use `try/finally` to guarantee the connection closes even if an exception occurs mid-function. Write operations use `conn.rollback()` in the except block to clean up partial state.

---

## Running Locally

### Prerequisites
- Python 3.11+
- PostgreSQL (local or Railway)

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
```

### Start the pipeline

```bash
# Terminal 1 — POS API (optional if using Railway URL above)
uvicorn pos_api:app --port 8000

# Terminal 2 — Ingest scheduler
python ingest.py --schedule

# Terminal 3 — Dashboard (optional, Railway hosts the live version)
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
| `awake-vitality` | `uvicorn pos_api:app --host 0.0.0.0 --port $PORT` | Mock POS API |
| `stellar-benevolence` | `python ingest.py --schedule` | Ingest scheduler |

### Deploy changes

```bash
git add .
git commit -m "your message"
git push
# Railway auto-redeploys on push to main
```

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

**SQLite → PostgreSQL** — migrated from SQLite to support concurrent reads/writes across three independently deployed cloud services.

**Separation of concerns** — three independent Railway services with single responsibilities. Failures don't cascade.

**Item-level validation** — data quality enforced at the item level, not just the transaction level. Invalid items are filtered individually before the transaction is rejected or accepted.

---

## Project Structure

```
lavista-pipeline/
├── pos_api.py          # Mock POS API — generates transactions with current timestamps
├── ingest.py           # Pipeline — fetch, filter, clean, load into Postgres
├── dashboard.py        # Streamlit dashboard — 7 pages
├── seed_employees.py   # One-time script — insert staff into DB
├── seed_expenses.py    # Monthly script — insert expenses into DB
├── test_ingest.py      # 31 pytest tests
├── Procfile            # Railway process definitions
├── requirements.txt    # Python dependencies
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
```