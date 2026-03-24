"""
Sova Bistrot Dashboard — Streamlit analytics on PostgreSQL.
Run with: streamlit run dashboard.py
"""

import os
from datetime import date
import pandas as pd
import psycopg2
import streamlit as st

DATABASE_URL = os.getenv("DATABASE_URL")

st.set_page_config(page_title="Sova Bistrot", layout="wide", page_icon="🦉")

OWL_SVG = """
<svg width="160" height="160" viewBox="0 0 680 320" xmlns="http://www.w3.org/2000/svg">
  <ellipse cx="340" cy="175" rx="58" ry="72" fill="#2c1e0f"/>
  <ellipse cx="340" cy="190" rx="36" ry="50" fill="#c8a87a"/>
  <path d="M322 158 Q340 162 358 158" stroke="#8b6914" stroke-width="1.2" fill="none" opacity="0.6"/>
  <path d="M318 168 Q340 173 362 168" stroke="#8b6914" stroke-width="1.2" fill="none" opacity="0.6"/>
  <path d="M316 178 Q340 184 364 178" stroke="#8b6914" stroke-width="1.2" fill="none" opacity="0.5"/>
  <path d="M316 188 Q340 194 364 188" stroke="#8b6914" stroke-width="1.2" fill="none" opacity="0.5"/>
  <path d="M318 198 Q340 204 362 198" stroke="#8b6914" stroke-width="1.2" fill="none" opacity="0.4"/>
  <ellipse cx="340" cy="118" rx="46" ry="44" fill="#2c1e0f"/>
  <polygon points="310,85 302,58 322,78" fill="#2c1e0f"/>
  <polygon points="370,85 378,58 358,78" fill="#2c1e0f"/>
  <polygon points="310,85 306,65 318,80" fill="#3d2a14"/>
  <polygon points="370,85 374,65 362,80" fill="#3d2a14"/>
  <ellipse cx="290" cy="188" rx="28" ry="55" fill="#1e130a" transform="rotate(-12 290 188)"/>
  <ellipse cx="390" cy="188" rx="28" ry="55" fill="#1e130a" transform="rotate(12 390 188)"/>
  <path d="M270 165 Q282 175 275 190" stroke="#3d2a14" stroke-width="1" fill="none"/>
  <path d="M265 178 Q278 185 272 200" stroke="#3d2a14" stroke-width="1" fill="none"/>
  <path d="M410 165 Q398 175 405 190" stroke="#3d2a14" stroke-width="1" fill="none"/>
  <path d="M415 178 Q402 185 408 200" stroke="#3d2a14" stroke-width="1" fill="none"/>
  <circle cx="322" cy="116" r="17" fill="#e8b84b"/>
  <circle cx="358" cy="116" r="17" fill="#e8b84b"/>
  <circle cx="322" cy="116" r="12" fill="#1a0f00"/>
  <circle cx="358" cy="116" r="12" fill="#1a0f00"/>
  <circle cx="326" cy="112" r="4" fill="#ffffff"/>
  <circle cx="362" cy="112" r="4" fill="#ffffff"/>
  <circle cx="322" cy="116" r="17" fill="none" stroke="#8b6914" stroke-width="1.5"/>
  <circle cx="358" cy="116" r="17" fill="none" stroke="#8b6914" stroke-width="1.5"/>
  <polygon points="340,126 330,134 350,134" fill="#e8b84b"/>
  <polygon points="340,130 333,135 347,135" fill="#c8940a"/>
  <line x1="325" y1="242" x2="310" y2="258" stroke="#8b6914" stroke-width="3" stroke-linecap="round"/>
  <line x1="325" y1="242" x2="318" y2="260" stroke="#8b6914" stroke-width="3" stroke-linecap="round"/>
  <line x1="325" y1="242" x2="326" y2="261" stroke="#8b6914" stroke-width="3" stroke-linecap="round"/>
  <line x1="355" y1="242" x2="370" y2="258" stroke="#8b6914" stroke-width="3" stroke-linecap="round"/>
  <line x1="355" y1="242" x2="362" y2="260" stroke="#8b6914" stroke-width="3" stroke-linecap="round"/>
  <line x1="355" y1="242" x2="354" y2="261" stroke="#8b6914" stroke-width="3" stroke-linecap="round"/>
</svg>
"""

EXPENSE_CATEGORIES = [
    "Rent", "Utilities", "Supplies", "Software",
    "Professional", "Insurance", "Marketing", "Maintenance", "Other"
]

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
    txn["month"]     = txn["timestamp"].dt.to_period("M")
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


