"""
Lavista Dashboard — Streamlit analytics on PostgreSQL.
Run with: streamlit run dashboard.py
"""

import os
import pandas as pd
import psycopg2
import streamlit as st

DATABASE_URL = os.getenv("DATABASE_URL")

st.set_page_config(page_title="Lavista Sales Dashboard", layout="wide")

# ── Database connection ────────────────────────────────────────────────────────

def get_conn():
    if not DATABASE_URL:
        return None
    try:
        return psycopg2.connect(DATABASE_URL)
    except Exception:
        return None


# ── Load data ──────────────────────────────────────────────────────────────────

@st.cache_data(ttl=30)
def load_sales_data():
    conn = get_conn()
    if not conn:
        return None, None, None
    txn   = pd.read_sql("SELECT * FROM transactions ORDER BY timestamp DESC", conn)
    items = pd.read_sql("SELECT * FROM transaction_items", conn)
    try:
        runs = pd.read_sql("SELECT * FROM pipeline_runs ORDER BY run_at DESC LIMIT 20", conn)
    except Exception:
        runs = pd.DataFrame()
    conn.close()
    txn["timestamp"] = pd.to_datetime(txn["timestamp"], utc=True)
    txn["date"]      = txn["timestamp"].dt.date
    txn["hour"]      = txn["timestamp"].dt.hour
    return txn, items, runs


@st.cache_data(ttl=60)
def load_payroll_data():
    conn = get_conn()
    if not conn:
        return None
    try:
        employees = pd.read_sql(
            "SELECT * FROM employees WHERE active = TRUE ORDER BY monthly_salary DESC", conn
        )
        conn.close()
        return employees
    except Exception:
        conn.close()
        return None


@st.cache_data(ttl=60)
def load_expenses_data():
    conn = get_conn()
    if not conn:
        return None
    try:
        expenses = pd.read_sql(
            "SELECT * FROM expenses ORDER BY month DESC, amount DESC", conn
        )
        conn.close()
        expenses["month"] = pd.to_datetime(expenses["month"]).dt.date
        return expenses
    except Exception:
        conn.close()
        return None


# ── Navigation ─────────────────────────────────────────────────────────────────

page = st.sidebar.selectbox(
    "Navigation",
    ["Sales", "Payroll", "Expenses", "Inventory", "P&L"],
    index=0,
)

st.sidebar.divider()

# ══════════════════════════════════════════════════════════════════════════════
# PAGE: SALES
# ══════════════════════════════════════════════════════════════════════════════

if page == "Sales":
    st.title("Lavista Restaurant — Sales Dashboard")

    txn, items, runs = load_sales_data()

    if txn is None or txn.empty:
        st.warning("No data yet — make sure DATABASE_URL is set and ingest.py has run.")
        st.stop()

    with st.sidebar:
        st.header("Filters")
        min_date = txn["date"].min()
        max_date = txn["date"].max()
        date_range = st.date_input("Date range", value=(min_date, max_date),
                                   min_value=min_date, max_value=max_date)
        all_servers = sorted(txn["server"].dropna().unique().tolist())
        selected_servers = st.multiselect("Servers", options=all_servers, default=all_servers)
        all_methods = sorted(txn["payment_method"].dropna().unique().tolist())
        selected_methods = st.multiselect("Payment methods", options=all_methods, default=all_methods)
        st.divider()
        st.caption("Auto-refreshes every 30 s")

    if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
        start_date, end_date = date_range
    else:
        start_date = end_date = date_range

    filtered_txn = txn[
        (txn["date"] >= start_date) & (txn["date"] <= end_date)
        & (txn["server"].isin(selected_servers))
        & (txn["payment_method"].isin(selected_methods))
    ]
    filtered_items = items[items["transaction_id"].isin(filtered_txn["transaction_id"])]

    if filtered_txn.empty:
        st.warning("No transactions match the current filters.")
        st.stop()

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

    col_a, col_b = st.columns(2)
    with col_a:
        st.subheader("Top 10 Items by Revenue")
        top_items = (
            filtered_items.groupby("item_name")["subtotal"]
            .sum().sort_values(ascending=False).head(10).reset_index()
        )
        top_items.columns = ["Item", "Revenue (€)"]
        st.bar_chart(top_items.set_index("Item"))
    with col_b:
        st.subheader("Revenue by Category")
        by_cat = (
            filtered_items.groupby("category")["subtotal"]
            .sum().sort_values(ascending=False).reset_index()
        )
        by_cat.columns = ["Category", "Revenue (€)"]
        st.bar_chart(by_cat.set_index("Category"))

    st.divider()

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
            .sort_values("Revenue (€)", ascending=False).reset_index()
        )
        st.dataframe(by_server, use_container_width=True, hide_index=True)

    st.divider()

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
                "run_at": "Run at", "fetched": "Fetched", "cleaned": "Cleaned",
                "inserted": "Inserted", "skipped": "Skipped",
                "status": "Status", "error_msg": "Error",
            })
            st.dataframe(display_runs.drop(columns=["id"]), use_container_width=True, hide_index=True)

    st.divider()
    with st.expander("Raw Transactions"):
        st.dataframe(
            filtered_txn.drop(columns=["date", "hour"]).sort_values("timestamp", ascending=False),
            use_container_width=True, hide_index=True,
        )


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: PAYROLL
# ══════════════════════════════════════════════════════════════════════════════

