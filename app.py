import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
from anthropic import Anthropic
import json
import pandas as pd
from datetime import datetime, date
import plotly.express as px
import re

# ─── Config ───
st.set_page_config(page_title="Expense Tracker", page_icon="💰", layout="wide")

CLAUDE_MODEL = st.secrets.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
CURRENCY = st.secrets.get("CURRENCY_SYMBOL", "S$")
CATEGORIES = [
    "Food & Dining", "Transport", "Rent/Housing", "Utilities",
    "Entertainment", "Health/Medical", "Shopping", "Groceries",
    "Subscriptions", "Education", "Wedding", "Other"
]
PAYMENT_METHODS = ["Cash", "Credit Card", "Debit Card", "Digital/Paylah", "Bank Transfer"]
SHEET_HEADERS = ["Date", "Amount", "Category", "Description", "Payment Method", "Notes", "Added By", "Timestamp"]


# ─── Auth ───
def check_password():
    """Simple shared password gate."""
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
        st.session_state.username = ""

    if st.session_state.authenticated:
        return True

    st.markdown("## 🔐 Expense Tracker Login")
    st.markdown("Enter the shared password and your name to continue.")

    with st.form("login_form"):
        username = st.text_input("Your Name", placeholder="e.g. Alex")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Login", use_container_width=True)

    if submitted:
        if password == st.secrets.get("APP_PASSWORD", "expenses123"):
            st.session_state.authenticated = True
            st.session_state.username = username.strip() or "Anonymous"
            st.rerun()
        else:
            st.error("Incorrect password.")
    return False


# ─── Google Sheets Connection ───
@st.cache_resource
def get_gsheet_connection():
    """Connect to Google Sheets using service account credentials."""
    sheet_id = st.secrets.get("SHEET_ID", "")
    if sheet_id:
        # Only need Sheets scope when opening by ID (no Drive API needed)
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    else:
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], scopes=scopes
    )
    client = gspread.authorize(creds)
    return client


def get_or_create_sheet(client):
    """Open existing sheet or create a new one with headers."""
    sheet_id = st.secrets.get("SHEET_ID", "")
    if sheet_id:
        spreadsheet = client.open_by_key(sheet_id)
    else:
        sheet_name = st.secrets.get("SHEET_NAME", "Expense Tracker")
        try:
            spreadsheet = client.open(sheet_name)
        except gspread.SpreadsheetNotFound:
            spreadsheet = client.create(sheet_name)
            spreadsheet.share("", perm_type="anyone", role="reader")

    worksheet = spreadsheet.sheet1
    existing = worksheet.row_values(1)
    if not existing or existing[0] != SHEET_HEADERS[0]:
        worksheet.clear()
        worksheet.append_row(SHEET_HEADERS)
        worksheet.format("1", {"textFormat": {"bold": True}})

    return worksheet


@st.cache_data(ttl=60, show_spinner=False)
def load_expenses(_worksheet):
    """Load all expenses into a DataFrame."""
    worksheet = _worksheet
    records = worksheet.get_all_records()
    if not records:
        return pd.DataFrame(columns=SHEET_HEADERS)
    df = pd.DataFrame(records)
    if "Amount" in df.columns:
        df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce").fillna(0)
    if "Date" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    return df


def add_expense(worksheet, expense_data, username):
    """Append a row to the Google Sheet."""
    row = [
        expense_data["date"],
        expense_data["amount"],
        expense_data["category"],
        expense_data["description"],
        expense_data["payment_method"],
        expense_data.get("notes", ""),
        username,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    ]
    worksheet.append_row(row, value_input_option="USER_ENTERED")


