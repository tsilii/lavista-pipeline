# Lavista Pipeline

End-to-end restaurant data pipeline — FastAPI mock POS → ingestion → PostgreSQL → Streamlit dashboard.

---

## Live Dashboard

The dashboard is always available at:
```
https://lavista-pipeline-production.up.railway.app
```
This runs 24/7 on Railway regardless of whether your laptop is on or off.

---

## Architecture

```
pos_api.py          ingest.py           PostgreSQL          dashboard.py
(fake POS API)  →   (scheduler)     →   (Railway)       →   (Railway / local)

Runs locally        Runs locally        Always on           Always on (Railway)
Port 8000           Every 1 minute      railway.internal    railway.app URL
```

---

## First Time Setup

**1. Clone the repo and create a virtual environment:**
```bash
git clone https://github.com/tsilii/lavista-pipeline.git
cd lavista-pipeline
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**2. Add your Railway DATABASE_URL permanently:**
```bash
echo 'export DATABASE_URL="your_railway_public_url"' >> ~/.zshrc
source ~/.zshrc
```
Get the URL from: Railway → Postgres → Variables → DATABASE_PUBLIC_URL

---

## Running the Pipeline

Open 2 terminals:

**Terminal 1 — fake POS API:**
```bash
source .venv/bin/activate
uvicorn pos_api:app --port 8000
```

**Terminal 2 — ingest scheduler:**
```bash
source .venv/bin/activate
python ingest.py --schedule
```

That's it. Data will flow into Railway's Postgres every 1 minute.
The Railway dashboard will show live data automatically.

---

## Running the Local Dashboard (optional)

Only needed if you want to test locally instead of using the Railway URL.

**Terminal 3:**
```bash
source .venv/bin/activate
streamlit run dashboard.py
```
Opens at: http://localhost:8501

---

## Stopping the Pipeline

- Press **Ctrl+C** in Terminal 1 to stop the POS API
- Press **Ctrl+C** in Terminal 2 to stop the ingest scheduler
- The Railway dashboard keeps running regardless

---

## Running Tests

```bash
source .venv/bin/activate
pytest test_ingest.py -v
```

---

## Environment Variables

| Variable | Where | Description |
|----------|-------|-------------|
| `DATABASE_URL` | local terminal | Railway public Postgres URL |
| `DATABASE_URL` | Railway lavista-pipeline service | Set via Variable Reference to Postgres |
| `PORT` | Railway lavista-pipeline service | Set to 8080 |
| `POS_API_URL` | optional | Defaults to http://127.0.0.1:8000/sales |
| `TRANSACTIONS_PER_FETCH` | optional | Defaults to 20 |
| `SCHEDULE_MINUTES` | optional | Defaults to 1 |

---

## Project Structure

```
lavista-pipeline/
├── pos_api.py          # Mock POS API — generates fake transactions
├── ingest.py           # Pipeline — fetch, clean, load into Postgres
├── dashboard.py        # Streamlit dashboard
├── test_ingest.py      # Pytest tests for clean_transaction()
├── Procfile            # Railway deployment config
├── requirements.txt    # Python dependencies
└── README.md           # This file
```

---

## Deploying Changes

Any push to the `main` branch automatically redeploys on Railway:
```bash
git add .
git commit -m "your message"
git push
```