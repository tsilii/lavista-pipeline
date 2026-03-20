"""
Lavista Dashboard — Streamlit analytics on PostgreSQL.
Run with: streamlit run dashboard.py
"""

import os
from datetime import datetime

import pandas as pd
import psycopg2
import streamlit as st

DATABASE_URL = os.getenv("DATABASE_URL")

st.set_page_config(page_title="Lavista Sales Dashboard", layout="wide")
st.title("Lavista Restaurant — Sales Dashboard")

# ── Load data ──────────────────────────────────────────────────────────────────

@st.cache_data(ttl=30)  # refresh every 30 s
def load_data():
    if not DATABASE_URL:
        return None, None, None

    try:
        conn = psycopg2.connect(DATABASE_URL)
    except Exception:
        return None, None, None

    txn   = pd.read_sql("SELECT * FROM transactions ORDER BY timestamp DESC", conn)
    items = pd.read_sql("SELECT * FROM transaction_items", conn)

    # Load pipeline runs if table exists
    try:
        runs = pd.read_sql(
            "SELECT * FROM pipeline_runs ORDER BY run_at DESC LIMIT 20", conn
        )
    except Exception:
        runs = pd.DataFrame()

    conn.close()

    txn["timestamp"] = pd.to_datetime(txn["timestamp"], utc=True)
    txn["date"]      = txn["timestamp"].dt.date
    txn["hour"]      = txn["timestamp"].dt.hour

    return txn, items, runs


txn, items, runs = load_data()

if txn is None or txn.empty:
    st.warning("No data yet — make sure DATABASE_URL is set and ingest.py has run.")
    st.stop()

# ── Sidebar filters ────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("Filters")

    min_date = txn["date"].min()
    max_date = txn["date"].max()
    date_range = st.date_input(
        "Date range",
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date,
    )

    all_servers = sorted(txn["server"].dropna().unique().tolist())
    selected_servers = st.multiselect(
        "Servers",
        options=all_servers,
        default=all_servers,
    )

    all_methods = sorted(txn["payment_method"].dropna().unique().tolist())
    selected_methods = st.multiselect(
        "Payment methods",
        options=all_methods,
        default=all_methods,
    )

    st.divider()
    st.caption("Auto-refreshes every 30 s")

# ── Apply filters ──────────────────────────────────────────────────────────────

if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
    start_date, end_date = date_range
else:
    start_date = end_date = date_range

filtered_txn = txn[
    (txn["date"] >= start_date)
    & (txn["date"] <= end_date)
    & (txn["server"].isin(selected_servers))
    & (txn["payment_method"].isin(selected_methods))
]

filtered_items = items[items["transaction_id"].isin(filtered_txn["transaction_id"])]

if filtered_txn.empty:
    st.warning("No transactions match the current filters.")
    st.stop()

# ── KPI row ────────────────────────────────────────────────────────────────────

total_revenue = filtered_txn["total"].sum()
total_txns    = len(filtered_txn)
avg_check     = filtered_txn["total"].mean()
top_server    = filtered_txn["server"].value_counts().idxmax()

k1, k2, k3, k4 = st.columns(4)
k1.metric("Total Revenue", f"€{total_revenue:,.2f}")
k2.metric("Transactions",  f"{total_txns:,}")
k3.metric("Avg Check",     f"€{avg_check:.2f}")
k4.metric("Top Server",    top_server)

st.divider()

# ── Revenue over time ──────────────────────────────────────────────────────────

col_left, col_right = st.columns(2)

with col_left:
    st.subheader("Revenue by Day")
    daily = filtered_txn.groupby("date")["total"].sum().reset_index()
    daily.columns = ["Date", "Revenue (€)"]
    st.bar_chart(daily.set_index("Date"))

with col_right:
    st.subheader("Transactions by Hour")
    hourly = filtered_txn.groupby("hour").size().reset_index(name="Count")
    st.bar_chart(hourly.set_index("hour"))

st.divider()

# ── Items analysis ─────────────────────────────────────────────────────────────

col_a, col_b = st.columns(2)

with col_a:
    st.subheader("Top 10 Items by Revenue")
    top_items = (
        filtered_items.groupby("item_name")["subtotal"]
        .sum()
        .sort_values(ascending=False)
        .head(10)
        .reset_index()
    )
    top_items.columns = ["Item", "Revenue (€)"]
    st.bar_chart(top_items.set_index("Item"))

with col_b:
    st.subheader("Revenue by Category")
    by_cat = (
        filtered_items.groupby("category")["subtotal"]
        .sum()
        .sort_values(ascending=False)
        .reset_index()
    )
    by_cat.columns = ["Category", "Revenue (€)"]
    st.bar_chart(by_cat.set_index("Category"))

st.divider()

# ── Payment methods & servers ──────────────────────────────────────────────────

col_p, col_s = st.columns(2)

with col_p:
    st.subheader("Payment Methods")
    pm = filtered_txn["payment_method"].value_counts().reset_index()
    pm.columns = ["Method", "Count"]
    st.dataframe(pm, use_container_width=True, hide_index=True)

with col_s:
    st.subheader("Revenue by Server")
    by_server = (
        filtered_txn.groupby("server")["total"]
        .agg(["sum", "count", "mean"])
        .rename(columns={"sum": "Revenue (€)", "count": "Transactions", "mean": "Avg Check (€)"})
        .sort_values("Revenue (€)", ascending=False)
        .reset_index()
    )
    st.dataframe(by_server, use_container_width=True, hide_index=True)

st.divider()

# ── Pipeline health ────────────────────────────────────────────────────────────

st.subheader("Pipeline Health")

if runs.empty:
    st.info("No pipeline runs recorded yet.")
else:
    last_run = runs.iloc[0]

    if last_run["status"] == "success":
        st.success(
            f"Last run: {last_run['run_at']}  |  "
            f"Fetched {last_run['fetched']}  →  "
            f"Inserted {last_run['inserted']}  |  "
            f"Skipped {last_run['skipped']} duplicates"
        )
    else:
        st.error(f"Last run FAILED: {last_run['run_at']}  |  {last_run['error_msg']}")

    with st.expander("Recent pipeline runs"):
        display_runs = runs.rename(columns={
            "run_at":    "Run at",
            "fetched":   "Fetched",
            "cleaned":   "Cleaned",
            "inserted":  "Inserted",
            "skipped":   "Skipped",
            "status":    "Status",
            "error_msg": "Error",
        })
        st.dataframe(display_runs.drop(columns=["id"]), use_container_width=True, hide_index=True)

st.divider()

# ── Raw table ──────────────────────────────────────────────────────────────────

with st.expander("Raw Transactions"):
    st.dataframe(
        filtered_txn.drop(columns=["date", "hour"]).sort_values("timestamp", ascending=False),
        use_container_width=True,
        hide_index=True,
    )