@st.cache_data(ttl=10)
def load_expenses_data():
    conn = get_conn()
    if not conn:
        return None
    try:
        expenses = pd.read_sql("SELECT * FROM expenses ORDER BY month DESC, amount DESC", conn)
        conn.close()
        expenses["month"] = pd.to_datetime(expenses["month"]).dt.date
        return expenses
    except Exception:
        conn.close()
        return None


@st.cache_data(ttl=10)
def load_supplier_data():
    conn = get_conn()
    if not conn:
        return None
    try:
        deliveries = pd.read_sql(
            "SELECT * FROM supplier_deliveries ORDER BY delivery_date DESC", conn
        )
        conn.close()
        if not deliveries.empty:
            deliveries["delivery_date"] = pd.to_datetime(deliveries["delivery_date"]).dt.date
            deliveries["month"]         = pd.to_datetime(deliveries["delivery_date"]).dt.to_period("M")
        return deliveries
    except Exception:
        conn.close()
        return None


# ── Write operations ───────────────────────────────────────────────────────────

def add_expense(category, description, amount, month):
    conn = get_conn()
    if not conn:
        return False, "Could not connect to database."
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO expenses (category, description, amount, frequency, month)
                VALUES (%s, %s, %s, 'monthly', %s)
                ON CONFLICT (description, month) DO UPDATE SET
                    category = EXCLUDED.category, amount = EXCLUDED.amount
            """, (category, description, amount, month))
        conn.commit()
        conn.close()
        return True, f"Expense '{description}' saved for {month.strftime('%B %Y')}."
    except Exception as e:
        conn.close()
        return False, str(e)


def delete_expense(expense_id):
    conn = get_conn()
    if not conn:
        return False, "Could not connect to database."
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM expenses WHERE id = %s", (expense_id,))
        conn.commit()
        conn.close()
        return True, "Expense deleted."
    except Exception as e:
        conn.close()
        return False, str(e)


def add_delivery(supplier_name, delivery_date, amount, description):
    conn = get_conn()
    if not conn:
        return False, "Could not connect to database."
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO supplier_deliveries (supplier_name, delivery_date, amount, description)
                VALUES (%s, %s, %s, %s)
            """, (supplier_name.strip(), delivery_date, amount, description.strip() or None))
        conn.commit()
        conn.close()
        return True, f"Delivery from '{supplier_name}' on {delivery_date} saved."
    except Exception as e:
        conn.close()
        return False, str(e)


def toggle_paid(delivery_id, paid):
    conn = get_conn()
    if not conn:
        return False, "Could not connect to database."
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE supplier_deliveries SET paid = %s WHERE id = %s", (paid, delivery_id))
        conn.commit()
        conn.close()
        return True, "Updated."
    except Exception as e:
        conn.close()
        return False, str(e)


def delete_delivery(delivery_id):
    conn = get_conn()
    if not conn:
        return False, "Could not connect to database."
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM supplier_deliveries WHERE id = %s", (delivery_id,))
        conn.commit()
        conn.close()
        return True, "Delivery deleted."
    except Exception as e:
        conn.close()
        return False, str(e)


# ── Navigation ─────────────────────────────────────────────────────────────────

page = st.sidebar.selectbox(
    "Navigation",
    ["Home", "Sales", "Payroll", "Expenses", "Suppliers", "Inventory", "P&L"],
    index=0,
)

st.sidebar.divider()
st.sidebar.markdown(OWL_SVG, unsafe_allow_html=True)
st.sidebar.markdown(
    "<div style='text-align:center; font-size:11px; color:#8b6914; letter-spacing:3px; margin-top:-8px;'>SOVA BISTROT</div>",
    unsafe_allow_html=True
)

# ══════════════════════════════════════════════════════════════════════════════
# PAGE: HOME
# ══════════════════════════════════════════════════════════════════════════════