def find_sheet_row(worksheet, row_data):
    """Find the 1-based sheet row index matching a DataFrame row by Timestamp."""
    timestamp = str(row_data.get("Timestamp", ""))
    if not timestamp:
        return None

    try:
        # Search in column H (Timestamp, column index 8) — server-side search
        cells = worksheet.findall(timestamp, in_column=8)
        if cells:
            # Verify by checking description too (in case of duplicate timestamps)
            for cell in cells:
                row_values = worksheet.row_values(cell.row)
                if (len(row_values) > 3
                        and row_values[3] == str(row_data.get("Description", ""))):
                    return cell.row
            # Fallback: return first match if description check fails
            return cells[0].row
    except Exception:
        pass

    # Fallback to full scan (original behavior)
    all_values = worksheet.get_all_values()
    row_date = (row_data["Date"].strftime("%Y-%m-%d")
                if hasattr(row_data["Date"], "strftime") else str(row_data["Date"]))
    for i, sheet_row in enumerate(all_values[1:], start=2):
        if (sheet_row[0] == row_date and
                sheet_row[3] == str(row_data.get("Description", "")) and
                sheet_row[7] == str(row_data.get("Timestamp", ""))):
            return i
    return None


def update_expense(worksheet, sheet_row_idx, expense_data, added_by):
    """Update an existing row in the Google Sheet."""
    updated_row = [
        expense_data["date"],
        str(expense_data["amount"]),
        expense_data["category"],
        expense_data["description"],
        expense_data["payment_method"],
        expense_data.get("notes", ""),
        added_by,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    ]
    worksheet.update(f"A{sheet_row_idx}:H{sheet_row_idx}", [updated_row],
                     value_input_option="USER_ENTERED")


# ─── Claude NLP Parsing ───
@st.cache_resource
def get_anthropic_client():
    """Reuse a single Anthropic client instance across reruns."""
    return Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])


def parse_expense_with_claude(user_input):
    """Use Claude to parse natural language into structured expense data."""
    client = get_anthropic_client()

    today = date.today().isoformat()

    system_prompt = f"""You are an expense parser. Parse natural language expense entries into structured JSON.

Return ONLY valid JSON with these fields:
- "date": string in YYYY-MM-DD format (default to today if not mentioned)
- "amount": number (just the number, no currency symbols)
- "category": one of {json.dumps(CATEGORIES)}
- "description": brief description of the expense
- "payment_method": one of {json.dumps(PAYMENT_METHODS)} (default to "Credit Card" if not mentioned)
- "notes": any extra context, or empty string

Be smart about inferring:
- "coffee" / "lunch" / "dinner" → Food & Dining
- "uber" / "taxi" / "bus" / "gas" / "fuel" → Transport
- "netflix" / "spotify" / "subscription" → Subscriptions
- "doctor" / "pharmacy" / "medicine" → Health/Medical
- "amazon" / "bought" / "purchased" → Shopping
- "groceries" / "supermarket" / "walmart" / "costco" → Groceries
- "movie" / "concert" / "game tickets" → Entertainment
- "rent" / "mortgage" → Rent/Housing
- "electric" / "water" / "internet" / "phone bill" → Utilities
- "course" / "books" / "tuition" → Education

If a credit card or card is mentioned, use "Credit Card". If Venmo/Zelle/PayPal/UPI/Apple Pay/Google Pay is mentioned, use "Digital/Paylah".

Respond with ONLY the JSON object, nothing else."""

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=150,
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": f"Today's date is {today}. Parse this: \"{user_input}\"",
            }
        ],
    )

    text = response.content[0].text.strip()
    text = text.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try extracting JSON object from surrounding text
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise ValueError(f"Could not parse response as JSON: {text[:200]}")


