"""
Sova Bistrot Dashboard — Streamlit analytics on PostgreSQL.
Run with: streamlit run dashboard.py
"""

import os
from datetime import date, datetime, timedelta
import pandas as pd
import psycopg2
import streamlit as st
import os
import streamlit as st

st.set_page_config(page_title="Sova Bistrot", layout="wide", page_icon="🦉")


# Password protection
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "SET_PASSWORD_IN_RAILWAY")

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.title("🦉 Sova Bistrot — Dashboard Access")
    password_input = st.text_input("Enter password:", type="password")
    if st.button("Login"):
        if password_input == DASHBOARD_PASSWORD:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Incorrect password")
    st.stop()

# Rest of your dashboard code continues here...
DATABASE_URL = os.getenv("DATABASE_URL")







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
    try:
        txn   = pd.read_sql("SELECT * FROM transactions ORDER BY timestamp DESC", conn)
        items = pd.read_sql("SELECT * FROM transaction_items", conn)
        try:
            runs = pd.read_sql("SELECT * FROM pipeline_runs ORDER BY run_at DESC LIMIT 20", conn)
        except Exception:
            runs = pd.DataFrame()
        txn["timestamp"] = pd.to_datetime(txn["timestamp"], utc=True)
        txn["date"]      = txn["timestamp"].dt.date
        txn["hour"]      = txn["timestamp"].dt.hour
        txn["month"]     = txn["timestamp"].dt.to_period("M")
        return txn, items, runs
    except Exception:
        return None, None, None
    finally:
        conn.close()


@st.cache_data(ttl=60)
def load_payroll_data():
    conn = get_conn()
    if not conn:
        return None
    try:
        return pd.read_sql(
            "SELECT * FROM employees WHERE active = TRUE ORDER BY monthly_salary DESC", conn
        )
    except Exception:
        return None
    finally:
        conn.close()


@st.cache_data(ttl=10)
def load_expenses_data():
    conn = get_conn()
    if not conn:
        return None
    try:
        expenses = pd.read_sql("SELECT * FROM expenses ORDER BY month DESC, amount DESC", conn)
        expenses["month"] = pd.to_datetime(expenses["month"]).dt.date
        return expenses
    except Exception:
        return None
    finally:
        conn.close()


@st.cache_data(ttl=10)
def load_supplier_data():
    conn = get_conn()
    if not conn:
        return None
    try:
        deliveries = pd.read_sql(
            "SELECT * FROM supplier_deliveries ORDER BY delivery_date DESC", conn
        )
        if not deliveries.empty:
            deliveries["delivery_date"] = pd.to_datetime(deliveries["delivery_date"]).dt.date
            deliveries["month"]         = pd.to_datetime(deliveries["delivery_date"]).dt.to_period("M")
        return deliveries
    except Exception:
        return None
    finally:
        conn.close()


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
        return True, f"Expense '{description}' saved for {month.strftime('%B %Y')}."
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()


def delete_expense(expense_id):
    conn = get_conn()
    if not conn:
        return False, "Could not connect to database."
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM expenses WHERE id = %s", (expense_id,))
        conn.commit()
        return True, "Expense deleted."
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()


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
        return True, f"Delivery from '{supplier_name}' on {delivery_date} saved."
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()


def toggle_paid(delivery_id, paid):
    conn = get_conn()
    if not conn:
        return False, "Could not connect to database."
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE supplier_deliveries SET paid = %s WHERE id = %s",
                (paid, delivery_id)
            )
        conn.commit()
        return True, "Updated."
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()


def delete_delivery(delivery_id):
    conn = get_conn()
    if not conn:
        return False, "Could not connect to database."
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM supplier_deliveries WHERE id = %s", (delivery_id,))
        conn.commit()
        return True, "Delivery deleted."
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()