if page == "Home":
    col_logo, col_title = st.columns([1, 3])
    with col_logo:
        st.markdown(OWL_SVG, unsafe_allow_html=True)
    with col_title:
        st.markdown("""
            <div style='padding-top: 20px;'>
                <div style='font-size: 42px; font-weight: 700; letter-spacing: 2px;'>SOVA</div>
                <div style='font-size: 16px; letter-spacing: 6px; color: #8b6914; margin-top: -8px;'>BISTROT</div>
                <div style='font-size: 15px; color: gray; margin-top: 12px;'>Business Intelligence Dashboard</div>
            </div>""", unsafe_allow_html=True)

    st.divider()

    txn, items, runs = load_sales_data()
    employees        = load_payroll_data()
    expenses         = load_expenses_data()
    deliveries       = load_supplier_data()

    st.subheader("Live Overview")
    k1, k2, k3, k4, k5 = st.columns(5)

    if txn is not None and not txn.empty:
        k1.metric("Total Revenue", f"€{txn['total'].sum():,.2f}")
        k2.metric("Transactions",  f"{len(txn):,}")
        k3.metric("Avg Check",     f"€{txn['total'].mean():.2f}")
    else:
        k1.metric("Total Revenue", "—")
        k2.metric("Transactions",  "—")
        k3.metric("Avg Check",     "—")

    if employees is not None and not employees.empty:
        k4.metric("Monthly Payroll", f"€{employees['monthly_salary'].sum():,.2f}")
    else:
        k4.metric("Monthly Payroll", "—")

    if deliveries is not None and not deliveries.empty:
        total_outstanding = float(deliveries[deliveries["paid"] == False]["amount"].sum())
        k5.metric("Supplier Balance", f"€{total_outstanding:,.2f}", help="Total unpaid supplier deliveries")
    else:
        k5.metric("Supplier Balance", "—")

    st.divider()
    st.subheader("Dashboard Pages")

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("#### 📊 Sales")
        st.markdown("Revenue by day and hour, top selling items, category breakdown, server performance.")
    with c2:
        st.markdown("#### 👥 Payroll")
        st.markdown("Staff overview, salary distribution by role, monthly payroll vs revenue.")
    with c3:
        st.markdown("#### 🧾 Expenses")
        st.markdown("Monthly fixed costs by category, add/edit/delete expenses from the dashboard.")

    c4, c5, c6 = st.columns(3)
    with c4:
        st.markdown("#### 🚚 Suppliers")
        st.markdown("Delivery log, monthly balance per supplier, carried over unpaid amounts, historical pivot table.")
    with c5:
        st.markdown("#### 📈 P&L")
        st.markdown("Full profit & loss statement — gross profit, net margin, health checks.")
    with c6:
        st.markdown("#### 📦 Inventory")
        st.markdown("Coming soon — stock levels, low stock alerts, waste tracking.")

    st.divider()
    st.subheader("Pipeline Status")
    if runs is not None and not runs.empty:
        last_run = runs.iloc[0]
        if last_run["status"] == "success":
            st.success(f"Last ingestion: {last_run['run_at']}  |  Inserted {last_run['inserted']} new rows  |  DB total: {len(txn):,} transactions")
        else:
            st.error(f"Last ingestion FAILED: {last_run['run_at']}  |  {last_run['error_msg']}")
    else:
        st.info("No pipeline runs recorded yet.")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: SALES
# ══════════════════════════════════════════════════════════════════════════════