# ─── UI Components ───
def render_smart_input(worksheet):
    """The natural language input section."""
    if "smart_input_counter" not in st.session_state:
        st.session_state.smart_input_counter = 0
    st.markdown("### ✍️ Add Expense")
    st.markdown("Type naturally — e.g. *'coffee 4.50 credit card'* or *'uber to airport $32 yesterday'*")

    # Quick-add templates
    templates = ["Coffee $4.50", "Lunch $15 card", "Uber $12", "Groceries $85 debit", "Netflix $15.99"]
    tmpl_cols = st.columns(len(templates))
    for i, tmpl in enumerate(templates):
        if tmpl_cols[i].button(tmpl, key=f"tmpl_{i}", use_container_width=True):
            st.session_state.smart_input_prefill = tmpl
            st.session_state.smart_input_counter += 1
            st.rerun()

    prefill = st.session_state.pop("smart_input_prefill", "")
    user_input = st.text_input(
        "What did you spend?",
        value=prefill,
        placeholder="grabbed lunch with coworkers, $18, paid with visa",
        label_visibility="collapsed",
        key=f"smart_input_{st.session_state.smart_input_counter}",
    )

    col_parse, col_clear = st.columns([1, 1])

    if col_parse.button("🧠 Parse with AI", use_container_width=True, type="primary"):
        if not user_input.strip():
            st.warning("Type something first!")
            return
        with st.spinner("Understanding your expense..."):
            try:
                parsed = parse_expense_with_claude(user_input)
                st.session_state.parsed_expense = parsed
                st.session_state.show_confirm = True
                st.rerun()
            except Exception as e:
                st.error(f"Couldn't parse that. Try rephrasing. Error: {e}")

    if col_clear.button("🗑️ Clear", use_container_width=True):
        st.session_state.pop("parsed_expense", None)
        st.session_state.pop("show_confirm", None)
        st.session_state.smart_input_counter += 1
        st.rerun()

    # Show parsed result for confirmation/editing
    if st.session_state.get("show_confirm") and "parsed_expense" in st.session_state:
        parsed = st.session_state.parsed_expense
        st.markdown("---")
        st.markdown("#### ✅ Parsed Result — Edit if needed, then confirm")

        with st.form("confirm_expense"):
            c1, c2 = st.columns(2)
            try:
                default_date = datetime.strptime(parsed["date"], "%Y-%m-%d").date()
            except (ValueError, KeyError):
                default_date = date.today()
            exp_date = c1.date_input("Date", value=default_date)
            exp_amount = c2.number_input(f"Amount ({CURRENCY})", value=float(parsed["amount"]), min_value=0.0, step=0.01, format="%.2f")

            c3, c4 = st.columns(2)
            cat_idx = CATEGORIES.index(parsed["category"]) if parsed["category"] in CATEGORIES else 0
            exp_category = c3.selectbox("Category", CATEGORIES, index=cat_idx)
            pay_idx = PAYMENT_METHODS.index(parsed["payment_method"]) if parsed["payment_method"] in PAYMENT_METHODS else 0
            exp_payment = c4.selectbox("Payment Method", PAYMENT_METHODS, index=pay_idx)

            exp_desc = st.text_input("Description", value=parsed["description"])
            exp_notes = st.text_input("Notes", value=parsed.get("notes", ""))

            submitted = st.form_submit_button("💾 Confirm & Save", use_container_width=True, type="primary")

        if submitted:
            expense_data = {
                "date": exp_date.isoformat(),
                "amount": exp_amount,
                "category": exp_category,
                "description": exp_desc,
                "payment_method": exp_payment,
                "notes": exp_notes,
            }
            with st.spinner("Saving to Google Sheet..."):
                add_expense(worksheet, expense_data, st.session_state.username)
            st.success(f"✅ Saved: {exp_desc} — {CURRENCY}{exp_amount:.2f}")
            st.session_state.pop("parsed_expense", None)
            st.session_state.pop("show_confirm", None)
            st.session_state.smart_input_counter += 1
            load_expenses.clear()
            st.rerun()