elif page == "Payroll":
    st.title("Lavista Restaurant — Payroll")

    employees = load_payroll_data()

    if employees is None or employees.empty:
        st.warning("No employee data found. Run `python seed_employees.py` first.")
        st.stop()

    total_payroll = employees["monthly_salary"].sum()
    total_staff   = len(employees)
    avg_salary    = employees["monthly_salary"].mean()
    annual_cost   = total_payroll * 12

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Monthly Payroll", f"€{total_payroll:,.2f}")
    k2.metric("Total Staff",     f"{total_staff}")
    k3.metric("Average Salary",  f"€{avg_salary:,.2f}")
    k4.metric("Annual Cost",     f"€{annual_cost:,.2f}")

    st.divider()

    col_left, col_right = st.columns(2)
    with col_left:
        st.subheader("Monthly Cost by Role")
        by_role = (
            employees.groupby("role")["monthly_salary"]
            .sum().sort_values(ascending=False).reset_index()
        )
        by_role.columns = ["Role", "Monthly Cost (€)"]
        st.bar_chart(by_role.set_index("Role"))
    with col_right:
        st.subheader("Salary Distribution")
        by_employee = employees[["name", "monthly_salary"]].copy()
        by_employee.columns = ["Employee", "Monthly Salary (€)"]
        st.bar_chart(by_employee.set_index("Employee"))

    st.divider()

    st.subheader("Staff Overview")
    display_employees = employees[["name", "role", "monthly_salary", "start_date"]].copy()
    display_employees.columns = ["Name", "Role", "Monthly Salary (€)", "Start Date"]
    display_employees["Monthly Salary (€)"] = display_employees["Monthly Salary (€)"].apply(
        lambda x: f"€{x:,.2f}"
    )
    st.dataframe(display_employees, use_container_width=True, hide_index=True)

    st.divider()

    st.subheader("Payroll vs Revenue")
    txn, _, _ = load_sales_data()
    if txn is not None and not txn.empty:
        total_revenue  = float(txn["total"].sum())
        payroll_ratio  = (float(total_payroll) / total_revenue * 100) if total_revenue > 0 else 0
        col_a, col_b, col_c = st.columns(3)
        col_a.metric("Total Revenue (all time)", f"€{total_revenue:,.2f}")
        col_b.metric("Monthly Payroll",          f"€{total_payroll:,.2f}")
        col_c.metric("Payroll / Revenue",         f"{payroll_ratio:.1f}%")
        if payroll_ratio > 35:
            st.warning("Payroll is above 35% of revenue — industry benchmark is 25–35%.")
        else:
            st.success("Payroll is within the healthy industry benchmark of 25–35% of revenue.")
    else:
        st.info("No sales data available for comparison.")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: EXPENSES
# ══════════════════════════════════════════════════════════════════════════════

elif page == "Expenses":
    st.title("Lavista Restaurant — Expenses")

    expenses = load_expenses_data()

    if expenses is None or expenses.empty:
        st.warning("No expenses found. Run `python seed_expenses.py` first.")
        st.stop()

    # Latest month available
    latest_month   = expenses["month"].max()
    month_expenses = expenses[expenses["month"] == latest_month]

    total_expenses   = month_expenses["amount"].sum()
    top_category     = month_expenses.groupby("category")["amount"].sum().idxmax()
    top_cat_amount   = month_expenses.groupby("category")["amount"].sum().max()
    num_categories   = month_expenses["category"].nunique()

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Monthly Expenses",   f"€{total_expenses:,.2f}")
    k2.metric("Biggest Category",   top_category)
    k3.metric("Category Amount",    f"€{top_cat_amount:,.2f}")
    k4.metric("Categories",         f"{num_categories}")

    st.divider()

    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("Expenses by Category")
        by_cat = (
            month_expenses.groupby("category")["amount"]
            .sum().sort_values(ascending=False).reset_index()
        )
        by_cat.columns = ["Category", "Amount (€)"]
        st.bar_chart(by_cat.set_index("Category"))

    with col_right:
        st.subheader("Expense Breakdown")
        display_exp = month_expenses[["category", "description", "amount"]].copy()
        display_exp.columns = ["Category", "Description", "Amount (€)"]
        display_exp["Amount (€)"] = display_exp["Amount (€)"].apply(lambda x: f"€{x:,.2f}")
        st.dataframe(display_exp, use_container_width=True, hide_index=True)

    st.divider()

    # Expenses vs Revenue
    st.subheader("Expenses vs Revenue")
    txn, _, _ = load_sales_data()
    if txn is not None and not txn.empty:
        total_revenue   = float(txn["total"].sum())
        expenses_ratio  = (float(total_expenses) / total_revenue * 100) if total_revenue > 0 else 0

        col_a, col_b, col_c = st.columns(3)
        col_a.metric("Total Revenue (all time)", f"€{total_revenue:,.2f}")
        col_b.metric("Monthly Expenses",         f"€{total_expenses:,.2f}")
        col_c.metric("Expenses / Revenue",        f"{expenses_ratio:.1f}%")

        if expenses_ratio > 30:
            st.warning("Expenses are above 30% of revenue — review costs.")
        else:
            st.success("Expenses are within a healthy range.")
    else:
        st.info("No sales data available for comparison.")

    # Month selector if multiple months exist
    all_months = sorted(expenses["month"].unique(), reverse=True)
    if len(all_months) > 1:
        st.divider()
        st.subheader("Historical Expenses")
        monthly_totals = (
            expenses.groupby("month")["amount"].sum().reset_index()
        )
        monthly_totals.columns = ["Month", "Total Expenses (€)"]
        st.bar_chart(monthly_totals.set_index("Month"))


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: INVENTORY (coming soon)
# ══════════════════════════════════════════════════════════════════════════════

elif page == "Inventory":
    st.title("Lavista Restaurant — Inventory")
    st.info("Inventory page coming soon.")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: P&L (coming soon)
# ══════════════════════════════════════════════════════════════════════════════

elif page == "P&L":
    st.title("Lavista Restaurant — Profit & Loss")
    st.info("P&L page coming soon. Complete Expenses and Inventory pages first.")