elif page == "Sales":
    st.title("Sova Bistrot — Sales")
    txn, items, runs = load_sales_data()

    if txn is None or txn.empty:
        st.warning("No data yet.")
        st.stop()

    all_months   = sorted(txn["month"].unique(), reverse=True)
    col_month, _ = st.columns([2, 4])
    with col_month:
        selected_month = st.selectbox("Viewing month", options=all_months,
                                      format_func=lambda m: m.strftime("%B %Y"))

    month_txn   = txn[txn["month"] == selected_month]
    month_items = items[items["transaction_id"].isin(month_txn["transaction_id"])]

    with st.sidebar:
        st.header("Filters")
        all_servers = sorted(month_txn["server"].dropna().unique().tolist())
        selected_servers = st.multiselect("Servers", options=all_servers, default=all_servers)
        all_methods = sorted(month_txn["payment_method"].dropna().unique().tolist())
        selected_methods = st.multiselect("Payment methods", options=all_methods, default=all_methods)
        st.divider()
        st.caption("Auto-refreshes every 30 s")

    filtered_txn   = month_txn[month_txn["server"].isin(selected_servers) & month_txn["payment_method"].isin(selected_methods)]
    filtered_items = month_items[month_items["transaction_id"].isin(filtered_txn["transaction_id"])]

    if filtered_txn.empty:
        st.warning("No transactions match the current filters.")
        st.stop()

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Total Revenue", f"€{filtered_txn['total'].sum():,.2f}")
    k2.metric("Transactions",  f"{len(filtered_txn):,}")
    k3.metric("Avg Check",     f"€{filtered_txn['total'].mean():.2f}")
    k4.metric("Top Server",    filtered_txn["server"].value_counts().idxmax())

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
        top_items = filtered_items.groupby("item_name")["subtotal"].sum().sort_values(ascending=False).head(10).reset_index()
        top_items.columns = ["Item", "Revenue (€)"]
        st.bar_chart(top_items.set_index("Item"))
    with col_b:
        st.subheader("Revenue by Category")
        by_cat = filtered_items.groupby("category")["subtotal"].sum().sort_values(ascending=False).reset_index()
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
        by_server = (filtered_txn.groupby("server")["total"]
            .agg(["sum", "count", "mean"])
            .rename(columns={"sum": "Revenue (€)", "count": "Transactions", "mean": "Avg Check (€)"})
            .sort_values("Revenue (€)", ascending=False).reset_index())
        st.dataframe(by_server, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Pipeline Health")
    if not runs.empty:
        last_run = runs.iloc[0]
        if last_run["status"] == "success":
            st.success(f"Last run: {last_run['run_at']}  |  Fetched {last_run['fetched']}  →  Inserted {last_run['inserted']}  |  Skipped {last_run['skipped']} duplicates")
        else:
            st.error(f"Last run FAILED: {last_run['run_at']}  |  {last_run['error_msg']}")
        with st.expander("Recent pipeline runs"):
            st.dataframe(runs.rename(columns={"run_at": "Run at", "fetched": "Fetched", "cleaned": "Cleaned", "inserted": "Inserted", "skipped": "Skipped", "status": "Status", "error_msg": "Error"}).drop(columns=["id"]), use_container_width=True, hide_index=True)

    st.divider()
    with st.expander("Raw Transactions"):
        st.dataframe(filtered_txn.drop(columns=["date", "hour", "month"]).sort_values("timestamp", ascending=False), use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: PAYROLL
# ══════════════════════════════════════════════════════════════════════════════

elif page == "Payroll":
    st.title("Sova Bistrot — Payroll")
    employees = load_payroll_data()
    txn, _, _ = load_sales_data()

    if employees is None or employees.empty:
        st.warning("No employee data found. Run `python seed_employees.py` first.")
        st.stop()

    if txn is not None and not txn.empty:
        all_months   = sorted(txn["month"].unique(), reverse=True)
        col_month, _ = st.columns([2, 4])
        with col_month:
            selected_month = st.selectbox("Viewing month", options=all_months, format_func=lambda m: m.strftime("%B %Y"))
        month_revenue = float(txn[txn["month"] == selected_month]["total"].sum())
    else:
        selected_month = None
        month_revenue  = 0.0

    total_payroll = employees["monthly_salary"].sum()
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Monthly Payroll", f"€{total_payroll:,.2f}")
    k2.metric("Total Staff",     f"{len(employees)}")
    k3.metric("Average Salary",  f"€{employees['monthly_salary'].mean():,.2f}")
    k4.metric("Annual Cost",     f"€{total_payroll * 12:,.2f}")

    st.divider()

    col_left, col_right = st.columns(2)
    with col_left:
        st.subheader("Monthly Cost by Role")
        by_role = employees.groupby("role")["monthly_salary"].sum().sort_values(ascending=False).reset_index()
        by_role.columns = ["Role", "Monthly Cost (€)"]
        st.bar_chart(by_role.set_index("Role"))
    with col_right:
        st.subheader("Salary Distribution")
        by_emp = employees[["name", "monthly_salary"]].copy()
        by_emp.columns = ["Employee", "Monthly Salary (€)"]
        st.bar_chart(by_emp.set_index("Employee"))

    st.divider()
    st.subheader("Staff Overview")
    disp = employees[["name", "role", "monthly_salary", "start_date"]].copy()
    disp.columns = ["Name", "Role", "Monthly Salary (€)", "Start Date"]
    disp["Monthly Salary (€)"] = disp["Monthly Salary (€)"].apply(lambda x: f"€{x:,.2f}")
    st.dataframe(disp, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader(f"Payroll vs Revenue — {selected_month.strftime('%B %Y') if selected_month else ''}")
    if month_revenue > 0:
        payroll_ratio = float(total_payroll) / month_revenue * 100
        col_a, col_b, col_c = st.columns(3)
        col_a.metric(f"Revenue ({selected_month.strftime('%B %Y')})", f"€{month_revenue:,.2f}")
        col_b.metric("Monthly Payroll",   f"€{total_payroll:,.2f}")
        col_c.metric("Payroll / Revenue", f"{payroll_ratio:.1f}%")
        if payroll_ratio > 35:
            st.warning("Payroll is above 35% of revenue — industry benchmark is 25–35%.")
        else:
            st.success("Payroll is within the healthy industry benchmark of 25–35% of revenue.")
    else:
        st.info("No sales data for the selected month.")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: EXPENSES
# ══════════════════════════════════════════════════════════════════════════════

elif page == "Expenses":
    st.title("Sova Bistrot — Expenses")
    expenses = load_expenses_data()

    if expenses is None or expenses.empty:
        st.warning("No expenses found. Add your first expense below.")
    else:
        all_months     = sorted(expenses["month"].unique(), reverse=True)
        selected_month = st.selectbox("Viewing month", options=all_months, format_func=lambda d: d.strftime("%B %Y"))
        month_expenses = expenses[expenses["month"] == selected_month]

        if not month_expenses.empty:
            total_expenses = month_expenses["amount"].sum()
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Monthly Expenses",  f"€{total_expenses:,.2f}")
            k2.metric("Biggest Category",  month_expenses.groupby("category")["amount"].sum().idxmax())
            k3.metric("Category Amount",   f"€{month_expenses.groupby('category')['amount'].sum().max():,.2f}")
            k4.metric("Categories",        f"{month_expenses['category'].nunique()}")

            st.divider()
            col_left, col_right = st.columns(2)
            with col_left:
                st.subheader("Expenses by Category")
                by_cat = month_expenses.groupby("category")["amount"].sum().sort_values(ascending=False).reset_index()
                by_cat.columns = ["Category", "Amount (€)"]
                st.bar_chart(by_cat.set_index("Category"))
            with col_right:
                st.subheader("Expense Breakdown")
                disp = month_expenses[["id", "category", "description", "amount"]].copy()
                disp["amount"] = disp["amount"].apply(lambda x: f"€{x:,.2f}")
                disp.columns = ["ID", "Category", "Description", "Amount (€)"]
                st.dataframe(disp, use_container_width=True, hide_index=True)

            st.divider()
            st.subheader("Expenses vs Revenue")
            txn, _, _ = load_sales_data()
            if txn is not None and not txn.empty:
                sel_period    = pd.Period(selected_month.strftime("%Y-%m"), "M")
                month_revenue = float(txn[txn["month"] == sel_period]["total"].sum())
                if month_revenue > 0:
                    ratio = float(total_expenses) / month_revenue * 100
                    col_a, col_b, col_c = st.columns(3)
                    col_a.metric(f"Revenue ({selected_month.strftime('%B %Y')})", f"€{month_revenue:,.2f}")
                    col_b.metric("Monthly Expenses",   f"€{total_expenses:,.2f}")
                    col_c.metric("Expenses / Revenue", f"{ratio:.1f}%")
                    if ratio > 30:
                        st.warning("Expenses are above 30% of revenue — review costs.")
                    else:
                        st.success("Expenses are within a healthy range.")

            if len(all_months) > 1:
                st.divider()
                st.subheader("Historical Expenses")
                monthly_totals = expenses.groupby("month")["amount"].sum().reset_index()
                monthly_totals["Month"] = pd.to_datetime(monthly_totals["month"]).dt.strftime("%B %Y")
                monthly_totals = monthly_totals.rename(columns={"amount": "Total Expenses (€)"})
                st.bar_chart(monthly_totals.set_index("Month"))
                
    st.divider()
    st.subheader("Manage Expenses")
    tab_add, tab_delete = st.tabs(["Add / Update", "Delete"])

    with tab_add:
        col1, col2 = st.columns(2)
        with col1:
            form_month       = st.date_input("Month", value=date.today().replace(day=1))
            form_category    = st.selectbox("Category", options=EXPENSE_CATEGORIES)
            form_description = st.text_input("Description", placeholder="e.g. Electricity bill March")
        with col2:
            form_amount = st.number_input("Amount (€)", min_value=0.0, step=10.0, format="%.2f")
            st.markdown("<br><br>", unsafe_allow_html=True)
            submit = st.button("Save Expense", type="primary", use_container_width=True)

        if submit:
            if not form_description.strip():
                st.error("Please enter a description.")
            elif form_amount <= 0:
                st.error("Amount must be greater than zero.")
            else:
                success, message = add_expense(form_category, form_description.strip(), form_amount, form_month.replace(day=1))
                if success:
                    st.success(message)
                    st.cache_data.clear()
                    st.rerun()
                else:
                    st.error(f"Error: {message}")

    with tab_delete:
        expenses_fresh = load_expenses_data()
        if expenses_fresh is None or expenses_fresh.empty:
            st.info("No expenses to delete.")
        else:
            options = {f"[{r['id']}] {r['description']} — {r['month'].strftime('%B %Y')} — €{r['amount']:,.2f}": r["id"] for _, r in expenses_fresh.iterrows()}
            selected_label = st.selectbox("Select expense to delete", options=list(options.keys()))
            if st.button("Delete Expense", type="primary", use_container_width=True):
                success, message = delete_expense(options[selected_label])
                if success:
                    st.success(message)
                    st.cache_data.clear()
                    st.rerun()
                else:
                    st.error(f"Error: {message}")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: SUPPLIERS
# ══════════════════════════════════════════════════════════════════════════════

elif page == "Suppliers":
    st.title("Sova Bistrot — Suppliers")

    deliveries = load_supplier_data()

    if deliveries is not None and not deliveries.empty:

        # ── Month selector ─────────────────────────────────────────────────────
        all_months     = sorted(deliveries["month"].unique(), reverse=True)
        col_month, _   = st.columns([2, 4])
        with col_month:
            selected_month = st.selectbox(
                "Viewing month", options=all_months,
                format_func=lambda m: m.strftime("%B %Y")
            )

        # Split deliveries: this month vs previous months
        month_del = deliveries[deliveries["month"] == selected_month]
        prev_del  = deliveries[deliveries["month"] < selected_month]

        # ── KPIs ───────────────────────────────────────────────────────────────
        total_ordered      = float(month_del["amount"].sum())
        total_paid_month   = float(month_del[month_del["paid"] == True]["amount"].sum())
        total_owed_month   = total_ordered - total_paid_month
        total_carried_over = float(prev_del[prev_del["paid"] == False]["amount"].sum()) if not prev_del.empty else 0.0
        total_outstanding  = total_owed_month + total_carried_over

        k1, k2, k3, k4, k5 = st.columns(5)
        k1.metric("This Month Ordered",  f"€{total_ordered:,.2f}")
        k2.metric("Paid This Month",     f"€{total_paid_month:,.2f}")
        k3.metric("This Month Owed",     f"€{total_owed_month:,.2f}")
        k4.metric("Carried Over",        f"€{total_carried_over:,.2f}",
                  help="Unpaid amounts from previous months")
        k5.metric("Total Outstanding",   f"€{total_outstanding:,.2f}",
                  help="This month owed + carried over from previous months")

        st.divider()

        # ── Monthly summary per supplier ───────────────────────────────────────
        st.subheader(f"Supplier Balance — {selected_month.strftime('%B %Y')}")
        st.caption("Carried Over = unpaid deliveries from all previous months per supplier")

        all_suppliers = deliveries["supplier_name"].unique()
        summary_rows  = []

        for supplier in sorted(all_suppliers):
            sup_month = month_del[month_del["supplier_name"] == supplier]
            sup_prev  = prev_del[prev_del["supplier_name"] == supplier] if not prev_del.empty else pd.DataFrame()

            this_ordered    = float(sup_month["amount"].sum())
            this_paid       = float(sup_month[sup_month["paid"] == True]["amount"].sum()) if not sup_month.empty else 0.0
            this_owed       = this_ordered - this_paid
            carried         = float(sup_prev[sup_prev["paid"] == False]["amount"].sum()) if not sup_prev.empty else 0.0
            total_owed_sup  = this_owed + carried
            deliveries_count= len(sup_month)

            if this_ordered > 0 or carried > 0:
                summary_rows.append({
                    "Supplier":            supplier,
                    "Deliveries":          deliveries_count,
                    "Monthly Total (€)":   f"€{this_ordered:,.2f}",
                    "Paid (€)":            f"€{this_paid:,.2f}",
                    "This Month Owed (€)": f"€{this_owed:,.2f}",
                    "Carried Over (€)":    f"€{carried:,.2f}",
                    "Total Outstanding (€)": f"€{total_owed_sup:,.2f}",
                })

        if summary_rows:
            summary_df = pd.DataFrame(summary_rows)
            st.dataframe(summary_df, use_container_width=True, hide_index=True)

        st.divider()

        # ── Delivery log with monthly running total ────────────────────────────
        st.subheader(f"Delivery Log — {selected_month.strftime('%B %Y')}")

        if month_del.empty:
            st.info("No deliveries recorded for this month.")
        else:
            # Show per supplier with running total
            for supplier in sorted(month_del["supplier_name"].unique()):
                sup_del = month_del[month_del["supplier_name"] == supplier].sort_values("delivery_date")
                sup_total = float(sup_del["amount"].sum())
                sup_paid  = float(sup_del[sup_del["paid"] == True]["amount"].sum())
                sup_owed  = sup_total - sup_paid

                with st.expander(
                    f"**{supplier}** — {len(sup_del)} deliveries — "
                    f"Monthly total: €{sup_total:,.2f} — "
                    f"Owed: €{sup_owed:,.2f}",
                    expanded=True
                ):
                    # Header row
                    h1, h2, h3, h4, h5 = st.columns([1.5, 3, 1.5, 1.2, 0.5])
                    h1.markdown("**Date**")
                    h2.markdown("**Description**")
                    h3.markdown("**Amount**")
                    h4.markdown("**Paid**")
                    h5.markdown("**Del**")

                    running_total = 0.0
                    for _, row in sup_del.iterrows():
                        running_total += float(row["amount"])
                        c1, c2, c3, c4, c5 = st.columns([1.5, 3, 1.5, 1.2, 0.5])
                        c1.write(str(row["delivery_date"]))
                        c2.write(row["description"] or "—")
                        c3.write(f"€{row['amount']:,.2f}")

                        paid_toggle = c4.checkbox(
                            "Paid", value=bool(row["paid"]),
                            key=f"paid_{row['id']}"
                        )
                        if paid_toggle != bool(row["paid"]):
                            success, _ = toggle_paid(row["id"], paid_toggle)
                            if success:
                                st.cache_data.clear()
                                st.rerun()

                        if c5.button("🗑", key=f"del_{row['id']}"):
                            success, message = delete_delivery(row["id"])
                            if success:
                                st.cache_data.clear()
                                st.rerun()
                            else:
                                st.error(message)

                    # Monthly total footer
                    st.markdown("---")
                    f1, f2, f3 = st.columns([4.5, 1.5, 1.2])
                    f1.markdown(f"**Monthly total for {supplier}**")
                    f2.markdown(f"**€{sup_total:,.2f}**")
                    f3.markdown(f"**Owed: €{sup_owed:,.2f}**")

        st.divider()

        # ── Historical pivot table ─────────────────────────────────────────────
        st.subheader("Supplier Expenses — All Months")
        st.caption("Total deliveries per supplier per month (€) — all time")

        pivot = deliveries.copy()
        pivot["month_label"] = pivot["month"].apply(lambda m: m.strftime("%b %Y"))
        pivot_table = pivot.pivot_table(
            index="supplier_name",
            columns="month_label",
            values="amount",
            aggfunc="sum",
            fill_value=0,
        )

        month_order = sorted(pivot["month"].unique())
        col_order   = [m.strftime("%b %Y") for m in month_order]
        pivot_table = pivot_table.reindex(columns=col_order, fill_value=0)
        pivot_table["Total"] = pivot_table.sum(axis=1)

        total_row      = pivot_table.sum(axis=0)
        total_row.name = "TOTAL"
        pivot_table    = pd.concat([pivot_table, total_row.to_frame().T])

        formatted = pivot_table.map(lambda x: f"€{x:,.2f}" if x > 0 else "—")
        formatted.index.name = "Supplier"
        st.dataframe(formatted, use_container_width=True)

    else:
        st.info("No deliveries recorded yet. Add your first delivery below.")

    st.divider()

    # ── Add delivery form ──────────────────────────────────────────────────────
    st.subheader("Add Delivery")

    col1, col2 = st.columns(2)
    with col1:
        sup_name   = st.text_input("Supplier name", placeholder="e.g. Metro Cash & Carry")
        del_date   = st.date_input("Delivery date", value=date.today())
    with col2:
        del_amount = st.number_input("Amount (€)", min_value=0.0, step=10.0, format="%.2f")
        del_desc   = st.text_input("Description (optional)", placeholder="e.g. Weekly produce order")

    if st.button("Save Delivery", type="primary"):
        if not sup_name.strip():
            st.error("Please enter a supplier name.")
        elif del_amount <= 0:
            st.error("Amount must be greater than zero.")
        else:
            success, message = add_delivery(sup_name, del_date, del_amount, del_desc)
            if success:
                st.success(message)
                st.cache_data.clear()
                st.rerun()
            else:
                st.error(f"Error: {message}")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: INVENTORY
# ══════════════════════════════════════════════════════════════════════════════

elif page == "Inventory":
    st.title("Sova Bistrot — Inventory")
    st.info("Inventory page coming soon.")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: P&L
# ══════════════════════════════════════════════════════════════════════════════

elif page == "P&L":
    st.title("Sova Bistrot — Profit & Loss")

    COGS_RATE = 0.30
    txn, _, _ = load_sales_data()
    employees = load_payroll_data()
    expenses  = load_expenses_data()

    if txn is None or txn.empty:
        st.warning("No sales data found.")
        st.stop()
    if employees is None or employees.empty:
        st.warning("No payroll data found. Run `python seed_employees.py` first.")
        st.stop()
    if expenses is None or expenses.empty:
        st.warning("No expenses data found. Add expenses in the Expenses page.")
        st.stop()

    sales_months   = set(txn["month"].unique())
    expense_months = set(pd.Period(d.strftime("%Y-%m"), "M") for d in expenses["month"].unique())
    common_months  = sorted(sales_months & expense_months, reverse=True)

    if not common_months:
        st.warning("No months have both sales and expenses data yet.")
        st.stop()

    col_month, _ = st.columns([2, 4])
    with col_month:
        selected_month = st.selectbox("P&L for month", options=common_months,
                                      format_func=lambda m: m.strftime("%B %Y"))

    month_txn          = txn[txn["month"] == selected_month]
    expense_date       = date(selected_month.year, selected_month.month, 1)
    month_expenses_df  = expenses[expenses["month"] == expense_date]

    gross_revenue      = float(month_txn["total"].sum())
    cogs               = gross_revenue * COGS_RATE
    gross_profit       = gross_revenue - cogs
    gross_margin       = (gross_profit / gross_revenue * 100) if gross_revenue > 0 else 0
    total_payroll      = float(employees["monthly_salary"].sum())
    total_expenses_amt = float(month_expenses_df["amount"].sum())
    total_opex         = total_payroll + total_expenses_amt
    net_profit         = gross_profit - total_opex
    net_margin         = (net_profit / gross_revenue * 100) if gross_revenue > 0 else 0

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Gross Revenue", f"€{gross_revenue:,.2f}")
    k2.metric("Gross Profit",  f"€{gross_profit:,.2f}")
    k3.metric("Net Profit",    f"€{net_profit:,.2f}",
              delta=f"{net_margin:.1f}% margin",
              delta_color="normal" if net_profit >= 0 else "inverse")
    k4.metric("Gross Margin",  f"{gross_margin:.1f}%")

    st.divider()

    col_left, col_right = st.columns([2, 1])

    with col_left:
        st.subheader("P&L Statement")

        def pl_row(label, amount, bold=False, indent=False, positive_is_good=True):
            prefix = "　　" if indent else ""
            color  = "green" if (amount >= 0 and positive_is_good) else "red"
            sign   = "+" if amount >= 0 else ""
            if bold:
                st.markdown(f"**{prefix}{label}** &nbsp;&nbsp;&nbsp; **:{color}[{sign}€{abs(amount):,.2f}]**")
            else:
                st.markdown(f"{prefix}{label} &nbsp;&nbsp;&nbsp; :{color}[{sign}€{abs(amount):,.2f}]")

        st.markdown("#### Revenue")
        pl_row("Total Sales", gross_revenue, bold=True)
        st.markdown("---")
        st.markdown("#### Cost of Goods Sold")
        pl_row("Food & Beverage Cost (est. 30%)", -cogs, indent=True, positive_is_good=False)
        st.caption("COGS estimated at industry standard 30%")
        pl_row("Gross Profit", gross_profit, bold=True)
        st.markdown(f"*Gross Margin: {gross_margin:.1f}%*")
        st.markdown("---")
        st.markdown("#### Operating Expenses")
        pl_row("Payroll", -total_payroll, indent=True, positive_is_good=False)
        for cat, amt in month_expenses_df.groupby("category")["amount"].sum().sort_values(ascending=False).items():
            pl_row(cat, -amt, indent=True, positive_is_good=False)
        pl_row("Total Operating Expenses", -total_opex, bold=True, positive_is_good=False)
        st.markdown("---")
        pl_row("Net Profit / Loss", net_profit, bold=True)
        st.markdown(f"*Net Margin: {net_margin:.1f}%*")

    with col_right:
        st.subheader("Cost Breakdown")
        breakdown = pd.DataFrame({"Category": ["COGS", "Payroll", "Expenses"], "Amount (€)": [cogs, total_payroll, total_expenses_amt]})
        st.bar_chart(breakdown.set_index("Category"))
        st.divider()
        st.subheader("Health Check")
        checks = [
            ("Gross margin > 60%",     gross_margin > 60),
            ("Payroll < 35% revenue",  (total_payroll / gross_revenue * 100) < 35 if gross_revenue > 0 else False),
            ("Expenses < 30% revenue", (total_expenses_amt / gross_revenue * 100) < 30 if gross_revenue > 0 else False),
            ("Net profit positive",    net_profit > 0),
        ]
        for check, passed in checks:
            st.markdown(f"{'✅' if passed else '❌'} {check}")

    st.divider()
    st.subheader("Summary")
    st.dataframe(pd.DataFrame([
        {"Line Item": "Gross Revenue",            "Amount (€)": f"€{gross_revenue:,.2f}"},
        {"Line Item": "Cost of Goods Sold (30%)", "Amount (€)": f"-€{cogs:,.2f}"},
        {"Line Item": "Gross Profit",             "Amount (€)": f"€{gross_profit:,.2f}"},
        {"Line Item": "Payroll",                  "Amount (€)": f"-€{total_payroll:,.2f}"},
        {"Line Item": "Operating Expenses",       "Amount (€)": f"-€{total_expenses_amt:,.2f}"},
        {"Line Item": "Net Profit / Loss",        "Amount (€)": f"€{net_profit:,.2f}"},
    ]), use_container_width=True, hide_index=True)