def render_manual_input(worksheet):
    """Fallback manual entry form."""
    if "manual_entry_counter" not in st.session_state:
        st.session_state.manual_entry_counter = 0
    st.markdown("### 📝 Manual Entry")
    with st.form(f"manual_entry_{st.session_state.manual_entry_counter}"):
        c1, c2 = st.columns(2)
        exp_date = c1.date_input("Date", value=date.today())
        exp_amount = c2.number_input(f"Amount ({CURRENCY})", min_value=0.0, step=0.01, format="%.2f")

        c3, c4 = st.columns(2)
        exp_category = c3.selectbox("Category", CATEGORIES)
        exp_payment = c4.selectbox("Payment Method", PAYMENT_METHODS)

        exp_desc = st.text_input("Description")
        exp_notes = st.text_input("Notes (optional)")

        submitted = st.form_submit_button("💾 Save Entry", use_container_width=True)

    if submitted:
        if exp_amount <= 0 or not exp_desc.strip():
            st.warning("Please enter an amount and description.")
            return
        expense_data = {
            "date": exp_date.isoformat(),
            "amount": exp_amount,
            "category": exp_category,
            "description": exp_desc,
            "payment_method": exp_payment,
            "notes": exp_notes,
        }
        with st.spinner("Saving..."):
            add_expense(worksheet, expense_data, st.session_state.username)
        st.success(f"✅ Saved: {exp_desc} — {CURRENCY}{exp_amount:.2f}")
        load_expenses.clear()
        st.session_state.manual_entry_counter += 1
        st.rerun()


def render_dashboard(df):
    """Summary dashboard with charts."""
    st.markdown("### 📊 Dashboard")

    if df.empty:
        st.info("No expenses yet. Add your first one above!")
        return

    # Date range filter
    from datetime import timedelta
    today = date.today()
    col_range, col_presets = st.columns([2, 3])
    with col_range:
        date_range = st.date_input(
            "Date Range",
            value=(today.replace(day=1), today),
            max_value=today,
        )
    with col_presets:
        st.markdown("<br>", unsafe_allow_html=True)
        p1, p2, p3, p4 = st.columns(4)
        if p1.button("This Week", use_container_width=True):
            st.session_state.dash_range = (today - timedelta(days=today.weekday()), today)
            st.rerun()
        if p2.button("This Month", use_container_width=True):
            st.session_state.dash_range = (today.replace(day=1), today)
            st.rerun()
        if p3.button("Last 30 Days", use_container_width=True):
            st.session_state.dash_range = (today - timedelta(days=30), today)
            st.rerun()
        if p4.button("All Time", use_container_width=True):
            st.session_state.dash_range = None
            st.rerun()

    # Apply date filter
    if "dash_range" in st.session_state and st.session_state.dash_range:
        date_range = st.session_state.dash_range

    if isinstance(date_range, tuple) and len(date_range) == 2 and df["Date"].notna().any():
        start, end = pd.Timestamp(date_range[0]), pd.Timestamp(date_range[1])
        df = df[(df["Date"] >= start) & (df["Date"] <= end)]

    if df.empty:
        st.info("No expenses in the selected date range.")
        return

    # Quick stats
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Spent", f"{CURRENCY}{df['Amount'].sum():,.2f}")
    c2.metric("Entries", len(df))
    c3.metric("Avg / Entry", f"{CURRENCY}{df['Amount'].mean():,.2f}")
    c4.metric("Highest", f"{CURRENCY}{df['Amount'].max():,.2f}")

    st.markdown("---")
    col_left, col_right = st.columns(2)

    with col_left:
        # Category breakdown
        cat_totals = df.groupby("Category")["Amount"].sum().reset_index()
        cat_totals = cat_totals.sort_values("Amount", ascending=False)
        if not cat_totals.empty:
            fig_pie = px.pie(
                cat_totals, values="Amount", names="Category",
                title="By Category", hole=0.4,
                color_discrete_sequence=px.colors.qualitative.Set3
            )
            fig_pie.update_traces(
                textposition='inside',
                textinfo='percent',
                hovertemplate='%{label}: %{value:,.2f} (%{percent})<extra></extra>'
            )
            fig_pie.update_layout(
                height=420,
                margin=dict(t=40, b=0, l=0, r=0),
                legend=dict(orientation="h", yanchor="bottom", y=-0.3),
            )
            st.plotly_chart(fig_pie, use_container_width=True)

    with col_right:
        # Payment method breakdown
        pay_totals = df.groupby("Payment Method")["Amount"].sum().reset_index()
        pay_totals = pay_totals.sort_values("Amount", ascending=False)
        if not pay_totals.empty:
            fig_bar = px.bar(
                pay_totals, x="Payment Method", y="Amount",
                title="By Payment Method",
                color="Payment Method",
                color_discrete_sequence=px.colors.qualitative.Pastel
            )
            fig_bar.update_layout(height=380, showlegend=False, margin=dict(t=40, b=0))
            st.plotly_chart(fig_bar, use_container_width=True)

    # Daily spending trend
    if "Date" in df.columns and df["Date"].notna().any():
        daily = df.groupby(df["Date"].dt.date)["Amount"].sum().reset_index()
        daily.columns = ["Date", "Amount"]
        daily = daily.sort_values("Date")
        fig_line = px.area(
            daily, x="Date", y="Amount",
            title="Daily Spending Trend",
            color_discrete_sequence=["#1abc9c"]
        )
        fig_line.update_layout(height=300, margin=dict(t=40, b=0))
        st.plotly_chart(fig_line, use_container_width=True)