def delete_delivery_with_reversal(delivery_id):
    """
    Delete a delivery AND reverse its inventory movements.
    Used only from the Audit Log — for correcting mistakes.
    """
    conn = get_conn()
    if not conn:
        return False, "Could not connect to database."
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT item_id, quantity FROM inventory_movements
                WHERE source = 'delivery' AND source_id = %s AND movement_type = 'in'
            """, (delivery_id,))
            movements = cur.fetchall()

            for item_id, qty in movements:
                cur.execute("SELECT quantity FROM inventory_items WHERE id = %s", (item_id,))
                row = cur.fetchone()
                if not row:
                    continue
                new_qty = float(row[0]) - float(qty)

                if new_qty <= 0:
                    cur.execute("DELETE FROM inventory_movements WHERE item_id = %s", (item_id,))
                    cur.execute("DELETE FROM inventory_items WHERE id = %s", (item_id,))
                else:
                    cur.execute("""
                        UPDATE inventory_items
                        SET quantity   = %s,
                            updated_at = NOW()
                        WHERE id = %s
                    """, (new_qty, item_id))
                    cur.execute("""
                        INSERT INTO inventory_movements
                            (item_id, movement_type, quantity, source, source_id, note)
                        VALUES (%s, 'out', %s, 'manual', %s, %s)
                    """, (item_id, qty, delivery_id,
                          f"Reversal: delivery #{delivery_id} deleted from audit log"))

            cur.execute("DELETE FROM supplier_deliveries WHERE id = %s", (delivery_id,))

        conn.commit()
        return True, f"Delivery #{delivery_id} deleted and inventory reversed."
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()


def mark_all_paid(supplier_name):
    """Mark all unpaid deliveries for a supplier as paid."""
    conn = get_conn()
    if not conn:
        return False, "Could not connect to database."
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE supplier_deliveries SET paid = TRUE
                WHERE LOWER(TRIM(supplier_name)) = LOWER(TRIM(%s)) AND paid = FALSE
            """, (supplier_name,))
            count = cur.rowcount
        conn.commit()
        return True, f"Marked {count} deliveries as paid for {supplier_name}."
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()


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

    # Current and previous month
    if txn is not None and not txn.empty:
        all_months_home  = sorted(txn["month"].unique())
        curr_m           = all_months_home[-1]
        prev_m           = all_months_home[-2] if len(all_months_home) > 1 else None
        curr_m_txn       = txn[txn["month"] == curr_m]
        prev_m_txn       = txn[txn["month"] == prev_m] if prev_m else pd.DataFrame()

        curr_rev  = float(curr_m_txn["total"].sum())
        curr_cnt  = len(curr_m_txn)
        curr_avg  = float(curr_m_txn["total"].mean())
        prev_rev  = float(prev_m_txn["total"].sum())  if not prev_m_txn.empty else None
        prev_cnt  = len(prev_m_txn)                   if not prev_m_txn.empty else None
        prev_avg  = float(prev_m_txn["total"].mean()) if not prev_m_txn.empty else None

        def home_delta(curr, prev):
            if prev is None or prev == 0:
                return None
            return f"{((curr - prev) / prev * 100):+.1f}% vs {prev_m.strftime('%b')}"
    else:
        curr_rev = curr_cnt = curr_avg = None
        prev_rev = prev_cnt = prev_avg = None
        curr_m   = None

    k1, k2, k3, k4, k5 = st.columns(5)

    if curr_rev is not None:
        k1.metric(f"Revenue ({curr_m.strftime('%b %Y')})", f"€{curr_rev:,.2f}",
                  delta=home_delta(curr_rev, prev_rev), delta_color="normal")
        k2.metric("Transactions", f"{curr_cnt:,}",
                  delta=f"{curr_cnt - prev_cnt:+,} vs {prev_m.strftime('%b')}" if prev_cnt is not None else None,
                  delta_color="normal")
        k3.metric("Avg Check", f"€{curr_avg:.2f}",
                  delta=home_delta(curr_avg, prev_avg), delta_color="normal")
    else:
        k1.metric("Revenue", "—")
        k2.metric("Transactions", "—")
        k3.metric("Avg Check", "—")

    if employees is not None and not employees.empty:
        k4.metric("Monthly Payroll", f"€{employees['monthly_salary'].sum():,.2f}")
    else:
        k4.metric("Monthly Payroll", "—")

    if deliveries is not None and not deliveries.empty:
        total_outstanding = float(deliveries[deliveries["paid"] == False]["amount"].sum())
        k5.metric("Supplier Balance", f"€{total_outstanding:,.2f}",
                  help="Total unpaid supplier deliveries")
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
        st.markdown("Monthly fixed costs by category, stacked historical chart, add/edit/delete expenses from the dashboard.")

    c4, c5, c6 = st.columns(3)
    with c4:
        st.markdown("#### 🚚 Suppliers")
        st.markdown("Delivery log, monthly balance per supplier, carried over unpaid amounts.")
    with c5:
        st.markdown("#### 📈 P&L")
        st.markdown("Full P&L statement, health checks, road to profitability chart.")
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
    import plotly.express as px
    import plotly.graph_objects as go
 
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

        # Date range within the selected month
        month_min = month_txn["date"].min()
        month_max = month_txn["date"].max()
        date_range = st.date_input(
            "Date range",
            value=(month_min, month_max),
            min_value=month_min,
            max_value=month_max,
        )
        if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
            date_start, date_end = date_range
        else:
            date_start = date_end = date_range

        all_servers = sorted(month_txn["server"].dropna().unique().tolist())
        selected_servers = st.multiselect("Servers", options=all_servers, default=all_servers)
        all_methods = sorted(month_txn["payment_method"].dropna().unique().tolist())
        selected_methods = st.multiselect("Payment methods", options=all_methods, default=all_methods)
        st.divider()
        st.caption("Auto-refreshes every 30 s")
 
    filtered_txn   = month_txn[
            (month_txn["date"] >= date_start)
            & (month_txn["date"] <= date_end)
            & month_txn["server"].isin(selected_servers)
            & month_txn["payment_method"].isin(selected_methods)
    ]    
    filtered_items = month_items[month_items["transaction_id"].isin(filtered_txn["transaction_id"])]
 
    if filtered_txn.empty:
        st.warning("No transactions match the current filters.")
        st.stop()
 
    # ── KPIs with month-over-month deltas ─────────────────────────────────────
    all_months_sorted = sorted(txn["month"].unique())
    current_idx       = list(all_months_sorted).index(selected_month)
    prev_month        = all_months_sorted[current_idx - 1] if current_idx > 0 else None
    prev_txn          = txn[txn["month"] == prev_month] if prev_month is not None else pd.DataFrame()
 
    curr_revenue = float(filtered_txn["total"].sum())
    curr_txns    = len(filtered_txn)
    curr_avg     = float(filtered_txn["total"].mean())
    top_server   = filtered_txn["server"].value_counts().idxmax()
 
    prev_revenue = float(prev_txn["total"].sum())    if not prev_txn.empty else None
    prev_txns    = len(prev_txn)                     if not prev_txn.empty else None
    prev_avg     = float(prev_txn["total"].mean())   if not prev_txn.empty else None
 
    def pct_delta(curr, prev):
        if prev is None or prev == 0:
            return None
        return f"{((curr - prev) / prev * 100):+.1f}%"
 
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Total Revenue", f"€{curr_revenue:,.2f}",
              delta=pct_delta(curr_revenue, prev_revenue),
              delta_color="normal")
    k2.metric("Transactions",  f"{curr_txns:,}",
              delta=f"{curr_txns - prev_txns:+,} vs prev month" if prev_txns is not None else None,
              delta_color="normal")
    k3.metric("Avg Check",     f"€{curr_avg:.2f}",
              delta=pct_delta(curr_avg, prev_avg),
              delta_color="normal")
    k4.metric("Top Server",    top_server)
 
    st.divider()
 
    # ── Revenue by Day ─────────────────────────────────────────────────────────
    col_left, col_right = st.columns(2)
 
    with col_left:
        st.subheader("Revenue by Day")
        daily = filtered_txn.groupby("date")["total"].sum().reset_index()
        daily.columns = ["Date", "Revenue"]
        daily["Date"] = daily["Date"].astype(str)
        fig_daily = go.Figure(go.Bar(
            x=daily["Date"],
            y=daily["Revenue"],
            marker_color="#2c3e7a",
            hovertemplate="<b>%{x}</b><br>Revenue: €%{y:,.2f}<extra></extra>",
        ))
        fig_daily.update_layout(
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            font_color="#cccccc", xaxis_title=None, yaxis_title="€",
            margin=dict(t=20, b=20),
        )
        st.plotly_chart(fig_daily, use_container_width=True)
 
    with col_right:
        st.subheader("Transactions by Hour")
        hourly = filtered_txn.groupby("hour").size().reset_index(name="Count")
        fig_hourly = go.Figure(go.Bar(
            x=hourly["hour"],
            y=hourly["Count"],
            marker_color="#8b6914",
            hovertemplate="<b>%{x}:00</b><br>Transactions: %{y}<extra></extra>",
        ))
        fig_hourly.update_layout(
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            font_color="#cccccc", xaxis_title="Hour", yaxis_title="Transactions",
            margin=dict(t=20, b=20),
        )
        st.plotly_chart(fig_hourly, use_container_width=True)
 
    st.divider()
 
    # ── Items & Categories ─────────────────────────────────────────────────────
    col_a, col_b = st.columns(2)
 
    with col_a:
        st.subheader("Top 10 Items by Revenue")
        top_items = (filtered_items.groupby("item_name")["subtotal"]
                     .sum().sort_values(ascending=True).tail(10).reset_index())
        top_items.columns = ["Item", "Revenue"]
        fig_items = go.Figure(go.Bar(
            x=top_items["Revenue"],
            y=top_items["Item"],
            orientation="h",
            marker_color="#1a6b3a",
            hovertemplate="<b>%{y}</b><br>Revenue: €%{x:,.2f}<extra></extra>",
        ))
        fig_items.update_layout(
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            font_color="#cccccc", xaxis_title="€", yaxis_title=None,
            margin=dict(t=20, b=20),
        )
        st.plotly_chart(fig_items, use_container_width=True)
 
    with col_b:
        st.subheader("Revenue by Category")
        by_cat = (filtered_items.groupby("category")["subtotal"]
                  .sum().sort_values(ascending=False).reset_index())
        by_cat.columns = ["Category", "Revenue"]
        fig_cat = px.pie(
            by_cat, values="Revenue", names="Category",
            hole=0.4,
            color_discrete_sequence=["#2c3e7a", "#8b6914", "#1a6b3a", "#a32d2d", "#555555"],
        )
        fig_cat.update_traces(
            hovertemplate="<b>%{label}</b><br>€%{value:,.2f}<br>%{percent}<extra></extra>",
            textinfo="label+percent",
        )
        fig_cat.update_layout(
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            font_color="#cccccc", showlegend=False,
            margin=dict(t=20, b=20),
        )
        st.plotly_chart(fig_cat, use_container_width=True)
 
    st.divider()
 
    # ── Payment Methods & Server Performance ───────────────────────────────────
    col_p, col_s = st.columns(2)
 
    with col_p:
        st.subheader("Payment Methods")
        pm = filtered_txn["payment_method"].value_counts().reset_index()
        pm.columns = ["Method", "Count"]
        fig_pm = go.Figure(go.Bar(
            x=pm["Method"],
            y=pm["Count"],
            marker_color=["#2c3e7a", "#8b6914", "#1a6b3a"],
            hovertemplate="<b>%{x}</b><br>Count: %{y}<extra></extra>",
        ))
        fig_pm.update_layout(
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            font_color="#cccccc", xaxis_title=None, yaxis_title="Transactions",
            margin=dict(t=20, b=20),
        )
        st.plotly_chart(fig_pm, use_container_width=True)
 
    with col_s:
        st.subheader("Revenue by Server")
        by_server = (filtered_txn.groupby("server")["total"]
            .agg(["sum", "count", "mean"])
            .rename(columns={"sum": "Revenue", "count": "Transactions", "mean": "Avg Check"})
            .sort_values("Revenue", ascending=False).reset_index())
        fig_srv = go.Figure()
        fig_srv.add_trace(go.Bar(
            x=by_server["server"], y=by_server["Revenue"],
            name="Revenue (€)", marker_color="#2c3e7a",
            hovertemplate="<b>%{x}</b><br>Revenue: €%{y:,.2f}<extra></extra>",
        ))
        fig_srv.add_trace(go.Scatter(
            x=by_server["server"], y=by_server["Avg Check"],
            name="Avg Check (€)", mode="markers",
            marker=dict(color="#e8b84b", size=10),
            hovertemplate="<b>%{x}</b><br>Avg Check: €%{y:,.2f}<extra></extra>",
            yaxis="y2",
        ))
        fig_srv.update_layout(
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            font_color="#cccccc", xaxis_title=None,
            yaxis=dict(title="Revenue (€)"),
            yaxis2=dict(title="Avg Check (€)", overlaying="y", side="right"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            margin=dict(t=40, b=20),
        )
        st.plotly_chart(fig_srv, use_container_width=True)
 
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
                import plotly.express as px
                hist             = expenses.copy()
                hist["Month"]    = pd.to_datetime(hist["month"]).dt.strftime("%B %Y")
                hist["month_dt"] = pd.to_datetime(hist["month"])
                month_order      = hist.sort_values("month_dt")["Month"].unique().tolist()
                fig = px.bar(
                    hist,
                    x="Month",
                    y="amount",
                    color="category",
                    category_orders={"Month": month_order},
                    labels={"amount": "Amount (€)", "category": "Category"},
                )
                fig.update_layout(
                    barmode="stack",
                    legend_title="Category",
                    xaxis_title=None,
                    yaxis_title="Total Expenses (€)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    paper_bgcolor="rgba(0,0,0,0)",
                    font_color="#cccccc",
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                )
                fig.update_traces(hovertemplate="<b>%{x}</b><br>%{fullData.name}: €%{y:,.2f}<extra></extra>")
                st.plotly_chart(fig, use_container_width=True)

    st.divider()
    st.subheader("Manage Expenses")
    tab_add, tab_delete = st.tabs(["Add / Update", "Delete"])

    with tab_add:
        col1, col2 = st.columns(2)
        with col1:
            form_month        = st.date_input("Month", value=date.today().replace(day=1))
            form_category_sel = st.selectbox("Category", options=EXPENSE_CATEGORIES + ["+ New category"])
            if form_category_sel == "+ New category":
                form_category = st.text_input("New category name", placeholder="e.g. Equipment repair")
            else:
                form_category = form_category_sel
            form_description  = st.text_input("Description", placeholder="e.g. Electricity bill March")
        with col2:
            form_amount = st.number_input("Amount (€)", min_value=0.0, step=10.0, format="%.2f")
            st.markdown("<br><br>", unsafe_allow_html=True)
            submit = st.button("Save Expense", type="primary", use_container_width=True)

        if submit:
            if not form_category.strip():
                st.error("Please enter a category name.")
            elif not form_description.strip():
                st.error("Please enter a description.")
            elif form_amount <= 0:
                st.error("Amount must be greater than zero.")
            else:
                normalised_desc     = form_description.strip().title()
                normalised_category = form_category.strip().title()
                success, message    = add_expense(
                    normalised_category,
                    normalised_desc,
                    form_amount,
                    form_month.replace(day=1)
                )
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
# PAGE: SUPPLIERS (REDESIGNED — supplier-first, aging buckets, combined audit)
# ══════════════════════════════════════════════════════════════════════════════

elif page == "Suppliers":
    st.title("Sova Bistrot — Suppliers")

    deliveries = load_supplier_data()

    if deliveries is not None and not deliveries.empty:

        all_months     = sorted(deliveries["month"].unique(), reverse=True)
        col_month, _   = st.columns([2, 4])
        with col_month:
            selected_month = st.selectbox(
                "Viewing month", options=all_months,
                format_func=lambda m: m.strftime("%B %Y")
            )

        month_del = deliveries[deliveries["month"] == selected_month]
        prev_del  = deliveries[deliveries["month"] < selected_month]

        total_ordered      = float(month_del["amount"].sum())
        total_paid_month   = float(month_del[month_del["paid"] == True]["amount"].sum())
        total_owed_month   = total_ordered - total_paid_month
        total_carried_over = float(prev_del[prev_del["paid"] == False]["amount"].sum()) if not prev_del.empty else 0.0
        total_outstanding  = total_owed_month + total_carried_over

        k1, k2, k3, k4, k5 = st.columns(5)
        k1.metric("This Month Ordered",  f"€{total_ordered:,.2f}")
        k2.metric("Paid This Month",     f"€{total_paid_month:,.2f}")
        k3.metric("This Month Owed",     f"€{total_owed_month:,.2f}")
        k4.metric("Carried Over",        f"€{total_carried_over:,.2f}", help="Unpaid from previous months")
        k5.metric("Total Outstanding",   f"€{total_outstanding:,.2f}", help="This month + carried over")

        # Recent Ingestions
        st.divider()
        st.subheader("🕐 Recent Ingestions")
        st.caption("Last 20 deliveries added to the system, most recent first.")

        @st.cache_data(ttl=10)
        def load_recent_ingestions():
            conn = get_conn()
            if not conn:
                return None
            try:
                return pd.read_sql("""
                    SELECT
                        created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Copenhagen' AS ingested_at,
                        supplier_name,
                        delivery_date,
                        amount,
                        description,
                        paid
                    FROM supplier_deliveries
                    ORDER BY created_at DESC
                    LIMIT 20
                """, conn)
            except Exception:
                return None
            finally:
                conn.close()

        recent = load_recent_ingestions()

        if recent is not None and not recent.empty:
            recent["ingested_at"]  = pd.to_datetime(recent["ingested_at"]).dt.strftime("%Y-%m-%d %H:%M")
            recent["amount"]       = recent["amount"].apply(lambda x: f"€{x:,.2f}")
            recent["paid"]         = recent["paid"].apply(lambda x: "✅" if x else "—")
            recent["description"]  = recent["description"].fillna("—")
            recent.columns         = ["Saved At", "Supplier", "Invoice Date", "Amount", "Description", "Paid"]
            st.dataframe(recent, use_container_width=True, hide_index=True)
        else:
            st.info("No deliveries recorded yet.")

        # Supplier Balance
        st.divider()
        st.subheader(f"Supplier Balance — {selected_month.strftime('%B %Y')}")
        st.caption("Carried Over = unpaid deliveries from all previous months per supplier")

        all_suppliers = deliveries["supplier_name"].unique()
        summary_rows  = []

        for supplier in sorted(all_suppliers):
            sup_month = month_del[month_del["supplier_name"] == supplier]
            sup_prev  = prev_del[prev_del["supplier_name"] == supplier] if not prev_del.empty else pd.DataFrame()

            this_ordered = float(sup_month["amount"].sum())
            this_paid    = float(sup_month[sup_month["paid"] == True]["amount"].sum()) if not sup_month.empty else 0.0
            this_owed    = this_ordered - this_paid
            carried      = float(sup_prev[sup_prev["paid"] == False]["amount"].sum()) if not sup_prev.empty else 0.0
            total_owed_s = this_owed + carried

            if this_ordered > 0 or carried > 0:
                summary_rows.append({
                    "Supplier":              supplier,
                    "Deliveries":            len(sup_month),
                    "Monthly Total (€)":     f"€{this_ordered:,.2f}",
                    "Paid (€)":              f"€{this_paid:,.2f}",
                    "This Month Owed (€)":   f"€{this_owed:,.2f}",
                    "Carried Over (€)":      f"€{carried:,.2f}",
                    "Total Outstanding (€)": f"€{total_owed_s:,.2f}",
                })

        if summary_rows:
            st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)

        # Delivery Log
        st.divider()
        st.subheader(f"Delivery Log — {selected_month.strftime('%B %Y')}")

        if month_del.empty:
            st.info("No deliveries recorded for this month.")
        else:
            # Track which expanders are open so they stay open after a rerun
            if "supplier_expander_state" not in st.session_state:
                st.session_state.supplier_expander_state = {}

            for supplier in sorted(month_del["supplier_name"].unique()):
                sup_del   = month_del[month_del["supplier_name"] == supplier].sort_values("delivery_date")
                sup_total = float(sup_del["amount"].sum())
                sup_paid  = float(sup_del[sup_del["paid"] == True]["amount"].sum())
                sup_owed  = sup_total - sup_paid

                exp_key     = f"exp_{supplier}"
                is_expanded = st.session_state.supplier_expander_state.get(exp_key, True)

                with st.expander(
                    f"**{supplier}** — {len(sup_del)} deliveries — "
                    f"Monthly total: €{sup_total:,.2f} — Owed: €{sup_owed:,.2f}",
                    expanded=is_expanded
                ):
                    h1, h2, h3, h4, h5 = st.columns([1.5, 3, 1.5, 1.2, 0.5])
                    h1.markdown("**Date**")
                    h2.markdown("**Description**")
                    h3.markdown("**Amount**")
                    h4.markdown("**Paid**")
                    h5.markdown("**Del**")

                    for _, row in sup_del.iterrows():
                        c1, c2, c3, c4, c5 = st.columns([1.5, 3, 1.5, 1.2, 0.5])
                        c1.write(str(row["delivery_date"]))
                        c2.write(row["description"] or "—")
                        c3.write(f"€{row['amount']:,.2f}")

                        paid_toggle = c4.checkbox("Paid", value=bool(row["paid"]), key=f"paid_{row['id']}")
                        if paid_toggle != bool(row["paid"]):
                            success, _ = toggle_paid(row["id"], paid_toggle)
                            if success:
                                st.session_state.supplier_expander_state[exp_key] = True
                                st.cache_data.clear()
                                st.rerun()

                        if c5.button("🗑", key=f"del_{row['id']}"):
                            success, message = delete_delivery(row["id"])
                            if success:
                                st.session_state.supplier_expander_state[exp_key] = True
                                st.cache_data.clear()
                                st.rerun()
                            else:
                                st.error(message)

                    st.markdown("---")
                    f1, f2, f3 = st.columns([4.5, 1.5, 1.2])
                    f1.markdown(f"**Monthly total for {supplier}**")
                    f2.markdown(f"**€{sup_total:,.2f}**")
                    f3.markdown(f"**Owed: €{sup_owed:,.2f}**")

        # Historical Pivot
        st.divider()
        st.subheader("Supplier Expenses — All Months")
        st.caption("Total deliveries per supplier per month (€)")

        pivot                = deliveries.copy()
        pivot["month_label"] = pivot["month"].apply(lambda m: m.strftime("%b %Y"))
        pivot_table          = pivot.pivot_table(index="supplier_name", columns="month_label",
                                                 values="amount", aggfunc="sum", fill_value=0)
        month_order          = sorted(pivot["month"].unique())
        col_order            = [m.strftime("%b %Y") for m in month_order]
        pivot_table          = pivot_table.reindex(columns=col_order, fill_value=0)
        pivot_table["Total"] = pivot_table.sum(axis=1)
        total_row            = pivot_table.sum(axis=0)
        total_row.name       = "TOTAL"
        pivot_table          = pd.concat([pivot_table, total_row.to_frame().T])
        formatted            = pivot_table.map(lambda x: f"€{x:,.2f}" if x > 0 else "—")
        formatted.index.name = "Supplier"
        st.dataframe(formatted, use_container_width=True)

    else:
        st.info("No deliveries recorded yet. Add your first delivery below.")

    # Ingestion Audit Log
    st.divider()
    st.subheader("📋 Ingestion Audit Log")
    st.caption("All deliveries saved via WhatsApp, grouped by supplier. Delete here to correct mistakes — inventory will be reversed automatically.")

    @st.cache_data(ttl=10)
    def load_audit_log():
        conn = get_conn()
        if not conn:
            return None
        try:
            return pd.read_sql("""
                SELECT
                    sd.id,
                    sd.supplier_name,
                    sd.delivery_date,
                    sd.amount,
                    sd.description,
                    sd.paid,
                    sd.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Copenhagen' AS created_at,
                    COUNT(di.id) as item_count
                FROM supplier_deliveries sd
                LEFT JOIN delivery_items di ON di.delivery_id = sd.id
                GROUP BY sd.id, sd.supplier_name, sd.delivery_date,
                         sd.amount, sd.description, sd.paid, sd.created_at
                ORDER BY sd.created_at DESC
                LIMIT 50
            """, conn)
        except Exception:
            return None
        finally:
            conn.close()

    audit = load_audit_log()

    if audit is None or audit.empty:
        st.info("No deliveries recorded yet.")
    else:
        for supplier in sorted(audit["supplier_name"].unique()):
            sup_audit = audit[audit["supplier_name"] == supplier].sort_values("delivery_date", ascending=False)
            sup_total = float(sup_audit["amount"].sum())
            sup_count = len(sup_audit)

            with st.expander(
                f"**{supplier}** — {sup_count} deliveries — Total: €{sup_total:,.2f}",
                expanded=False
            ):
                h1, h2, h3, h4, h5 = st.columns([2, 2, 1.5, 1, 0.5])
                h1.markdown("**Date**")
                h2.markdown("**Description**")
                h3.markdown("**Amount**")
                h4.markdown("**Items**")
                h5.markdown("**Del**")

                for _, row in sup_audit.iterrows():
                    c1, c2, c3, c4, c5 = st.columns([2, 2, 1.5, 1, 0.5])
                    c1.write(str(row["delivery_date"]))
                    c1.caption(f"Saved: {pd.to_datetime(row['created_at']).strftime('%Y-%m-%d %H:%M')}")
                    c2.write(row["description"] or "—")
                    c3.write(f"€{float(row['amount']):,.2f}")
                    c3.caption("✅ Paid" if row["paid"] else "⏳ Unpaid")
                    c4.write(f"{int(row['item_count'])} items")

                    if c5.button("🗑", key=f"audit_del_{row['id']}",
                                  help="Delete this mistake and reverse inventory"):
                        success, message = delete_delivery_with_reversal(row["id"])
                        if success:
                            st.success(message)
                            st.cache_data.clear()
                            st.rerun()
                        else:
                            st.error(f"Error: {message}")

    # Add Delivery
    st.divider()
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

    @st.cache_data(ttl=15)
    def load_inventory():
        conn = get_conn()
        if not conn:
            return None, None
        try:
            items = pd.read_sql("""
                SELECT id, name, unit, quantity, reorder_threshold, updated_at
                FROM inventory_items
                ORDER BY name
            """, conn)
            movements = pd.read_sql("""
                SELECT m.id, i.name, m.movement_type, m.quantity,
                       m.source, m.note, m.created_at
                FROM inventory_movements m
                JOIN inventory_items i ON i.id = m.item_id
                ORDER BY m.created_at DESC
                LIMIT 100
            """, conn)
            return items, movements
        except Exception:
            return None, None
        finally:
            conn.close()

    items, movements = load_inventory()

    if items is None or items.empty:
        st.info("No inventory data yet. Confirm a delivery via WhatsApp to populate inventory automatically.")
        st.stop()

    total_items  = len(items)
    low_stock    = items[items["quantity"] <= items["reorder_threshold"]]
    out_of_stock = items[items["quantity"] == 0]

    k1, k2, k3 = st.columns(3)
    k1.metric("Total Items Tracked", total_items)
    k2.metric("Low Stock Alerts", len(low_stock),
              delta=f"{len(low_stock)} need restocking" if len(low_stock) > 0 else None,
              delta_color="inverse")
    k3.metric("Out of Stock", len(out_of_stock),
              delta="Needs immediate attention" if len(out_of_stock) > 0 else None,
              delta_color="inverse")

    st.divider()
    st.subheader("Current Stock Levels")

    def stock_status(row):
        if row["quantity"] == 0:
            return "🔴 Out of stock"
        elif row["quantity"] <= row["reorder_threshold"] and row["reorder_threshold"] > 0:
            return "🟡 Low stock"
        else:
            return "🟢 OK"

    display = items.copy()
    display["Status"]            = display.apply(stock_status, axis=1)
    display["quantity"]          = display["quantity"].apply(lambda x: f"{float(x):,.2f}")
    display["reorder_threshold"] = display["reorder_threshold"].apply(lambda x: f"{float(x):,.2f}")
    display["updated_at"]        = pd.to_datetime(display["updated_at"]).dt.strftime("%Y-%m-%d %H:%M")

    st.dataframe(
        display[["name", "unit", "quantity", "reorder_threshold", "Status", "updated_at"]]
        .rename(columns={
            "name":              "Item",
            "unit":              "Unit",
            "quantity":          "In Stock",
            "reorder_threshold": "Reorder At",
            "updated_at":        "Last Updated",
        }),
        use_container_width=True,
        hide_index=True,
    )

    st.divider()
    st.subheader("Manual Stock Adjustment")
    st.caption("Use this to correct stock counts or record waste.")

    col1, col2, col3 = st.columns(3)
    item_names = items["name"].tolist()
    with col1:
        selected_item = st.selectbox("Item", options=item_names)
    with col2:
        adj_type = st.selectbox("Adjustment", options=["Add stock (+)", "Remove stock (-)"])
    with col3:
        adj_qty = st.number_input("Quantity", min_value=0.0, step=1.0, format="%.2f")

    adj_note = st.text_input("Note (optional)", placeholder="e.g. Stock count correction, waste")

    if st.button("Apply Adjustment", type="primary"):
        if adj_qty <= 0:
            st.error("Quantity must be greater than zero.")
        else:
            conn = get_conn()
            try:
                item_row = items[items["name"] == selected_item].iloc[0]
                item_id  = int(item_row["id"])
                current  = float(str(item_row["quantity"]).replace(",", ""))
                movement = "in" if "Add" in adj_type else "out"
                new_qty  = current + adj_qty if movement == "in" else max(0, current - adj_qty)
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE inventory_items SET quantity = %s, updated_at = NOW() WHERE id = %s",
                        (new_qty, item_id)
                    )
                    cur.execute("""
                        INSERT INTO inventory_movements (item_id, movement_type, quantity, source, note)
                        VALUES (%s, %s, %s, 'manual', %s)
                    """, (item_id, movement, adj_qty, adj_note or "Manual adjustment"))
                conn.commit()
                st.success(f"Updated {selected_item}: {current:,.2f} → {new_qty:,.2f} {item_row['unit']}")
                st.cache_data.clear()
                st.rerun()
            except Exception as e:
                conn.rollback()
                st.error(f"Error: {e}")
            finally:
                conn.close()

    st.divider()
    st.subheader("Set Reorder Thresholds")
    st.caption("Get a low stock alert when an item falls below this level.")

    col_a, col_b = st.columns(2)
    with col_a:
        threshold_item = st.selectbox("Item", options=item_names, key="threshold_item")
    with col_b:
        current_threshold = float(items[items["name"] == threshold_item]["reorder_threshold"].iloc[0])
        new_threshold = st.number_input(
            "Alert when stock falls below",
            min_value=0.0, value=current_threshold, step=1.0, format="%.2f"
        )

    if st.button("Save Threshold", type="primary"):
        conn = get_conn()
        try:
            item_id = int(items[items["name"] == threshold_item]["id"].iloc[0])
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE inventory_items SET reorder_threshold = %s WHERE id = %s",
                    (new_threshold, item_id)
                )
            conn.commit()
            st.success(f"Threshold for {threshold_item} set to {new_threshold:,.2f}")
            st.cache_data.clear()
            st.rerun()
        except Exception as e:
            conn.rollback()
            st.error(f"Error: {e}")
        finally:
            conn.close()

    st.divider()
    st.subheader("Recent Stock Movements")
    if movements is not None and not movements.empty:
        movements["created_at"]    = pd.to_datetime(movements["created_at"]).dt.strftime("%Y-%m-%d %H:%M")
        movements["movement_type"] = movements["movement_type"].map({"in": "📦 IN", "out": "🛒 OUT"})
        st.dataframe(
            movements[["created_at", "name", "movement_type", "quantity", "source", "note"]]
            .rename(columns={
                "created_at":    "Time",
                "name":          "Item",
                "movement_type": "Type",
                "quantity":      "Qty",
                "source":        "Source",
                "note":          "Note",
            }),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No movements recorded yet.")

# ══════════════════════════════════════════════════════════════════════════════
# PAGE: P&L
# ══════════════════════════════════════════════════════════════════════════════

elif page == "P&L":
    import plotly.graph_objects as go

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
        breakdown = pd.DataFrame({"Category": ["COGS", "Payroll", "Expenses"],
                                  "Amount (€)": [cogs, total_payroll, total_expenses_amt]})
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

    # ── Revenue trend chart ────────────────────────────────────────────────────

    st.divider()
    st.subheader("Monthly Revenue Trend")
    st.caption("Revenue per month vs break-even point. Shows if the restaurant is growing.")

    # Calculate break-even revenue — the revenue needed to cover all fixed costs
    breakeven_revenue = (total_payroll + total_expenses_amt) / (1 - COGS_RATE)

    rev_rows = []
    for m in sorted(sales_months):
        m_rev = float(txn[txn["month"] == m]["total"].sum())
        rev_rows.append({
            "month":   m.strftime("%B %Y"),
            "revenue": m_rev,
        })

    if rev_rows:
        rev_df = pd.DataFrame(rev_rows)

        fig_rev = go.Figure()

        # Revenue bars — green if above break-even, amber if below
        bar_colors_rev = ["#1a6b3a" if v >= breakeven_revenue else "#8b6914" for v in rev_df["revenue"]]

        fig_rev.add_trace(go.Bar(
            x=rev_df["month"],
            y=rev_df["revenue"],
            name="Monthly Revenue",
            marker_color=bar_colors_rev,
            hovertemplate="<b>%{x}</b><br>Revenue: €%{y:,.2f}<extra></extra>",
        ))

        # Break-even line
        fig_rev.add_hline(
            y=breakeven_revenue,
            line_dash="dash",
            line_color="#e8b84b",
            line_width=2,
            annotation_text=f"Break-even (€{breakeven_revenue:,.0f})",
            annotation_position="top right",
        )

        # Trend line using rolling average if enough data
        if len(rev_df) >= 2:
            fig_rev.add_trace(go.Scatter(
                x=rev_df["month"],
                y=rev_df["revenue"],
                name="Revenue trend",
                mode="lines+markers",
                line=dict(color="#85b7eb", width=2, dash="dot"),
                marker=dict(size=6),
                hovertemplate="<b>%{x}</b><br>Revenue: €%{y:,.2f}<extra></extra>",
            ))

        fig_rev.update_layout(
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            font_color="#cccccc",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            xaxis_title=None,
            yaxis_title="Revenue (€)",
            hovermode="x unified",
        )

        st.plotly_chart(fig_rev, use_container_width=True)

        # Gap to break-even for selected month
        gap = gross_revenue - breakeven_revenue
        if gap >= 0:
            st.success(f"{selected_month.strftime('%B %Y')} revenue is **€{gap:,.2f} above break-even** — costs are covered.")
        else:
            st.warning(f"{selected_month.strftime('%B %Y')} revenue is **€{abs(gap):,.2f} below break-even** — need €{breakeven_revenue:,.2f}/month to cover all costs.")

    # ── Cumulative P&L chart ───────────────────────────────────────────────────

    st.divider()
    st.subheader("Monthly Performance & Road to Profitability")
    st.caption("Bars show monthly net profit/loss. Line shows cumulative position.")

    STARTING_CAPITAL   = 200_000
    WARNING_THRESHOLD  = -STARTING_CAPITAL * 0.50
    DANGER_THRESHOLD   = -STARTING_CAPITAL * 0.75
    TERMINAL_THRESHOLD = -STARTING_CAPITAL * 0.90

    pl_rows = []
    for m in sorted(common_months):
        m_txn      = txn[txn["month"] == m]
        m_exp_date = date(m.year, m.month, 1)
        m_exp      = expenses[expenses["month"] == m_exp_date]

        if m_txn.empty or m_exp.empty:
            continue

        m_revenue = float(m_txn["total"].sum())
        m_cogs    = m_revenue * COGS_RATE
        m_gp      = m_revenue - m_cogs
        m_opex    = float(employees["monthly_salary"].sum()) + float(m_exp["amount"].sum())
        m_net     = m_gp - m_opex

        pl_rows.append({
            "month":      m.strftime("%B %Y"),
            "net_profit": m_net,
        })

    if len(pl_rows) > 0:
        pl_df               = pd.DataFrame(pl_rows)
        pl_df["cumulative"] = pl_df["net_profit"].cumsum()

        bar_colors = ["#1a6b3a" if v >= 0 else "#a32d2d" for v in pl_df["net_profit"]]

        fig = go.Figure()

        fig.add_trace(go.Bar(
            x=pl_df["month"],
            y=pl_df["net_profit"],
            name="Monthly Net Profit/Loss",
            marker_color=bar_colors,
            hovertemplate="<b>%{x}</b><br>Monthly: €%{y:,.2f}<extra></extra>",
        ))

        fig.add_trace(go.Scatter(
            x=pl_df["month"],
            y=pl_df["cumulative"],
            name="Cumulative P&L",
            mode="lines+markers",
            line=dict(color="#e8b84b", width=2.5),
            marker=dict(size=8),
            hovertemplate="<b>%{x}</b><br>Cumulative: €%{y:,.2f}<extra></extra>",
        ))

        fig.add_hline(y=0,        line_dash="dash", line_color="#888888", line_width=1,
                      annotation_text="Break-even",    annotation_position="bottom right")
        fig.add_hline(y=-100_000, line_dash="dot",  line_color="#e8b84b", line_width=1,
                      annotation_text="Warning (50%)", annotation_position="bottom right")
        fig.add_hline(y=-150_000, line_dash="dot",  line_color="#d85a30", line_width=1,
                      annotation_text="Danger (75%)",  annotation_position="bottom right")
        fig.add_hline(y=-180_000, line_dash="dot",  line_color="#a32d2d", line_width=1,
                      annotation_text="Critical (90%)", annotation_position="bottom right")

        fig.update_layout(
            barmode="relative",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            font_color="#cccccc",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            xaxis_title=None,
            yaxis_title="€",
            hovermode="x unified",
        )

        st.plotly_chart(fig, use_container_width=True)

        final_cumulative  = pl_df["cumulative"].iloc[-1]
        capital_remaining = STARTING_CAPITAL + final_cumulative
        capital_consumed  = (abs(final_cumulative) / STARTING_CAPITAL * 100) if final_cumulative < 0 else 0

        if final_cumulative >= 0:
            st.success(f"Cumulative position: **+€{final_cumulative:,.2f}** — the restaurant is profitable.")
        elif final_cumulative >= WARNING_THRESHOLD:
            st.success(
                f"Cumulative loss: **-€{abs(final_cumulative):,.2f}** ({capital_consumed:.1f}% of capital consumed) — "
                f"within expected startup range. Capital remaining: **€{capital_remaining:,.2f}**"
            )
        elif final_cumulative >= DANGER_THRESHOLD:
            st.warning(
                f"⚠️ WARNING — Cumulative loss: **-€{abs(final_cumulative):,.2f}** "
                f"({capital_consumed:.1f}% of capital consumed). "
                f"Capital remaining: **€{capital_remaining:,.2f}**. "
                f"Review pricing, staffing and cost structure urgently."
            )
        elif final_cumulative >= TERMINAL_THRESHOLD:
            st.error(
                f"🚨 DANGER — Cumulative loss: **-€{abs(final_cumulative):,.2f}** "
                f"({capital_consumed:.1f}% of capital consumed). "
                f"Capital remaining: **€{capital_remaining:,.2f}**. "
                f"Major decisions required — consider restructuring or pivoting."
            )
        else:
            st.error(
                f"🔴 CRITICAL — Cumulative loss: **-€{abs(final_cumulative):,.2f}** "
                f"({capital_consumed:.1f}% of capital consumed). "
                f"Capital remaining: **€{capital_remaining:,.2f}**. "
                f"Capital nearly depleted. Immediate action required."
            )

        st.markdown("**Capital runway**")
        col_r1, col_r2, col_r3 = st.columns(3)
        col_r1.metric("Starting Capital", f"€{STARTING_CAPITAL:,.2f}")
        col_r2.metric("Consumed",         f"€{abs(min(final_cumulative, 0)):,.2f} ({capital_consumed:.1f}%)")
        col_r3.metric("Remaining",        f"€{max(capital_remaining, 0):,.2f}")
        st.progress(min(capital_consumed / 100, 1.0))

    else:
        st.info("Need at least one month of complete data to show P&L trend.")