def render_history(df, worksheet):
    """Recent expense history table with search, pagination, and export."""
    st.markdown("### 📋 Recent Expenses")

    if df.empty:
        st.info("No expenses recorded yet.")
        return

    # Search
    search_query = st.text_input("🔍 Search expenses", placeholder="e.g. coffee, uber, pharmacy...")

    # Filters
    c1, c2, c3 = st.columns(3)
    cat_filter = c1.multiselect("Filter by Category", CATEGORIES)
    pay_filter = c2.multiselect("Filter by Payment Method", PAYMENT_METHODS)
    person_filter = c3.multiselect("Filter by Person", df["Added By"].unique().tolist() if "Added By" in df.columns else [])

    filtered = df.copy()
    if search_query:
        mask = (
            filtered["Description"].str.contains(search_query, case=False, na=False) |
            filtered["Notes"].str.contains(search_query, case=False, na=False) |
            filtered["Category"].str.contains(search_query, case=False, na=False)
        )
        filtered = filtered[mask]
    if cat_filter:
        filtered = filtered[filtered["Category"].isin(cat_filter)]
    if pay_filter:
        filtered = filtered[filtered["Payment Method"].isin(pay_filter)]
    if person_filter and "Added By" in filtered.columns:
        filtered = filtered[filtered["Added By"].isin(person_filter)]

    filtered = filtered.sort_values("Date", ascending=False)

    # Pagination
    PAGE_SIZE = 25
    total_entries = len(filtered)
    total_pages = max(1, (total_entries + PAGE_SIZE - 1) // PAGE_SIZE)
    col_page, _ = st.columns([1, 3])
    page = col_page.number_input("Page", min_value=1, max_value=total_pages, value=1, step=1)
    start = (page - 1) * PAGE_SIZE
    page_df = filtered.iloc[start:start + PAGE_SIZE]

    # Format for display
    display_df = page_df.copy()
    if "Date" in display_df.columns:
        display_df["Date"] = display_df["Date"].dt.strftime("%Y-%m-%d").fillna("")
    if "Amount" in display_df.columns:
        display_df["Amount"] = display_df["Amount"].apply(lambda x: f"{CURRENCY}{x:,.2f}")

    st.dataframe(display_df, use_container_width=True, hide_index=True)
    st.caption(f"Showing {start + 1}-{start + len(page_df)} of {total_entries} entries (Page {page}/{total_pages})")

    # CSV export (unformatted data)
    col_export, col_edit, col_delete = st.columns([1, 1, 1])
    csv_data = filtered.copy()
    if "Date" in csv_data.columns:
        csv_data["Date"] = csv_data["Date"].dt.strftime("%Y-%m-%d").fillna("")
    csv = csv_data.to_csv(index=False)
    col_export.download_button(
        label="📥 Download CSV",
        data=csv,
        file_name=f"expenses_{date.today().isoformat()}.csv",
        mime="text/csv",
        use_container_width=True,
    )

    # Edit expenses
    with col_edit:
        with st.expander("✏️ Edit Expense"):
            if len(page_df) > 0:
                row_to_edit = st.selectbox(
                    "Select entry to edit",
                    options=list(range(1, len(page_df) + 1)),
                    format_func=lambda i: f"Row {i}: {page_df.iloc[i-1].get('Description', '')} — {CURRENCY}{page_df.iloc[i-1].get('Amount', 0):,.2f}",
                    key="edit_row_select",
                )
                selected = page_df.iloc[row_to_edit - 1]
                _row_key = f"{page}_{row_to_edit}"
                with st.form(f"edit_expense_form_{_row_key}"):
                    ec1, ec2 = st.columns(2)
                    try:
                        default_date = selected["Date"].date() if hasattr(selected["Date"], "date") else date.today()
                    except Exception:
                        default_date = date.today()
                    edit_date = ec1.date_input("Date", value=default_date, key=f"edit_date_{_row_key}")
                    edit_amount = ec2.number_input(
                        f"Amount ({CURRENCY})", value=float(selected["Amount"]),
                        min_value=0.0, step=0.01, format="%.2f", key=f"edit_amount_{_row_key}",
                    )

                    ec3, ec4 = st.columns(2)
                    cat_idx = CATEGORIES.index(selected["Category"]) if selected["Category"] in CATEGORIES else 0
                    edit_category = ec3.selectbox("Category", CATEGORIES, index=cat_idx, key=f"edit_category_{_row_key}")
                    pay_idx = PAYMENT_METHODS.index(selected["Payment Method"]) if selected["Payment Method"] in PAYMENT_METHODS else 0
                    edit_payment = ec4.selectbox("Payment Method", PAYMENT_METHODS, index=pay_idx, key=f"edit_payment_{_row_key}")

                    edit_desc = st.text_input("Description", value=str(selected.get("Description", "")), key=f"edit_desc_{_row_key}")
                    edit_notes = st.text_input("Notes", value=str(selected.get("Notes", "")), key=f"edit_notes_{_row_key}")

                    # Added By — editable, populated from known users
                    known_users = df["Added By"].unique().tolist() if "Added By" in df.columns else []
                    current_added_by = str(selected.get("Added By", st.session_state.username))
                    if current_added_by and current_added_by not in known_users:
                        known_users.insert(0, current_added_by)
                    added_by_idx = known_users.index(current_added_by) if current_added_by in known_users else 0
                    edit_added_by = st.selectbox(
                        "Added By", options=known_users, index=added_by_idx,
                        key=f"edit_added_by_{_row_key}",
                    )

                    save_edit = st.form_submit_button("💾 Save Changes", use_container_width=True, type="primary")

                if save_edit:
                    if edit_amount <= 0 or not edit_desc.strip():
                        st.warning("Please enter an amount and description.")
                    else:
                        sheet_row_idx = find_sheet_row(worksheet, selected)
                        if sheet_row_idx:
                            # Optimistic locking: check if the row was modified since we loaded it
                            current_row = worksheet.row_values(sheet_row_idx)
                            original_timestamp = str(selected.get("Timestamp", ""))
                            current_timestamp = current_row[7] if len(current_row) > 7 else ""

                            if original_timestamp and current_timestamp and original_timestamp != current_timestamp:
                                st.error(
                                    "⚠️ This expense was modified by another user since you loaded it. "
                                    "Please refresh the page and try again."
                                )
                            else:
                                expense_data = {
                                    "date": edit_date.isoformat(),
                                    "amount": edit_amount,
                                    "category": edit_category,
                                    "description": edit_desc,
                                    "payment_method": edit_payment,
                                    "notes": edit_notes,
                                }
                                with st.spinner("Updating expense..."):
                                    update_expense(worksheet, sheet_row_idx, expense_data, edit_added_by)
                                st.success(f"✅ Updated: {edit_desc} — {CURRENCY}{edit_amount:.2f}")
                                load_expenses.clear()
                                st.rerun()
                        else:
                            st.error("Could not find the entry in the sheet. It may have been deleted.")

    # Delete expenses
    with col_delete:
        with st.expander("🗑️ Delete Expenses"):
            st.warning("Select entries to delete by row number (from the current page).")
            if len(page_df) > 0:
                rows_to_delete = st.multiselect(
                    "Select rows",
                    options=list(range(1, len(page_df) + 1)),
                    format_func=lambda i: f"Row {i}: {page_df.iloc[i-1].get('Description', '')} — {CURRENCY}{page_df.iloc[i-1].get('Amount', 0):,.2f}",
                )
                if st.button("Delete Selected", type="primary") and rows_to_delete:
                    # Collect all sheet row indices first, then delete in descending order
                    # to avoid index shifting between find and delete operations
                    sheet_rows = []
                    for row_num in rows_to_delete:
                        row = page_df.iloc[row_num - 1]
                        sheet_row_idx = find_sheet_row(worksheet, row)
                        if sheet_row_idx:
                            sheet_rows.append(sheet_row_idx)
                    if sheet_rows:
                        for idx in sorted(sheet_rows, reverse=True):
                            worksheet.delete_rows(idx)
                        st.success(f"Deleted {len(sheet_rows)} expense(s).")
                        load_expenses.clear()
                        st.rerun()


@st.dialog("Confirm Logout")
def confirm_logout():
    st.write("Are you sure you want to log out?")
    c1, c2 = st.columns(2)
    if c1.button("Yes, log out", type="primary", use_container_width=True):
        st.session_state.authenticated = False
        st.rerun()
    if c2.button("Cancel", use_container_width=True):
        st.rerun()


def render_guide():
    """Feature walkthrough and usage guide."""
    st.markdown("### ℹ️ Quick Guide")
    st.markdown("A short walkthrough of everything this app can do.")

    st.markdown("---")

    st.markdown("""
#### 🧠 Smart Input
Type expenses in plain English and let AI do the rest.

- **Natural language** — write something like *"coffee 4.50 credit card"* or *"uber to airport $32 yesterday"* and the AI will extract the date, amount, category, and payment method automatically.
- **Quick templates** — tap a preset button to pre-fill common expenses.
- **Review before saving** — the parsed result appears in an editable form so you can tweak anything before confirming.

#### 📝 Manual Entry
Prefer filling out a form yourself? Use the Manual tab to enter each field directly — date, amount, category, payment method, description, and notes.

#### 📊 Dashboard
Visualize your spending at a glance.

- **Top-line stats** — total spent, number of entries, average per entry, and highest single expense.
- **Date filters** — pick a custom range or use the quick presets (This Week, This Month, Last 30 Days, All Time).
- **Charts** — interactive pie chart by category, bar chart by payment method, and an area chart showing the daily spending trend.

#### 📋 History
Browse, search, and manage every recorded expense.

- **Search & filter** — find entries by keyword, category, payment method, or contributor.
- **Pagination** — 25 entries per page for easy browsing.
- **Export** — download your filtered data as a CSV file.
- **Edit** — select any entry to update its details.
- **Delete** — remove one or more entries at once.

#### ⚡ Sidebar
Always visible on the left.

- **Today / This Month** — real-time spending totals.
- **Budget tracker** — set a monthly budget and see a progress bar. A warning appears when you hit 90%.
- **Contributors** — see how much each person has added.
- **Refresh / Logout** — reload data from the sheet or sign out.
""")

    st.markdown("---")
    st.caption("All data is stored in a shared Google Sheet. Every user logs in with the same password and their own name.")


# ─── Main App ───
def main():
    if not check_password():
        return

    st.markdown(f"# 💰 Expense Tracker")
    st.caption(f"Logged in as **{st.session_state.username}** · Shared workspace")

    # Connect to Google Sheets
    try:
        client = get_gsheet_connection()
        worksheet = get_or_create_sheet(client)
    except Exception as e:
        st.error(f"⚠️ Could not connect to Google Sheets. Check your secrets configuration.\n\n{e}")
        st.markdown("""
        **Setup needed:** Add these to `.streamlit/secrets.toml`:
        ```toml
        APP_PASSWORD = "your_shared_password"
        ANTHROPIC_API_KEY = "sk-ant-..."
        SHEET_NAME = "Expense Tracker"

        [gcp_service_account]
        type = "service_account"
        project_id = "..."
        private_key_id = "..."
        private_key = "..."
        client_email = "..."
        client_id = "..."
        auth_uri = "..."
        token_uri = "..."
        # ... rest of service account JSON
        ```
        """)
        return

    # Load data
    df = load_expenses(worksheet)

    # Tabs
    tab_smart, tab_manual, tab_dash, tab_history, tab_guide = st.tabs(
        ["🧠 Smart Input", "📝 Manual", "📊 Dashboard", "📋 History", "ℹ️ Guide"]
    )

    with tab_smart:
        render_smart_input(worksheet)

    with tab_manual:
        render_manual_input(worksheet)

    with tab_dash:
        render_dashboard(df)

    with tab_history:
        render_history(df, worksheet)

    with tab_guide:
        render_guide()

    # Sidebar
    with st.sidebar:
        st.markdown("### ⚡ Quick Stats")
        if not df.empty:
            today = date.today()
            today_total = df[df["Date"].dt.date == today]["Amount"].sum() if df["Date"].notna().any() else 0
            st.metric("Today's Spending", f"{CURRENCY}{today_total:,.2f}")

            this_month = df[
                (df["Date"].dt.month == today.month) & (df["Date"].dt.year == today.year)
            ]["Amount"].sum() if df["Date"].notna().any() else 0
            st.metric("This Month", f"{CURRENCY}{this_month:,.2f}")

            # Monthly budget progress
            budget = st.session_state.get("monthly_budget", 0)
            if budget > 0:
                pct = min(this_month / budget, 1.0)
                st.markdown(f"**Budget:** {CURRENCY}{this_month:,.2f} / {CURRENCY}{budget:,.2f}")
                st.progress(pct)
                if pct >= 0.9:
                    st.warning(f"You've used {pct:.0%} of your monthly budget!")

            st.markdown("---")
            st.markdown("### 👥 Contributors")
            if "Added By" in df.columns:
                for person in df["Added By"].unique():
                    person_total = df[df["Added By"] == person]["Amount"].sum()
                    st.markdown(f"**{person}**: {CURRENCY}{person_total:,.2f}")
        else:
            st.info("No data yet")

        st.markdown("---")
        st.markdown("### 🎯 Monthly Budget")
        new_budget = st.number_input(
            f"Set budget ({CURRENCY})", min_value=0.0, step=50.0, format="%.2f",
            value=st.session_state.get("monthly_budget", 0.0),
        )
        if new_budget != st.session_state.get("monthly_budget", 0.0):
            st.session_state.monthly_budget = new_budget
            st.rerun()

        st.markdown("---")
        if st.button("🔄 Refresh Data"):
            load_expenses.clear()
            st.rerun()
        if st.button("🚪 Logout"):
            confirm_logout()


if __name__ == "__main__":
    main()