import streamlit as st
import pandas as pd
import sys
import subprocess
import time
from datetime import datetime
import io
import hashlib
import json
import uuid

# --- 1. CONFIGURATION & AUTO-INSTALL ---
st.set_page_config(page_title="Americana ERP - PM Module", page_icon="🏭", layout="wide")

def install(package):
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", package])
    except:
        st.error(f"Failed to install {package}. Please install manually.")

# --- IMPORTS ---
# Visualization
try:
    import plotly.express as px
    import plotly.graph_objects as go
except ImportError:
    install("plotly")
    import plotly.express as px
    import plotly.graph_objects as go

# Utilities
try:
    import qrcode
    from PIL import Image
except ImportError:
    install("qrcode")
    install("pillow")
    import qrcode
    from PIL import Image

try:
    import fpdf
    from fpdf import FPDF
except ImportError:
    install("fpdf")
    from fpdf import FPDF

try:
    import numpy as np
except ImportError:
    install("numpy")
    import numpy as np

# Excel support (required by pd.read_excel for .xlsx files)
try:
    import openpyxl
except ImportError:
    try:
        install("openpyxl")
        import openpyxl
    except Exception:
        openpyxl = None   # app still runs; only .xlsx upload is affected (add openpyxl to requirements.txt)

def read_upload(f):
    """Read an uploaded CSV / XLSX into a DataFrame."""
    if f.name.lower().endswith(".csv"):
        return pd.read_csv(f)
    if openpyxl is None:
        raise ImportError("Excel support is missing. Add 'openpyxl' to requirements.txt (or upload a CSV file).")
    return pd.read_excel(f)

# Machine Learning
try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.preprocessing import LabelEncoder
except ImportError:
    install("scikit-learn")
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.preprocessing import LabelEncoder

# --- 2. FILE-BASED STORAGE (replaces Firebase) ---
# All data lives in st.session_state.store = {collection_name: [documents]}.
# The user downloads it as a JSON backup file and uploads it again to resume.
APP_ID = "Americana ERP - PM Module"
BACKUP_VERSION = 1
COLLECTIONS = ["users", "assets", "pm_plans", "work_orders", "spare_parts", "breakdowns", "notifications", "chat_sessions"]

# Columns guaranteed to exist (prevents KeyErrors on empty / partial data)
DEFAULT_COLUMNS = {
    "work_orders": {"custom_id": "", "asset_name": "", "type": "", "status": "", "description": "",
                    "total_cost": 0.0, "created_at": None, "closed_date": None,
                    "fixing_technician": None, "technician": None},
    "spare_parts": {"name": "", "sku": "", "quantity": 0, "cost": 0.0, "location": ""},
    "assets": {"name": "", "tag": "", "site": ""},
    "pm_plans": {"asset_name": "", "frequency": "", "next_due": ""},
}

def init_store():
    if "store" not in st.session_state:
        st.session_state.store = {c: [] for c in COLLECTIONS}
    if "dirty" not in st.session_state:
        st.session_state.dirty = 0
    if "loaded_sig" not in st.session_state:
        st.session_state.loaded_sig = None

init_store()

def _json_default(o):
    if hasattr(o, "item"):
        try: return o.item()
        except Exception: pass
    return str(o)

def export_backup():
    payload = {
        "app": APP_ID,
        "version": BACKUP_VERSION,
        "saved_at": str(datetime.now()),
        "wo_counter": st.session_state.get("wo_counter", 1),
        "data": st.session_state.store,
    }
    return json.dumps(payload, default=_json_default, indent=2).encode("utf-8")

def import_backup(raw_bytes):
    payload = json.loads(raw_bytes.decode("utf-8"))
    if payload.get("app") != APP_ID or "data" not in payload:
        raise ValueError("This is not a valid Americana ERP backup file.")
    store = {c: [] for c in COLLECTIONS}
    for c, docs in payload["data"].items():
        store[c] = docs if isinstance(docs, list) else []
    st.session_state.store = store
    st.session_state.wo_counter = int(payload.get("wo_counter", 1))
    st.session_state.dirty = 0

def restore_widget(key):
    """Upload widget. Restores data once per distinct file, then reruns."""
    up = st.file_uploader("Upload saved data file (.json)", type=["json"], key=key)
    if up is not None:
        raw = up.getvalue()
        sig = hashlib.md5(raw).hexdigest()
        if sig != st.session_state.loaded_sig:
            try:
                import_backup(raw)
                st.session_state.loaded_sig = sig
                st.toast("Data restored from file", icon="✅")
                st.rerun()
            except Exception as e:
                st.error(f"Could not load file: {e}")

def safe_list(v):
    return v if isinstance(v, list) else []

def one_based(df):
    """Return a copy of the DataFrame whose row numbers start at 1 (not 0)."""
    df = df.copy()
    df.index = range(1, len(df) + 1)
    return df

# --- 3. UI & CSS STYLING (SAP FIORI STYLE) ---
def add_sap_styling(module_name):
    # SAP Color Palette & Dynamic Backgrounds
    backgrounds = {
        "📊 Dashboard": "linear-gradient(135deg, #fdfbfb 0%, #ebedee 100%)",
        "🤖 Migo Chatbot": "linear-gradient(135deg, #e0c3fc 0%, #8ec5fc 100%)", # AI Theme
        "🛠️ Asset Master Data": "linear-gradient(to top, #cfd9df 0%, #e2ebf0 100%)",
        "📅 Planned Maintenance": "linear-gradient(120deg, #fdfbfb 0%, #ebedee 100%)",
        "🔧 Work Orders": "linear-gradient(to top, #dfe9f3 0%, white 100%)",
        "📦 Inventory": "linear-gradient(to right, #ece9e6, #ffffff)",
        "🚨 Breakdown Reporting": "linear-gradient(to right, #ffecd2 0%, #fcb69f 100%)",
        "💼 Maintenance Manager": "linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%)"
    }

    bg_style = backgrounds.get(module_name, "white")

    st.markdown(f"""
        <style>
        /* MAIN BACKGROUND & FONT */
        .stApp {{
            background-image: {bg_style};
            font-family: "72", "Arial", "Helvetica", sans-serif;
        }}

        /* HEADER BAR (SHELL BAR) */
        header[data-testid="stHeader"] {{
            background-color: #354a5f;
        }}

        /* --- SIDEBAR STYLING --- */
        section[data-testid="stSidebar"] {{
            background-color: #2c3e50;
        }}

        /* FORCE WHITE TEXT IN SIDEBAR */
        section[data-testid="stSidebar"] * {{
            color: #ffffff !important;
        }}

        /* Sidebar file uploader / download button readability */
        section[data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] {{
            background-color: #34495e;
            border: 1px dashed #8fa3b8;
        }}
        section[data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] button,
        section[data-testid="stSidebar"] .stDownloadButton>button {{
            background-color: #0a6ed1;
            border: none;
        }}

        /* --- CRITICAL FIX: COLLAPSED SIDEBAR ARROW --- */
        [data-testid="stSidebarCollapsedControl"] {{
            color: #ffffff !important;
            background-color: transparent !important;
        }}
        [data-testid="stSidebarCollapsedControl"] i {{
            color: #ffffff !important;
        }}

        /* Fix for Expander headers inside Sidebar */
        section[data-testid="stSidebar"] .streamlit-expanderHeader {{
            color: #ffffff !important;
            background-color: transparent !important;
        }}

        /* CARDS / TILES */
        div.stMetric {{
            background-color: #ffffff;
            border: 1px solid #d9d9d9;
            padding: 15px;
            border-radius: 4px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.12), 0 1px 2px rgba(0,0,0,0.24);
            transition: all 0.3s cubic-bezier(.25,.8,.25,1);
        }}
        div.stMetric:hover {{
            box-shadow: 0 14px 28px rgba(0,0,0,0.25), 0 10px 10px rgba(0,0,0,0.22);
        }}

        /* BUTTONS */
        .stButton>button {{
            background-color: #0a6ed1;
            color: white;
            border-radius: 4px;
            border: none;
            font-weight: bold;
        }}
        .stButton>button:hover {{
            background-color: #0854a0;
        }}

        /* TABS styling */
        .stTabs [data-baseweb="tab-list"] {{
            gap: 10px;
        }}
        .stTabs [data-baseweb="tab"] {{
            height: 50px;
            white-space: pre-wrap;
            background-color: white;
            border-radius: 4px 4px 0px 0px;
            gap: 1px;
            padding-top: 10px;
            padding-bottom: 10px;
            border: 1px solid #d9d9d9;
        }}
        .stTabs [aria-selected="true"] {{
            background-color: #eff4f9;
            border-bottom: 3px solid #0a6ed1;
            color: #0a6ed1;
            font-weight: bold;
        }}

        /* DATAFRAME HEADERS */
        thead tr th:first-child {{display:none}}
        tbody th {{display:none}}
        </style>
    """, unsafe_allow_html=True)

# --- 4. HELPER FUNCTIONS ---

def hash_password(password):
    return hashlib.sha256(str.encode(password)).hexdigest()

def log_notification(message, toast=True):
    try:
        st.session_state.store["notifications"].append({
            "id": uuid.uuid4().hex,
            "message": message,
            "timestamp": str(datetime.now()),
            "read": False
        })
        if toast:
            st.toast(message, icon="🔔")
    except Exception as e:
        print(f"Notification Error: {e}")

def load_collection(collection_name):
    """Returns the collection as a DataFrame (same contract as before)."""
    try:
        docs = st.session_state.store.get(collection_name, [])
        df = pd.DataFrame([dict(d) for d in docs])
        for col, default in DEFAULT_COLUMNS.get(collection_name, {}).items():
            if col not in df.columns:
                df[col] = default
        return df
    except Exception:
        return pd.DataFrame()

def fetch_notifications():
    try:
        notes = list(st.session_state.store.get("notifications", []))
        notes.sort(key=lambda n: str(n.get("timestamp", "")), reverse=True)
        out = []
        for n in notes[:10]:
            n = dict(n)
            n["timestamp"] = pd.to_datetime(n.get("timestamp"), errors="coerce")
            out.append(n)
        return out
    except Exception:
        return []

def save_document(collection_name, data, notify=True):
    try:
        doc = dict(data)
        doc["id"] = uuid.uuid4().hex
        st.session_state.store.setdefault(collection_name, []).append(doc)
        st.session_state.dirty += 1

        if notify:
            msg = f"New record added to {collection_name}"
            if collection_name == 'pm_plans': msg = f"📅 PM Plan Created: {data.get('asset_name', 'Unknown')}"
            elif collection_name == 'work_orders': msg = f"🔧 WO Generated: {data.get('asset_name', 'Unknown')}"
            elif collection_name == 'breakdowns': msg = f"🚨 Breakdown Reported: {data.get('asset_name', 'Unknown')}"
            elif collection_name == 'assets': msg = f"🛠️ New Asset Imported: {data.get('tag', 'Unknown')}"
            elif collection_name == 'spare_parts': msg = f"📦 New Part Added: {data.get('name', 'Unknown')}"
            log_notification(msg)
    except Exception as e:
        st.error(f"Error saving data: {e}")

def update_document(collection_name, doc_id, data):
    try:
        for doc in st.session_state.store.get(collection_name, []):
            if doc.get("id") == doc_id:
                doc.update(data)
                break
        st.session_state.dirty += 1
        status = data.get('status', 'Updated')
        log_notification(f"✅ {collection_name[:-1].title()} {status}: ID {str(doc_id)[:5]}...")
    except Exception as e:
        st.error(f"Error updating data: {e}")

# --- 5. AUTHENTICATION ---
if 'user' not in st.session_state:
    st.session_state.user = None
if 'role' not in st.session_state:
    st.session_state.role = None
if 'wo_counter' not in st.session_state:
    st.session_state.wo_counter = 1

def login_screen():
    st.markdown("""
    <style>
    .stApp { background-color: #f0f2f5; }
    .login-box {
        background: white; padding: 40px; border-radius: 8px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.1); text-align: center;
    }
    </style>
    """, unsafe_allow_html=True)

    col1, col2, col3 = st.columns([1,2,1])
    with col2:
        st.markdown("## 🏭 FactoryPro Login")
        st.markdown("**Enterprise Access**")

        # Resume previous session BEFORE signing in
        with st.expander("📂 Resume previous work (upload saved data file)", expanded=False):
            restore_widget("restore_login")
            n_docs = sum(len(v) for v in st.session_state.store.values())
            if n_docs: st.caption(f"✅ Data loaded: {n_docs} records")

        with st.form("login"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign In")

            if submitted:
                if username == "admin" and password == "admin123":
                    st.session_state.user = "admin"
                    st.session_state.role = "Manager"
                    st.rerun()

                users = load_collection('users')
                if not users.empty:
                    user_match = users[users['username'] == username]
                    if not user_match.empty:
                        db_pass = user_match.iloc[0].get('password_hash', '')
                        if db_pass == hash_password(password) or db_pass == password:
                            st.session_state.user = username
                            if username == "admin":
                                st.session_state.role = "Manager"
                            else:
                                st.session_state.role = user_match.iloc[0].get('role', 'User')
                            st.success("Login Successful")
                            time.sleep(0.5)
                            st.rerun()
                        else:
                            st.error("Invalid Password")
                    else:
                        st.error("User not found")
                else:
                    st.info("No users stored. Use Master: admin / admin123")

if not st.session_state.user:
    login_screen()
    st.stop()

# --- 6. PDF GENERATOR ---
class PDF(FPDF):
    def header(self):
        self.set_font('Arial', 'B', 15)
        self.cell(0, 10, 'Americana Foods - EP Plants', 0, 1, 'L')
        self.set_font('Arial', 'I', 10)
        self.cell(0, 10, 'Maintenance Work Order', 0, 1, 'L')
        self.line(10, 30, 200, 30)
        self.ln(5)
    def footer(self):
        self.set_y(-15)
        self.set_font('Arial', 'I', 8)
        self.cell(0, 10, f'Page {self.page_no()}', 0, 0, 'C')

def create_wo_pdf(wo_data):
    pdf = PDF()
    pdf.add_page()
    pdf.set_font("Arial", size=10)

    def get_meta_value(key):
        meta = wo_data.get('meta')
        if isinstance(meta, dict):
            return str(meta.get(key, ''))
        return ''

    def print_field(label, value):
        pdf.set_font("Arial", 'B', 10)
        pdf.cell(35, 8, label, 0, 0)
        pdf.set_font("Arial", '', 10)
        pdf.cell(55, 8, str(value), 0, 0)

    print_field("Product Brand:", get_meta_value('brand'))
    print_field("Product Type:", wo_data.get('asset_name', ''))
    pdf.ln(8)
    print_field("Manufacturer:", get_meta_value('manufacturer'))
    label_txt = f"{wo_data.get('asset_name', '')} / {get_meta_value('tag')}"
    print_field("Label:", label_txt)
    pdf.ln(8)
    print_field("Section:", get_meta_value('section'))
    print_field("Tag Number:", get_meta_value('tag'))
    pdf.ln(8)
    print_field("Line:", get_meta_value('line'))
    print_field("Location:", get_meta_value('location'))
    pdf.ln(8)
    print_field("Overhaul ID:", wo_data.get('custom_id', ''))
    pdf.ln(12)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(5)

    pdf.set_font("Arial", 'B', 10)
    pdf.cell(40, 8, "Check Type:", 0, 0)
    pdf.set_font("Arial", '', 10)
    pdf.cell(50, 8, str(wo_data.get('type', '')), 0, 1)

    pdf.set_font("Arial", 'B', 10)
    pdf.cell(40, 8, "Description:", 0, 0)
    pdf.set_font("Arial", '', 10)
    desc = wo_data.get('description', '')
    pdf.multi_cell(0, 8, str(desc) if isinstance(desc, str) else '')
    pdf.ln(5)

    pdf.set_fill_color(255, 230, 230)
    pdf.set_font("Arial", 'B', 9)
    pdf.multi_cell(0, 8, "SAFETY WARNING: Never perform maintenance unless machine is at complete standstill. LOTO Applied.", 1, 'C', True)
    pdf.ln(5)

    pdf.set_fill_color(220, 220, 220)
    pdf.cell(10, 8, "#", 1, 0, 'C', True)
    pdf.cell(110, 8, "Task Description", 1, 0, 'C', True)
    pdf.cell(20, 8, "Status", 1, 0, 'C', True)
    pdf.cell(50, 8, "Remarks", 1, 1, 'C', True)

    tasks = wo_data.get('tasks_data')
    pdf.set_font("Arial", '', 9)
    if isinstance(tasks, list) and len(tasks) > 0:
        for idx, t in enumerate(tasks, 1):
            if isinstance(t, dict):
                pdf.cell(10, 8, str(t.get('id', idx)), 1, 0, 'C')
                pdf.cell(110, 8, str(t.get('desc', '')), 1, 0, 'L')
                done_mark = "DONE" if t.get('done') else "PENDING"
                pdf.cell(20, 8, done_mark, 1, 0, 'C')
                pdf.cell(50, 8, str(t.get('remark', '')), 1, 1, 'L')
    else:
        pdf.cell(190, 8, "See General Remarks", 1, 1, 'C')
    pdf.ln(10)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(5)

    parts = wo_data.get('parts_used', [])
    if parts and isinstance(parts, list) and len(parts) > 0:
        pdf.set_font("Arial", 'B', 10)
        pdf.cell(0, 8, "Spare Parts & Spendings", 0, 1, 'L')
        pdf.set_fill_color(240, 240, 240)
        pdf.set_font("Arial", 'B', 9)
        pdf.cell(80, 8, "Part Name", 1, 0, 'L', True)
        pdf.cell(20, 8, "Qty", 1, 0, 'C', True)
        pdf.cell(30, 8, "Unit Cost", 1, 0, 'R', True)
        pdf.cell(30, 8, "Total", 1, 1, 'R', True)
        pdf.set_font("Arial", '', 9)
        total_parts_cost = 0.0
        for p in parts:
            name = str(p.get('name', 'Unknown'))
            qty = float(p.get('qty', 0))
            line_total = float(p.get('total_cost', 0))
            cost = float(p.get('unit_cost', line_total / qty if qty else 0))
            total_parts_cost += line_total
            pdf.cell(80, 8, name, 1, 0, 'L')
            pdf.cell(20, 8, str(qty), 1, 0, 'C')
            pdf.cell(30, 8, f"${cost:.2f}", 1, 0, 'R')
            pdf.cell(30, 8, f"${line_total:.2f}", 1, 1, 'R')
        pdf.set_font("Arial", 'B', 9)
        pdf.cell(130, 8, "Total Spare Parts Cost:", 1, 0, 'R')
        pdf.cell(30, 8, f"${total_parts_cost:.2f}", 1, 1, 'C', True)
        pdf.ln(8)
    else:
        pdf.set_font("Arial", 'I', 9)
        pdf.cell(0, 10, "No spare parts consumed.", 0, 1, 'L')

    tech = wo_data.get('fixing_technician')
    if not isinstance(tech, str) or not tech:
        tech = wo_data.get('technician', '')
    approver = wo_data.get('approver', '')
    pdf.set_font("Arial", 'B', 10)
    pdf.cell(90, 10, f"Technician: {tech if isinstance(tech, str) else ''}", 0, 0)
    pdf.cell(90, 10, f"Approver: {approver if isinstance(approver, str) else ''}", 0, 1)

    return pdf.output(dest='S').encode('latin-1', 'replace')

# --- 7. DATA HANDLING ---
def generate_overhaul_id(wo_number):
    now = datetime.now()
    return f"{wo_number:04d}{now.hour:02d}{now.minute:02d}{now.day:02d}{now.month:02d}{now.year}"

def get_machine_data():
    st.sidebar.markdown("---")
    st.sidebar.header("📂 PM Data Source")
    st.sidebar.caption("Upload file (Machine Name, Site, Section, etc.)")
    uploaded_file = st.sidebar.file_uploader("Upload Machine List (Optional)", type=['csv', 'xlsx'], key="machine_list_upload")

    if uploaded_file is not None:
        try:
            return read_upload(uploaded_file)
        except Exception as e:
            st.sidebar.error(f"Error reading file: {e}")
            return pd.DataFrame()
    else:
        df_assets = load_collection('assets')
        if not df_assets.empty:
            return pd.DataFrame({
                "Machine Name": df_assets.get('name', 'Unknown'),
                "Brand": df_assets.get('brand', 'Generic'),
                "Manufacturer": df_assets.get('manufacturer', 'Generic'),
                "Tag Number": df_assets.get('tag', 'Unknown'),
                "Frequency": "Standard",
                "Site": df_assets.get('site', "Unknown"),
                "Section": df_assets.get('section', "Unknown")
            })
        else:
            return pd.DataFrame(columns=["Machine Name", "Brand", "Manufacturer", "Tag Number", "Site", "Section"])

# --- 8. SIDEBAR (RESTRUCTURED & WHITE TEXT) ---
with st.sidebar:
    # 1. Image/Logo at Top
    st.image("https://cdn-icons-png.flaticon.com/512/906/906343.png", width=60)

    # 2. User Info
    user_display = st.session_state.get('user', 'User')
    st.markdown(f"**User: {user_display}**")

    # 3. Connection Status
    st.markdown("✅ **ERP Connected**")

    st.write("") # Spacer

    # 3b. DATA FILE (replaces the database)
    st.markdown("### 💾 Data File")
    if st.session_state.dirty > 0:
        st.markdown(f"⚠️ **{st.session_state.dirty} unsaved change(s)**")
    else:
        st.markdown("✅ All changes saved")
    st.download_button(
        "📥 Save Data to File",
        data=export_backup(),
        file_name=f"americana_erp_data_{datetime.now().strftime('%Y%m%d_%H%M')}.json",
        mime="application/json",
        key="download_backup",
        on_click=lambda: st.session_state.update(dirty=0),
    )
    with st.expander("📂 Load Data from File", expanded=False):
        restore_widget("restore_sidebar")

    st.write("")

    # 4. Alerts
    st.markdown("### 🔔 Alerts")
    notes_data = fetch_notifications()
    with st.expander("Latest Messages", expanded=False):
        if notes_data:
            for n_data in notes_data:
                ts = n_data.get('timestamp')
                time_str = ts.strftime("%H:%M") if pd.notna(ts) else ""
                st.markdown(f"**{time_str}** {n_data.get('message', '')}")
        else:
            st.markdown("No notifications.")

    st.write("") # Spacer

    # 5. Logout Button
    if st.button("LOGOUT", type="primary"):
        st.session_state.user = None
        st.rerun()

    st.write("") # Spacer

    # 6. Menu Modules (AT BOTTOM)
    st.markdown("**ERP Modules**")
    menu_options = ["📊 Dashboard", "🤖 Migo Chatbot", "🛠️ Asset Master Data", "📅 Planned Maintenance", "🔧 Work Orders", "📦 Inventory", "🚨 Breakdown Reporting"]

    if st.session_state.role == "Manager" or st.session_state.user == "admin":
         menu_options.append("💼 Maintenance Manager")

    menu = st.radio("ERP Modules", menu_options, label_visibility="collapsed")

# --- APPLY SAP STYLE ---
add_sap_styling(menu)

# =====================================================================
# --- QR LABEL ENGINE (styled QR + logo + printable A4 label sheets) ---
# =====================================================================
import os
import tempfile
from PIL import ImageDraw, ImageFont

def _qr_font(size, bold=True):
    names = (["DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf"] if bold
             else ["DejaVuSans.ttf", "LiberationSans-Regular.ttf", "arial.ttf", "Arial.ttf"])
    for n in names:
        try: return ImageFont.truetype(n, size)
        except Exception: pass
    try: return ImageFont.load_default(size)
    except Exception: return ImageFont.load_default()

def default_logo_png():
    """Fallback badge (monogram) used until the real Americana Foods logo is uploaded."""
    size = 400
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0)); d = ImageDraw.Draw(img)
    d.ellipse((0, 0, size - 1, size - 1), fill=(200, 16, 46, 255))
    d.ellipse((22, 22, size - 23, size - 23), outline=(255, 255, 255, 255), width=10)
    f = _qr_font(170); bb = d.textbbox((0, 0), "AF", font=f)
    d.text(((size - (bb[2] - bb[0])) / 2 - bb[0], (size - (bb[3] - bb[1])) / 2 - bb[1]), "AF", font=f, fill=(255, 255, 255, 255))
    buf = io.BytesIO(); img.save(buf, "PNG"); return buf.getvalue()

@st.cache_data(show_spinner=False, max_entries=3000)
def make_qr_png(payload, logo_bytes=b""):
    """QR with HIGH error correction (30%) and the logo centred on a white rounded badge (22% of width)."""
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=8, border=2)
    qr.add_data(payload); qr.make(fit=True)
    img = qr.make_image(fill_color="#1b2a3a", back_color="white")
    img = (img.get_image() if hasattr(img, "get_image") else img).convert("RGB")
    W = img.size[0]
    logo = Image.open(io.BytesIO(logo_bytes or default_logo_png())).convert("RGBA")
    target = int(W * 0.22)
    logo.thumbnail((target, target), Image.LANCZOS)
    pad = max(4, int(target * 0.14))
    bw, bh = logo.size[0] + 2 * pad, logo.size[1] + 2 * pad
    badge = Image.new("RGBA", (bw, bh), (255, 255, 255, 0))
    ImageDraw.Draw(badge).rounded_rectangle((0, 0, bw - 1, bh - 1), radius=int(min(bw, bh) * 0.2), fill=(255, 255, 255, 255))
    badge.alpha_composite(logo, (pad, pad))
    img.paste(badge, ((W - bw) // 2, (W - bh) // 2), badge)
    buf = io.BytesIO(); img.save(buf, "PNG"); return buf.getvalue()

def asset_label_fields(row):
    def clean(v):
        v = "" if v is None else str(v)
        return "" if v.strip().lower() in ("nan", "none") else v.strip()
    name, tag, aid = clean(row.get("name")), clean(row.get("tag")), clean(row.get("id"))
    loc = " / ".join(x for x in [clean(row.get("site")), clean(row.get("section")), clean(row.get("line"))] if x)
    display_id = tag or aid[:8].upper()
    payload = f"ID:{aid}\nTag:{tag}\nName:{name}"
    return display_id, name, loc, payload

def make_label_png(display_id, name, payload, logo_bytes=b""):
    """Single printable label image: header band, QR with logo, asset ID below."""
    qr = Image.open(io.BytesIO(make_qr_png(payload, logo_bytes))).convert("RGB")
    W = qr.size[0]; pad = 30; head = 60; foot = 120
    canvas = Image.new("RGB", (W + 2 * pad, head + W + foot), "white"); d = ImageDraw.Draw(canvas)
    d.rectangle((0, 0, canvas.size[0], head), fill=(53, 74, 95))
    def center(text, y, font, fill):
        bb = d.textbbox((0, 0), text, font=font); d.text(((canvas.size[0] - (bb[2] - bb[0])) / 2 - bb[0], y), text, font=font, fill=fill)
    center("AMERICANA FOODS", 14, _qr_font(30), "white")
    canvas.paste(qr, (pad, head + 5))
    f_id = _qr_font(44)
    while d.textbbox((0, 0), display_id, font=f_id)[2] > canvas.size[0] - 20 and f_id.size > 16:
        f_id = _qr_font(f_id.size - 4)
    center(display_id, head + W + 12, f_id, (27, 42, 58))
    center(name[:40], head + W + 72, _qr_font(26, bold=False), (90, 100, 110))
    d.rectangle((0, 0, canvas.size[0] - 1, canvas.size[1] - 1), outline=(180, 190, 200), width=3)
    buf = io.BytesIO(); canvas.save(buf, "PNG"); return buf.getvalue()

def _lat(t): return str(t).encode("latin-1", "replace").decode("latin-1")

def create_qr_labels_pdf(df, logo_bytes=b"", cols=3, rows=4, progress=None):
    """A4 sheet(s) of labels ready to print and stick on machines."""
    pdf = FPDF("P", "mm", "A4"); pdf.set_auto_page_break(False)
    mx, my = 8, 10
    cw, ch = (210 - 2 * mx) / cols, (297 - 2 * my) / rows
    per, hh = cols * rows, 7
    tmp = tempfile.mkdtemp(); n = len(df)
    for i, (_, row) in enumerate(df.iterrows()):
        if i % per == 0: pdf.add_page()
        k = i % per; x = mx + (k % cols) * cw; y = my + (k // cols) * ch
        display_id, name, loc, payload = asset_label_fields(row)
        pdf.set_draw_color(170, 180, 190); pdf.set_line_width(0.3); pdf.rect(x + 1, y + 1, cw - 2, ch - 2)
        pdf.set_fill_color(53, 74, 95); pdf.rect(x + 1, y + 1, cw - 2, hh, "F")
        pdf.set_xy(x + 1, y + 1 + 1.2); pdf.set_font("Arial", "B", 8); pdf.set_text_color(255, 255, 255)
        pdf.cell(cw - 2, 4.5, "AMERICANA FOODS", 0, 0, "C")
        qr_size = min(cw - 10, ch - hh - 24)
        path = os.path.join(tmp, f"q{i}.png")
        with open(path, "wb") as fh: fh.write(make_qr_png(payload, logo_bytes))
        pdf.image(path, x + (cw - qr_size) / 2, y + 1 + hh + 2, qr_size, qr_size)
        try: os.remove(path)
        except Exception: pass
        fs = 12 if cols <= 2 else 10.5 if cols == 3 else 9
        pdf.set_font("Arial", "B", fs); txt = _lat(display_id)
        while pdf.get_string_width(txt) > cw - 6 and fs > 5:
            fs -= 0.5; pdf.set_font("Arial", "B", fs)
        pdf.set_text_color(27, 42, 58); pdf.set_xy(x + 1, y + 1 + hh + 2 + qr_size + 1.5)
        pdf.cell(cw - 2, 5.5, txt, 0, 2, "C")
        pdf.set_font("Arial", "", 7.5); pdf.set_text_color(90, 100, 110)
        pdf.cell(cw - 2, 4, _lat(name[:40]), 0, 2, "C")
        if loc: pdf.cell(cw - 2, 4, _lat(loc[:44]), 0, 2, "C")
        if progress and (i % 5 == 0 or i == n - 1): progress((i + 1) / max(n, 1))
    try: os.rmdir(tmp)
    except Exception: pass
    return pdf.output(dest="S").encode("latin-1", "replace")


# =====================================================================
# --- 9. MIGO AI / NLP ENGINE ---
# Hybrid NLP pipeline (100% local, no API needed):
#   1. Rule layer        -> high-precision regex for KPI words (MTTR, OEE, ...)
#   2. Intent classifier -> TF-IDF (word + character n-grams) + cosine similarity
#   3. Entity extraction -> fuzzy asset/tag matching, dates, top-N, site/line
#   4. Context memory    -> follow-up questions ("and last month?", "what about Oven?")
#   5. Analytics layer   -> KPIs, forecast, risk scoring, TF-IDF failure keywords
#   6. Optional LLM      -> free-form questions when an API key is provided
# =====================================================================
import re
import difflib
from datetime import timedelta
from sklearn.metrics.pairwise import cosine_similarity

INTENT_EXAMPLES = {
    "greeting": ["hi", "hello", "hey migo", "good morning", "good evening"],
    "thanks": ["thanks", "thank you", "great job", "perfect", "awesome"],
    "help": ["help", "what can you do", "what can i ask you", "capabilities", "how can you help me", "show me examples"],
    "total_spend": ["total maintenance spend", "how much did we spend", "total cost", "what is the total spending",
                    "maintenance expenses so far", "total spend this month", "how much money spent on maintenance"],
    "cost_by_asset": ["cost by asset", "spend per machine", "which machine costs the most", "top spenders",
                      "most expensive assets", "maintenance cost per machine", "chart cost by asset"],
    "cost_trend": ["monthly cost trend", "cost over time", "spending trend", "cost per month",
                   "how did cost change", "trend of maintenance spend"],
    "forecast": ["forecast next month cost", "predict maintenance spend", "cost prediction",
                 "expected spending next month", "budget forecast", "projection of costs"],
    "open_orders": ["list open work orders", "show open orders", "pending work orders", "which jobs are open",
                    "open wo", "what is still open", "outstanding orders"],
    "closed_orders": ["show closed work orders", "completed orders", "finished jobs", "closed wo history",
                      "list completed work orders"],
    "wo_summary": ["how many work orders", "work order count", "number of work orders", "work order status",
                   "work orders by status", "work orders overview"],
    "breakdown_summary": ["how many breakdowns", "breakdown count", "number of failures", "list breakdowns",
                          "show breakdown events", "unplanned stops", "recent breakdowns"],
    "bad_actors": ["bad actors", "which machine breaks down the most", "worst machines", "most failures",
                   "machines with most breakdowns", "problem machines", "top breakdown assets"],
    "mttr": ["what is mttr", "mean time to repair", "average repair time", "how long do repairs take", "repair duration"],
    "mtbf": ["what is mtbf", "mean time between failures", "average time between breakdowns", "failure interval"],
    "availability": ["oee", "availability", "uptime", "machine availability percentage", "equipment effectiveness",
                     "downtime hours", "total downtime"],
    "low_stock": ["low stock items", "which parts are running out", "spare parts shortage", "items below minimum",
                  "reorder list", "stock alert", "parts to reorder"],
    "inventory_value": ["inventory value", "value of spare parts", "how much is stock worth", "total stock value",
                        "inventory worth"],
    "inventory_list": ["list spare parts", "show inventory", "all parts in stock", "spare parts list", "stock levels",
                       "how many of this part in stock"],
    "asset_list": ["list assets", "show all machines", "asset master data", "machine list", "show assets",
                   "list of equipment"],
    "asset_info": ["tell me about machine", "details of asset", "asset information", "info about tag number",
                   "find machine", "look up tag", "show me the history of machine", "what happened to this machine"],
    "asset_count": ["how many assets", "number of machines", "count of equipment", "how many machines do we have"],
    "pm_plans": ["show pm plans", "preventive maintenance schedule", "planned maintenance list",
                 "what pm is scheduled", "active maintenance plans", "pm due"],
    "pm_compliance": ["pm compliance", "preventive maintenance completion rate", "how many pm completed",
                      "planned vs unplanned", "pm vs breakdown ratio", "pm execution rate"],
    "technician_perf": ["technician performance", "who closed the most orders", "best technician",
                        "workload per technician", "manpower", "how many technicians", "technician productivity"],
    "failure_keywords": ["common failure causes", "top failure keywords", "what are the main issues",
                         "frequent problems", "root causes", "most common faults", "why machines fail"],
    "risk": ["which machines are at risk", "predict next failure", "risk ranking", "failure risk",
             "which asset will fail next", "predictive maintenance", "high risk equipment"],
    "summary": ["give me an executive summary", "overall status", "maintenance summary", "how are we doing",
                "plant overview", "kpi summary", "management report", "recommendations",
                "what should we focus on", "action plan", "insights"],
    "performance_report": ["machine performance report", "report about machine performance", "performance by asset",
                           "asset performance table", "analyze machines", "reliability report"],
    "backlog": ["backlog age", "how old are open orders", "oldest open work order", "aging of open orders",
                "overdue work orders"],
    "app_howto": ["how do i create a work order", "how to add a spare part", "how to report a breakdown",
                  "how to save my data", "how do i upload assets", "how to generate qr code",
                  "how to resume my work", "how to create a pm plan", "how to close a job", "how to export pdf"],
}

FAQ = [
    ("how do i create a work order", "Work orders are created in two ways:\n- **📅 Planned Maintenance → Active Schedule → Generate WO** (from a PM plan)\n- **🚨 Breakdown Reporting** → describe the issue → **Report**\n\nThen manage them in **🔧 Work Orders**."),
    ("how to close a work order job technician", "Go to **🔧 Work Orders → Open Orders**, expand the order, tick the finished tasks, add spare parts used, type the technician name and click **Close Job**."),
    ("how to add spare parts used to a work order", "Open the work order in **🔧 Work Orders**, choose the part and quantity, then click **Add Part**. The cost is added to the order automatically."),
    ("how to add a new spare part or update inventory quantity", "In **📦 Inventory**: use the **Add** tab for a new part (name + cost) and the **List** tab to update an existing part's quantity."),
    ("how to import upload assets master data excel", "Open **🛠️ Asset Master Data → Upload Master Data**, choose your CSV/XLSX (Machine, Tag Number, Site, Section, Line…) and click **Import Data**."),
    ("how to generate a qr code for a machine", "Go to **🛠️ Asset Master Data → QR Generator**, select the asset and click **Generate QR**, then download the PNG."),
    ("how to create a pm plan preventive maintenance schedule", "Go to **📅 Planned Maintenance → Create Plan**, select the machine, frequency and up to 3 tasks, then **Save Plan**."),
    ("how to report a breakdown failure", "Open **🚨 Breakdown Reporting**, enter the asset and issue, and click **Report**. A high-priority work order is created automatically."),
    ("how to save my data download file resume upload backup", "Use **📥 Save Data to File** in the sidebar to download your data (including Migo chats). Next time, upload the file from the login screen or **📂 Load Data from File** in the sidebar to resume."),
    ("how to generate work order pdf print", "Open **🔧 Work Orders → History**, select a closed order, click **Generate PDF**, then **Download**."),
    ("how to download ai strategy report dashboard", "Go to **📊 Dashboard → 🤖 AI Insight & Action** (needs 5+ work orders) and click **📥 Report**."),
    ("how to change kpi targets mttr mtbf oee", "In **📊 Dashboard → ⚡ Reliability Center**, open **Configure KPI Targets** and adjust the values."),
    ("how to view notifications alerts messages", "Open **🔔 Alerts → Latest Messages** in the sidebar to see the 10 most recent events."),
    ("how to forecast opex budget", "Open **💼 Maintenance Manager** (managers only), set actuals, inflation and risk allowance to see the OPEX forecast waterfall."),
    ("how to review my previous chats with migo", "In **🤖 Migo Chatbot**, switch to **📜 Chat History** to search, review, continue, download or delete past conversations."),
]

QUICK_PROMPTS = [
    "Give me an executive summary",
    "Which machines are at highest risk?",
    "Show open work orders",
    "List low stock items",
    "Chart cost by asset",
    "Forecast next month cost",
]

FOLLOW = {
    "total_spend": ["Chart cost by asset", "Show monthly cost trend", "Forecast next month cost"],
    "cost_by_asset": ["Show monthly cost trend", "Which machines are at highest risk?", "Give me an executive summary"],
    "cost_trend": ["Forecast next month cost", "Chart cost by asset", "Total spend this month"],
    "forecast": ["Show monthly cost trend", "Give me an executive summary"],
    "open_orders": ["How old are the open orders?", "Technician performance", "Show breakdowns"],
    "closed_orders": ["Technician performance", "Total spend this month"],
    "wo_summary": ["Show open work orders", "PM compliance", "Show breakdowns"],
    "breakdown_summary": ["Bad actors", "What is the MTTR?", "Most common failure causes"],
    "bad_actors": ["Which machines are at highest risk?", "Most common failure causes", "What is the MTTR?"],
    "mttr": ["What is the MTBF?", "Show availability and OEE", "Bad actors"],
    "mtbf": ["What is the MTTR?", "Show availability and OEE", "Bad actors"],
    "availability": ["What is the MTTR?", "Give me an executive summary"],
    "low_stock": ["What is the inventory value?", "List spare parts"],
    "inventory_value": ["List low stock items", "List spare parts"],
    "inventory_list": ["List low stock items", "What is the inventory value?"],
    "asset_list": ["How many assets do we have?", "Machine performance report"],
    "asset_count": ["List assets", "Machine performance report"],
    "asset_info": ["Which machines are at highest risk?", "Chart cost by asset"],
    "pm_plans": ["PM compliance", "Show open work orders"],
    "pm_compliance": ["Show PM plans", "Give me an executive summary"],
    "technician_perf": ["Show open work orders", "Total spend this month"],
    "failure_keywords": ["Bad actors", "Which machines are at highest risk?"],
    "risk": ["Bad actors", "Most common failure causes", "Give me an executive summary"],
    "summary": ["Which machines are at highest risk?", "List low stock items", "Forecast next month cost"],
    "performance_report": ["Chart cost by asset", "Which machines are at highest risk?"],
    "backlog": ["Show open work orders", "Technician performance"],
}
DEFAULT_FOLLOW = ["Give me an executive summary", "Show open work orders", "List low stock items"]

# Regex rules: (pattern, intent) -> checked before the ML classifier (first match wins)
RULES = [
    (r"^(hi|hello|hey|salam|good (morning|afternoon|evening))\b[\s\w!.,]{0,14}$", "greeting"),
    (r"\b(thanks|thank you|thx)\b", "thanks"),
    (r"\bhow (do|can|to|should|could)\b.*\b(create|add|close|report|upload|import|save|resume|generate|download|change|set|export|view|find|review|print)\b", "app_howto"),
    (r"\bhow to\b", "app_howto"),
    (r"\bmttr\b", "mttr"),
    (r"\bmtbf\b", "mtbf"),
    (r"\boee\b|availability|uptime|downtime", "availability"),
    (r"low stock|reorder|running out|out of stock|shortage|below minimum", "low_stock"),
    (r"\bat risk\b|\brisk\b|fail next|predictive|likely to fail", "risk"),
    (r"bad actor|worst machine", "bad_actors"),
    (r"forecast|projection|predict.*(cost|spend)", "forecast"),
    (r"\b(backlog|overdue|oldest|aging|ageing)\b", "backlog"),
    (r"root cause|failure cause|keyword|common (failure|issue|problem|fault)", "failure_keywords"),
    (r"executive|summary|overview|recommend|insight|action plan|how are we doing", "summary"),
    (r"pm compliance|planned vs|pm vs", "pm_compliance"),
    (r"technician|manpower", "technician_perf"),
    (r"(trend|monthly|per month|over time|by month)", "cost_trend"),
]

STOP_WORDS = set("""a an the of for on in at to and or is are was were be been do does did me my we our you your it its this that these
those with by from as about show tell give list what which who how many much more most least top best worst first any all
please can could would should want need get find cost costs spend spent total machine machines asset assets open closed order orders
work breakdown breakdowns report chart plot graph draw status stock part parts technician technicians maintenance performance trend
monthly summary month week year today yesterday last next days day plan plans compliance risk forecast average repair time number
count value inventory items item details info information history happened""".split())


class TextMatcher:
    """TF-IDF (word 1-2 grams + char 3-5 grams) cosine-similarity matcher: tolerant to typos."""
    def __init__(self, texts, labels):
        self.labels = labels
        self.vw = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True)
        self.vc = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True)
        self.Xw = self.vw.fit_transform(texts)
        self.Xc = self.vc.fit_transform(texts)

    def rank(self, q):
        sw = cosine_similarity(self.vw.transform([q]), self.Xw)[0]
        sc = cosine_similarity(self.vc.transform([q]), self.Xc)[0]
        s = 0.5 * sw + 0.5 * sc
        best = {}
        for lab, v in zip(self.labels, s):
            best[lab] = max(best.get(lab, 0.0), float(v))
        return sorted(best.items(), key=lambda x: -x[1])


@st.cache_resource(show_spinner=False)
def get_migo_models():
    texts, labels = [], []
    for lab, exs in INTENT_EXAMPLES.items():
        for e in exs:
            texts.append(e); labels.append(lab)
    intent_m = TextMatcher(texts, labels)
    faq_m = TextMatcher([q for q, _ in FAQ], list(range(len(FAQ))))
    return intent_m, faq_m

INTENT_THRESHOLD = 0.30


# ---------- Data layer ----------
def migo_load():
    wo = load_collection("work_orders")
    inv = load_collection("spare_parts")
    assets = load_collection("assets")
    plans = load_collection("pm_plans")
    wo["created_at"] = pd.to_datetime(wo["created_at"], errors="coerce")
    wo["closed_date"] = pd.to_datetime(wo["closed_date"], errors="coerce")
    wo["total_cost"] = pd.to_numeric(wo["total_cost"], errors="coerce").fillna(0.0)
    wo["technician_name"] = wo["fixing_technician"].fillna(wo["technician"]).fillna("Unknown")
    wo["is_breakdown"] = wo["type"].astype(str).str.contains("Breakdown", case=False, na=False)
    wo["is_pm"] = wo["type"].astype(str).str.contains("PM", case=True, na=False)
    wo["repair_hrs"] = (wo["closed_date"] - wo["created_at"]).dt.total_seconds() / 3600
    inv["quantity"] = pd.to_numeric(inv["quantity"], errors="coerce").fillna(0)
    inv["cost"] = pd.to_numeric(inv["cost"], errors="coerce").fillna(0)
    return {"wo": wo, "inv": inv, "assets": assets, "plans": plans}


def migo_kpis(wo):
    k = {"mttr": 0.0, "mtbf": 0.0, "availability": 100.0, "oee": 0.0, "downtime": 0.0, "bd": 0}
    if wo.empty:
        return k
    bd = wo[wo["is_breakdown"]]
    k["bd"] = len(bd)
    closed = bd[bd["status"] == "Closed"].dropna(subset=["repair_hrs"])
    if len(closed):
        k["mttr"] = float(closed["repair_hrs"].mean())
        k["downtime"] = float(closed["repair_hrs"].sum())
    first = wo["created_at"].min()
    if pd.notna(first):
        total_h = max((datetime.now() - first).total_seconds() / 3600, 1e-9)
        if len(bd): k["mtbf"] = total_h / len(bd)
        k["availability"] = max(0.0, (total_h - k["downtime"]) / total_h * 100)
    k["oee"] = k["availability"] * 0.95 * 0.98
    return k


# ---------- Entity extraction ----------
def extract_time_range(ql):
    now = datetime.now()
    sod = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if "today" in ql: return (sod, now, "today")
    if "yesterday" in ql: return (sod - timedelta(days=1), sod, "yesterday")
    m = re.search(r"last (\d+) (day|week|month)s?", ql)
    if m:
        n = int(m.group(1)); days = n * {"day": 1, "week": 7, "month": 30}[m.group(2)]
        return (now - timedelta(days=days), now, f"last {n} {m.group(2)}s")
    if "this week" in ql: return (sod - timedelta(days=sod.weekday()), now, "this week")
    if "last week" in ql:
        s = sod - timedelta(days=sod.weekday() + 7); return (s, s + timedelta(days=7), "last week")
    if "this month" in ql: return (sod.replace(day=1), now, "this month")
    if "last month" in ql:
        e = sod.replace(day=1); s = (e - timedelta(days=1)).replace(day=1); return (s, e, "last month")
    if "this year" in ql or "ytd" in ql: return (sod.replace(month=1, day=1), now, "this year")
    if "last year" in ql:
        return (sod.replace(year=now.year - 1, month=1, day=1), sod.replace(month=1, day=1), "last year")
    return None


def extract_topn(ql, default):
    m = re.search(r"(?:top|first|worst|best|biggest|highest|bottom)\s+(\d+)", ql)
    return (int(m.group(1)), True) if m else (default, False)


def build_candidates(assets, wo):
    cand = {}
    bad = ("nan", "n/a", "unknown", "none", "")
    if not assets.empty:
        for nm, tg in zip(assets["name"].astype(str), assets["tag"].astype(str)):
            for k in (nm, tg):
                if k.strip().lower() not in bad:
                    cand.setdefault(k.strip().lower(), (nm if nm.lower() not in bad else "", tg if tg.lower() not in bad else ""))
    if not wo.empty:
        for a in wo["asset_name"].dropna().astype(str).unique():
            if a.strip().lower() not in bad:
                cand.setdefault(a.strip().lower(), (a, ""))
    return cand


def find_assets(ql, cand):
    clean = re.sub(r"[^a-z0-9\-\. ]", " ", ql)
    hits = {}
    for k, pair in cand.items():
        if len(k) >= 3 and k in clean and re.search(r"(?<![a-z0-9])" + re.escape(k) + r"(?![a-z0-9])", clean):
            hits[k] = pair
    keys = list(hits)
    hits = {k: v for k, v in hits.items() if not any(k != o and k in o for o in keys)}  # keep longest
    if not hits and cand:
        toks = clean.split()
        klist = list(cand)
        tried = 0
        for size in (1, 2, 3):
            for i in range(len(toks) - size + 1):
                win = toks[i:i + size]
                if len(" ".join(win)) < 4 or win[0] in STOP_WORDS or win[-1] in STOP_WORDS:
                    continue
                tried += 1
                if tried > 25: break
                m = difflib.get_close_matches(" ".join(win), klist, n=1, cutoff=0.85)
                if m: hits[m[0]] = cand[m[0]]
    out, seen = [], set()
    for k, (nm, tg) in hits.items():
        if (nm, tg) not in seen:
            seen.add((nm, tg)); out.append({"name": nm, "tag": tg})
    return out[:3]


def find_group_filter(ql, assets):
    out = {}
    if assets.empty: return out
    for col in ["site", "section", "line", "factory"]:
        if col in assets.columns:
            for v in assets[col].dropna().astype(str).unique():
                vl = v.lower().strip()
                if vl and vl not in ("nan", "general", "unknown") and re.search(r"(?<![a-z0-9])" + re.escape(vl) + r"(?![a-z0-9])", ql):
                    out[col] = v; break
    return out


# ---------- Response helpers ----------
def to_records(df, limit=200):
    d = df.head(limit).copy()
    for c in d.columns:
        if pd.api.types.is_datetime64_any_dtype(d[c]):
            d[c] = d[c].dt.strftime("%Y-%m-%d %H:%M")
    d = d.round(2).astype(object).where(pd.notna(d), None)
    return json.loads(json.dumps(d.to_dict("records"), default=_json_default))

def R(text, table=None, chart=None):
    return {"text": text, "table": to_records(table) if isinstance(table, pd.DataFrame) and not table.empty else None, "chart": chart}

def chart_spec(kind, labels, values, title, xlabel="", ylabel=""):
    return {"kind": kind, "labels": [str(l) for l in labels], "values": [float(v) for v in values],
            "title": title, "xlabel": xlabel, "ylabel": ylabel}

def make_fig(spec):
    k, L, V = spec["kind"], spec["labels"], spec["values"]
    if k == "pie": fig = go.Figure(go.Pie(labels=L, values=V, hole=0.45))
    elif k == "barh":
        fig = go.Figure(go.Bar(x=V, y=L, orientation="h", marker=dict(color=V, colorscale="Blues")))
        fig.update_yaxes(autorange="reversed")
    elif k == "line": fig = go.Figure(go.Scatter(x=L, y=V, mode="lines+markers", line=dict(color="#0a6ed1", width=3)))
    else: fig = go.Figure(go.Bar(x=L, y=V, marker=dict(color=V, colorscale="Blues")))
    fig.update_layout(title=spec.get("title", ""), height=360, margin=dict(l=20, r=20, t=50, b=20),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(255,255,255,0.6)",
                      xaxis_title=spec.get("xlabel", ""), yaxis_title=spec.get("ylabel", ""))
    return fig

def money(x): return f"${x:,.2f}"


def scoped_wo(ctx):
    wo = ctx["wo"]
    if wo.empty: return wo
    if ctx["time"]:
        s, e, _ = ctx["time"]
        wo = wo[(wo["created_at"] >= s) & (wo["created_at"] <= e)]
    if ctx["assets_found"]:
        pats = []
        for a in ctx["assets_found"]:
            pats += [p.lower() for p in (a["name"], a["tag"]) if p]
        names = wo["asset_name"].astype(str).str.lower()
        wo = wo[names.apply(lambda x: any(p in x for p in pats))]
    return wo

def scope_label(ctx):
    parts = []
    if ctx["time"]: parts.append(ctx["time"][2])
    if ctx["assets_found"]: parts.append("for " + ", ".join((a["name"] or a["tag"]) for a in ctx["assets_found"]))
    return f" ({', '.join(parts)})" if parts else ""

def charts_ok(ctx): return not ctx["table_only"]


# ---------- Intent handlers ----------
def h_greeting(ctx):
    wo, inv = ctx["wo"], ctx["inv"]
    n_open = int((wo["status"] == "Open").sum()) if not wo.empty else 0
    low = int((inv["quantity"] < 10).sum()) if not inv.empty else 0
    return R(f"Hello! 👋 I'm **Migo**, your maintenance AI assistant. Right now there are **{n_open} open work orders** and **{low} low-stock parts**. Ask me anything about costs, breakdowns, assets, stock, PM or technicians.")

def h_thanks(ctx): return R("You're welcome! 😊 Ask me anything else whenever you need.")

def h_help(ctx):
    return R("""I understand natural-language questions about the whole app. For example:

- 💰 **Cost:** *total spend this month*, *chart cost by asset*, *monthly cost trend*, *forecast next month cost*
- 🔧 **Work orders:** *show open work orders*, *how many work orders*, *how old are the open orders?*
- 🚨 **Reliability:** *bad actors*, *what is the MTTR / MTBF / OEE?*, *most common failure causes*, *which machines are at highest risk?*
- 🛠️ **Assets:** *list assets in Snacks*, *how many machines*, *tell me about Oven*
- 📦 **Inventory:** *low stock items*, *inventory value*, *list spare parts*
- 📅 **PM:** *PM compliance*, *show PM plans*
- 👥 **People:** *technician performance*
- 🧠 **Insights:** *executive summary*, *machine performance report*
- ❓ **How-to:** *how do I create a work order?*, *how do I save my data?*

I remember context, so you can follow up with *"and last month?"* or *"what about Oven?"*.""")

def h_total_spend(ctx):
    wo = scoped_wo(ctx)
    if wo.empty: return R("I found no work orders" + scope_label(ctx) + ".")
    total = wo["total_cost"].sum()
    cat = wo["is_breakdown"].map({True: "Breakdown", False: "PM / Other"})
    tbl = wo.groupby(cat).agg(**{"Work Orders": ("id", "count"), "Cost ($)": ("total_cost", "sum")}).reset_index().rename(columns={"is_breakdown": "Category", "type": "Category"})
    tbl.columns = ["Category", "Work Orders", "Cost ($)"]
    return R(f"Total maintenance spend{scope_label(ctx)}: **{money(total)}** across **{len(wo)}** work orders.", tbl)

def h_cost_by_asset(ctx):
    wo = scoped_wo(ctx)
    if wo.empty: return R("No cost data available" + scope_label(ctx) + ".")
    n, _ = ctx["topn"]
    g = wo.groupby("asset_name")["total_cost"].sum().sort_values(ascending=False).head(n)
    tbl = g.reset_index(); tbl.columns = ["Asset", "Cost ($)"]
    ch = chart_spec("barh", g.index, g.values, f"Top {len(g)} assets by cost{scope_label(ctx)}") if charts_ok(ctx) else None
    return R(f"**{g.index[0]}** is the biggest spender with **{money(g.iloc[0])}** ({g.iloc[0] / max(wo['total_cost'].sum(), 1e-9) * 100:.0f}% of total{scope_label(ctx)}).", tbl, ch)

def _monthly(wo):
    m = wo.dropna(subset=["created_at"]).copy()
    m["month"] = m["created_at"].dt.strftime("%Y-%m")
    return m.groupby("month")["total_cost"].sum().sort_index()

def h_cost_trend(ctx):
    g = _monthly(scoped_wo(ctx))
    if g.empty: return R("There is no dated cost data yet.")
    tbl = g.reset_index(); tbl.columns = ["Month", "Cost ($)"]
    txt = f"Monthly maintenance cost{scope_label(ctx)}: highest in **{g.idxmax()}** ({money(g.max())}), lowest in **{g.idxmin()}** ({money(g.min())}), average **{money(g.mean())}**/month."
    return R(txt, tbl, chart_spec("line", g.index, g.values, "Monthly cost trend", ylabel="$") if charts_ok(ctx) else None)

def h_forecast(ctx):
    g = _monthly(scoped_wo(ctx))
    if len(g) < 3: return R("I need at least **3 months** of work-order history to build a reliable forecast.")
    y = g.values.astype(float); x = np.arange(len(y))
    slope, intercept = np.polyfit(x, y, 1)
    trend = max(0.0, slope * len(y) + intercept); ma = float(y[-3:].mean())
    pred = (trend + ma) / 2
    nxt = str(pd.Period(g.index[-1], "M") + 1)
    tbl = pd.DataFrame({"Method": ["Linear trend", "3-month moving average", "Blended forecast"], "Next month ($)": [trend, ma, pred]})
    ch = chart_spec("line", list(g.index) + [nxt + " (forecast)"], list(y) + [pred], "Cost history & forecast", ylabel="$")
    direction = "rising 📈" if slope > 0 else "falling 📉"
    return R(f"Forecast for **{nxt}**: about **{money(pred)}** (blend of trend and 3-month average). The underlying trend is {direction} ({money(abs(slope))}/month).", tbl, ch)

def _wo_table(df, cols):
    df = df.copy()
    cols = [c for c in cols if c in df.columns]
    return df[cols].rename(columns={"custom_id": "WO #", "asset_name": "Asset", "type": "Type", "description": "Description", "status": "Status",
                                    "created_at": "Created", "closed_date": "Closed", "total_cost": "Cost ($)", "technician_name": "Technician", "age_days": "Age (days)"})

def h_open_orders(ctx):
    wo = scoped_wo(ctx)
    op = wo[wo["status"] == "Open"].copy() if not wo.empty else wo
    if op.empty: return R("🎉 No open work orders" + scope_label(ctx) + ".")
    op["age_days"] = (datetime.now() - op["created_at"]).dt.days
    op = op.sort_values("age_days", ascending=False)
    note = f" Showing the oldest {min(len(op), 25)}." if len(op) > 25 else ""
    return R(f"There are **{len(op)} open work orders**{scope_label(ctx)}. Oldest is **{int(op['age_days'].max()) if op['age_days'].notna().any() else 0} days** old.{note}",
             _wo_table(op.head(25), ["custom_id", "asset_name", "type", "description", "age_days"]))

def h_closed_orders(ctx):
    wo = scoped_wo(ctx)
    cl = wo[wo["status"] == "Closed"].sort_values("closed_date", ascending=False) if not wo.empty else wo
    if cl.empty: return R("No closed work orders" + scope_label(ctx) + " yet.")
    return R(f"**{len(cl)} closed work orders**{scope_label(ctx)} (latest 25 shown) - total cost **{money(cl['total_cost'].sum())}**.",
             _wo_table(cl.head(25), ["custom_id", "asset_name", "type", "technician_name", "closed_date", "total_cost"]))

def h_wo_summary(ctx):
    wo = scoped_wo(ctx)
    if wo.empty: return R("No work orders found" + scope_label(ctx) + ".")
    st_counts = wo["status"].replace("", "Unknown").fillna("Unknown").value_counts()
    ty = wo["is_breakdown"].map({True: "Breakdown", False: "PM / Other"}).value_counts()
    tbl = pd.DataFrame({"Group": list(st_counts.index) + list(ty.index), "Count": list(st_counts.values) + list(ty.values)})
    return R(f"**{len(wo)} work orders**{scope_label(ctx)}: " + ", ".join(f"{v} {k}" for k, v in st_counts.items()) + ". By type: " + ", ".join(f"{v} {k}" for k, v in ty.items()) + ".",
             tbl, chart_spec("pie", st_counts.index, st_counts.values, "Work orders by status") if charts_ok(ctx) else None)

def h_breakdown_summary(ctx):
    wo = scoped_wo(ctx)
    bd = wo[wo["is_breakdown"]].copy() if not wo.empty else wo
    if bd.empty: return R("No breakdowns recorded" + scope_label(ctx) + ". 👍")
    k = migo_kpis(bd)
    op = int((bd["status"] == "Open").sum())
    recent = bd.sort_values("created_at", ascending=False).head(10)
    return R(f"**{len(bd)} breakdowns**{scope_label(ctx)} ({op} still open). Total repair downtime **{k['downtime']:.1f} h**, average repair time **{k['mttr']:.1f} h**, cost **{money(bd['total_cost'].sum())}**.",
             _wo_table(recent, ["created_at", "asset_name", "description", "status", "total_cost"]))

def h_bad_actors(ctx):
    wo = scoped_wo(ctx)
    bd = wo[wo["is_breakdown"]] if not wo.empty else wo
    if bd.empty: return R("No breakdown data yet - no bad actors identified.")
    n, _ = ctx["topn"]
    g = bd.groupby("asset_name").agg(Breakdowns=("id", "count"), **{"Downtime (h)": ("repair_hrs", "sum"), "Cost ($)": ("total_cost", "sum")}).sort_values(["Breakdowns", "Downtime (h)"], ascending=False).head(n).reset_index().rename(columns={"asset_name": "Asset"})
    return R(f"🚨 Worst performer{scope_label(ctx)}: **{g.iloc[0]['Asset']}** with **{int(g.iloc[0]['Breakdowns'])} breakdowns**.",
             g, chart_spec("bar", g["Asset"], g["Breakdowns"], "Breakdowns per asset") if charts_ok(ctx) else None)

def _asset_mttr(wo):
    bd = wo[wo["is_breakdown"] & (wo["status"] == "Closed")].dropna(subset=["repair_hrs"])
    if bd.empty: return None
    return bd.groupby("asset_name")["repair_hrs"].mean().sort_values(ascending=False).head(10)

def h_mttr(ctx):
    wo = scoped_wo(ctx); k = migo_kpis(wo); tgt = st.session_state.get("target_mttr", 2.0)
    if k["mttr"] == 0: return R("There are no closed breakdowns yet, so MTTR cannot be calculated. (MTTR = *Mean Time To Repair*, average hours to fix a failure.)")
    g = _asset_mttr(wo); tbl = None
    if g is not None: tbl = g.reset_index(); tbl.columns = ["Asset", "MTTR (h)"]
    status = "✅ within target" if k["mttr"] <= tgt else "⚠️ above target"
    return R(f"**MTTR{scope_label(ctx)}: {k['mttr']:.2f} h** (target {tgt:.1f} h) - {status}. *MTTR = average hours to repair a breakdown.*", tbl)

def h_mtbf(ctx):
    wo = scoped_wo(ctx); k = migo_kpis(wo); tgt = st.session_state.get("target_mtbf", 200.0)
    if k["mtbf"] == 0: return R("No breakdowns recorded, so MTBF cannot be calculated. (MTBF = *Mean Time Between Failures*.)")
    status = "✅ meets target" if k["mtbf"] >= tgt else "⚠️ below target"
    return R(f"**MTBF{scope_label(ctx)}: {k['mtbf']:.1f} h** (target {tgt:.0f} h) - {status}, based on **{k['bd']}** breakdowns. *Higher is better.*")

def h_availability(ctx):
    wo = scoped_wo(ctx); k = migo_kpis(wo); tgt = st.session_state.get("target_oee", 85.0)
    tbl = pd.DataFrame({"KPI": ["Availability %", "OEE % (estimated)", "Repair downtime (h)", "OEE target %"], "Value": [k["availability"], k["oee"], k["downtime"], tgt]})
    status = "✅ on target" if k["oee"] >= tgt else "⚠️ below target"
    return R(f"Availability **{k['availability']:.1f}%**, estimated OEE **{k['oee']:.1f}%** ({status}), total repair downtime **{k['downtime']:.1f} h**.", tbl)

def h_low_stock(ctx):
    inv = ctx["inv"]
    if inv.empty: return R("No spare parts in inventory yet.")
    m = re.search(r"(?:below|under|less than|<)\s*(\d+)", ctx["ql"]); thr = int(m.group(1)) if m else 10
    low = inv[inv["quantity"] < thr].sort_values("quantity")
    if low.empty: return R(f"✅ Inventory is healthy - no parts below {thr} units.")
    cols = [c for c in ["name", "sku", "quantity", "location", "cost"] if c in low.columns]
    return R(f"⚠️ **{len(low)} parts** are below **{thr} units**. Most critical: **{low.iloc[0]['name']}** ({low.iloc[0]['quantity']:g} left).",
             low[cols].rename(columns={"name": "Part", "sku": "SKU", "quantity": "Qty", "location": "Location", "cost": "Unit Cost ($)"}))

def h_inventory_value(ctx):
    inv = ctx["inv"]
    if inv.empty: return R("No spare parts in inventory yet.")
    inv = inv.copy(); inv["value"] = inv["quantity"] * inv["cost"]
    top = inv.sort_values("value", ascending=False).head(5)[["name", "quantity", "cost", "value"]]
    top.columns = ["Part", "Qty", "Unit Cost ($)", "Value ($)"]
    return R(f"Total inventory value: **{money(inv['value'].sum())}** across **{len(inv)}** parts. Top 5 by value below.", top)

def h_inventory_list(ctx):
    inv = ctx["inv"]
    if inv.empty: return R("No spare parts in inventory yet.")
    names = {str(n).lower(): n for n in inv["name"].astype(str)}
    hit = [orig for low, orig in names.items() if len(low) >= 3 and low in ctx["ql"]]
    if not hit:
        cm = [m for t in ctx["ql"].split() for m in difflib.get_close_matches(t, list(names), n=1, cutoff=0.85) if len(t) >= 4]
        hit = [names[m] for m in cm]
    if hit:
        sel = inv[inv["name"].isin(hit)]
        cols = [c for c in ["name", "sku", "quantity", "cost", "location"] if c in sel.columns]
        return R(f"Found **{len(sel)}** matching part(s).", sel[cols])
    cols = [c for c in ["name", "sku", "quantity", "cost", "location"] if c in inv.columns]
    return R(f"**{len(inv)} spare parts** in stock (showing up to 200).", inv[cols])

def h_asset_list(ctx):
    a = ctx["assets"]
    if a.empty: return R("No assets stored yet. Import them from **🛠️ Asset Master Data**.")
    f = a
    for col, v in ctx["group"].items(): f = f[f[col].astype(str).str.lower() == v.lower()]
    cols = [c for c in ["name", "tag", "factory", "site", "section", "line"] if c in f.columns]
    lab = " in " + ", ".join(ctx["group"].values()) if ctx["group"] else ""
    return R(f"**{len(f)} assets**{lab} (showing up to 200).", f[cols].rename(columns=str.title))

def h_asset_count(ctx):
    a = ctx["assets"]
    if a.empty: return R("No assets stored yet.")
    f = a
    for col, v in ctx["group"].items(): f = f[f[col].astype(str).str.lower() == v.lower()]
    lab = " in " + ", ".join(ctx["group"].values()) if ctx["group"] else ""
    tbl, ch = None, None
    if "site" in f.columns and not ctx["group"]:
        g = f["site"].astype(str).value_counts()
        tbl = g.reset_index(); tbl.columns = ["Site", "Assets"]
        ch = chart_spec("pie", g.index, g.values, "Assets by site") if charts_ok(ctx) else None
    return R(f"There are **{len(f)} assets**{lab}.", tbl, ch)

def h_asset_info(ctx):
    if not ctx["assets_found"]:
        ex = ctx["assets"]["name"].dropna().astype(str).head(3).tolist() if not ctx["assets"].empty else []
        return R("Which machine do you mean? Give me a machine name or tag number" + (f", e.g. *{', '.join(ex)}*." if ex else "."))
    a = ctx["assets_found"][0]; df = ctx["assets"]
    row = None
    if not df.empty:
        m = df[(df["name"].astype(str).str.lower() == a["name"].lower()) | (df["tag"].astype(str).str.lower() == a["tag"].lower())]
        if not m.empty: row = m.iloc[0]
    wo = scoped_wo(ctx)
    lines = [f"**{a['name'] or a['tag']}**" + (f" - tag `{a['tag']}`" if a["tag"] else "")]
    if row is not None:
        det = [f"{c.title()}: {row[c]}" for c in ["factory", "site", "section", "line"] if c in row.index and pd.notna(row[c]) and str(row[c]) != ""]
        if det: lines.append(" | ".join(det))
    if wo.empty:
        lines.append("No work orders recorded for this asset yet.")
        return R("\n\n".join(lines))
    bd = wo[wo["is_breakdown"]]
    lines.append(f"🔧 **{len(wo)}** work orders ({int((wo['status'] == 'Open').sum())} open) · 🚨 **{len(bd)}** breakdowns · 💰 **{money(wo['total_cost'].sum())}** total cost")
    mt = bd[bd["status"] == "Closed"]["repair_hrs"].mean() if not bd.empty else np.nan
    if pd.notna(mt): lines.append(f"⏱️ Average repair time **{mt:.1f} h**")
    return R("\n\n".join(lines), _wo_table(wo.sort_values("created_at", ascending=False).head(10), ["custom_id", "type", "description", "status", "created_at", "total_cost"]))

def h_pm_plans(ctx):
    p = ctx["plans"]
    if p.empty: return R("No PM plans yet. Create one in **📅 Planned Maintenance**.")
    t = p.copy(); t["Tasks"] = t["tasks_data"].apply(lambda x: len(safe_list(x))) if "tasks_data" in t.columns else 0
    cols = [c for c in ["asset_name", "frequency", "next_due", "status", "Tasks"] if c in t.columns]
    return R(f"**{len(p)} PM plans** are stored.", t[cols].rename(columns={"asset_name": "Asset", "frequency": "Frequency", "next_due": "Next Due", "status": "Status"}))

def h_pm_compliance(ctx):
    wo = scoped_wo(ctx)
    pm = wo[wo["is_pm"]] if not wo.empty else wo
    if pm.empty: return R("No PM work orders yet, so compliance cannot be calculated.")
    done = int((pm["status"] == "Closed").sum()); rate = done / len(pm) * 100
    bd = int(wo["is_breakdown"].sum())
    txt = f"**PM compliance{scope_label(ctx)}: {rate:.1f}%** ({done} of {len(pm)} PM orders closed). Planned vs unplanned: **{len(pm)} PM** vs **{bd} breakdowns**."
    txt += " ⚠️ Below the 90% goal." if rate < 90 else " ✅ Meets the 90% goal."
    return R(txt, chart=chart_spec("pie", ["Completed", "Pending"], [done, len(pm) - done], "PM execution") if charts_ok(ctx) else None)

def h_technician_perf(ctx):
    wo = scoped_wo(ctx)
    if wo.empty: return R("No work-order data yet.")
    cl = wo[wo["status"] == "Closed"]
    if cl.empty: return R(f"There are **{wo['technician_name'].nunique()}** technician(s) recorded but no closed orders yet.")
    g = cl.groupby("technician_name").agg(**{"Orders Closed": ("id", "count"), "Avg Repair (h)": ("repair_hrs", "mean"), "Cost Handled ($)": ("total_cost", "sum")}).sort_values("Orders Closed", ascending=False).reset_index().rename(columns={"technician_name": "Technician"})
    return R(f"🏆 Top technician{scope_label(ctx)}: **{g.iloc[0]['Technician']}** with **{int(g.iloc[0]['Orders Closed'])}** closed orders. **{len(g)}** technicians have closed orders.",
             g, chart_spec("barh", g["Technician"], g["Orders Closed"], "Orders closed per technician") if charts_ok(ctx) else None)

def h_failure_keywords(ctx):
    wo = scoped_wo(ctx)
    txt = [t for t in (wo[wo["is_breakdown"]]["description"].dropna().tolist() if not wo.empty else []) if isinstance(t, str) and t.strip()]
    if len(txt) < 2: return R("I need at least 2 breakdown descriptions to extract failure keywords.")
    try:
        vec = TfidfVectorizer(stop_words="english", max_features=20)
        X = vec.fit_transform(txt)
        s = pd.Series(np.asarray(X.mean(axis=0)).ravel(), index=vec.get_feature_names_out()).sort_values(ascending=False).head(8)
    except Exception:
        return R("The breakdown descriptions do not contain enough text to analyse.")
    tbl = s.reset_index(); tbl.columns = ["Keyword", "Weight"]
    return R(f"🔎 NLP analysis of **{len(txt)}** breakdown descriptions - top failure keyword: **{s.index[0]}**.", tbl,
             chart_spec("barh", s.index, s.values, "Top failure keywords (TF-IDF)") if charts_ok(ctx) else None)

def h_risk(ctx):
    wo = ctx["wo"]
    bd = wo[wo["is_breakdown"]].copy() if not wo.empty else wo
    if bd.empty: return R("I need breakdown history to estimate failure risk.")
    bd["age_days"] = (datetime.now() - bd["created_at"]).dt.days.clip(lower=0).fillna(365)
    bd["w"] = np.exp(-bd["age_days"] / 30.0)
    g = bd.groupby("asset_name").agg(Breakdowns=("id", "count"), Recent=("w", "sum"), Downtime=("repair_hrs", "sum")).fillna(0)
    nz = lambda s: s / s.max() if s.max() > 0 else s * 0
    g["Risk Score"] = (100 * (0.5 * nz(g["Recent"]) + 0.3 * nz(g["Breakdowns"]) + 0.2 * nz(g["Downtime"]))).round(0)
    g["Level"] = g["Risk Score"].apply(lambda v: "🔴 High" if v >= 70 else "🟠 Medium" if v >= 40 else "🟢 Low")
    n, _ = ctx["topn"]
    g = g.sort_values("Risk Score", ascending=False).head(n).reset_index().rename(columns={"asset_name": "Asset", "Downtime": "Downtime (h)"})
    return R(f"⚠️ Highest failure risk: **{g.iloc[0]['Asset']}** (score {int(g.iloc[0]['Risk Score'])}/100). *Score = 50% recent-failure weight (30-day decay) + 30% frequency + 20% downtime.*",
             g[["Asset", "Breakdowns", "Downtime (h)", "Risk Score", "Level"]], chart_spec("bar", g["Asset"], g["Risk Score"], "Failure risk score") if charts_ok(ctx) else None)

def h_backlog(ctx):
    wo = scoped_wo(ctx)
    op = wo[wo["status"] == "Open"].copy() if not wo.empty else wo
    if op.empty: return R("🎉 No open work orders - the backlog is empty.")
    op["age_days"] = (datetime.now() - op["created_at"]).dt.days
    avg = op["age_days"].mean(); old = int((op["age_days"] > 14).sum())
    return R(f"Backlog{scope_label(ctx)}: **{len(op)}** open orders, average age **{avg:.1f} days**, **{old}** older than 14 days. Oldest 10 below.",
             _wo_table(op.sort_values("age_days", ascending=False).head(10), ["custom_id", "asset_name", "type", "age_days"]))

def h_performance_report(ctx):
    wo = scoped_wo(ctx)
    if wo.empty: return R("I need more work-order data to generate a performance report.")
    costs = wo.groupby("asset_name")["total_cost"].sum()
    bds = wo[wo["is_breakdown"]]["asset_name"].value_counts()
    cl = wo[wo["is_breakdown"] & (wo["status"] == "Closed")].dropna(subset=["repair_hrs"])
    mt = cl.groupby("asset_name")["repair_hrs"].mean() if not cl.empty else pd.Series(dtype=float)
    rep = pd.DataFrame({"Total Cost ($)": costs, "Breakdowns": bds, "MTTR (h)": mt}).fillna(0).sort_values("Total Cost ($)", ascending=False).head(25)
    rep.index.name = "Asset"
    return R(f"📋 Machine performance report{scope_label(ctx)} - **{len(rep)}** assets ranked by cost.", rep.reset_index())

def h_summary(ctx):
    wo, inv = ctx["wo"], ctx["inv"]
    if wo.empty: return R("There is no work-order data yet. Add PM plans or report breakdowns and I'll build a full executive summary.")
    k = migo_kpis(wo); tm = st.session_state.get("target_mttr", 2.0); to = st.session_state.get("target_oee", 85.0)
    pm = wo[wo["is_pm"]]; rate = (pm["status"] == "Closed").mean() * 100 if len(pm) else 0
    low = int((inv["quantity"] < 10).sum()) if not inv.empty else 0
    op = wo[wo["status"] == "Open"]; avg_age = (datetime.now() - op["created_at"]).dt.days.mean() if len(op) else 0
    top = wo.groupby("asset_name")["total_cost"].sum().sort_values(ascending=False)
    bad = wo[wo["is_breakdown"]]["asset_name"].value_counts()
    acts = []
    if k["mttr"] > 5: acts.append(f"🔴 **MTTR is critical** ({k['mttr']:.1f} h) - review repair procedures and spares.")
    elif k["mttr"] > tm: acts.append(f"🟠 MTTR ({k['mttr']:.1f} h) is above the {tm:.1f} h target.")
    else: acts.append("🟢 MTTR is within target.")
    if len(pm) and rate < 90: acts.append(f"🟠 PM compliance is only **{rate:.0f}%** - close overdue PM orders.")
    if low: acts.append(f"🔴 **{low} spare parts** are low on stock - reorder.")
    if avg_age and avg_age > 14: acts.append(f"🟠 Open orders average **{avg_age:.0f} days** old - clear the backlog.")
    if len(bad): acts.append(f"🚨 Bad actor: **{bad.index[0]}** ({int(bad.iloc[0])} breakdowns) - consider root-cause analysis.")
    if len(top): acts.append(f"💰 Top cost driver: **{top.index[0]}** ({money(top.iloc[0])}).")
    tbl = pd.DataFrame({"KPI": ["Total spend ($)", "Work orders", "Open orders", "Breakdowns", "MTTR (h)", "MTBF (h)", "OEE % (est.)", "PM compliance %", "Low-stock parts"],
                        "Value": [wo["total_cost"].sum(), len(wo), len(op), k["bd"], k["mttr"], k["mtbf"], k["oee"], rate, low]})
    return R("### 🧠 Executive Summary\n" + "\n".join(f"- {a}" for a in acts) + f"\n\n*OEE target {to:.0f}% · current estimate {k['oee']:.1f}%.*", tbl)

def h_howto(ctx):
    rank = ctx["faq_m"].rank(ctx["ql"])
    idx, score = rank[0]
    if score < 0.15: return R("I couldn't find a matching guide. Try *how do I create a work order?*, *how do I import assets?* or *how do I save my data?*")
    return R(FAQ[idx][1])

HANDLERS = {
    "greeting": h_greeting, "thanks": h_thanks, "help": h_help, "total_spend": h_total_spend, "cost_by_asset": h_cost_by_asset,
    "cost_trend": h_cost_trend, "forecast": h_forecast, "open_orders": h_open_orders, "closed_orders": h_closed_orders,
    "wo_summary": h_wo_summary, "breakdown_summary": h_breakdown_summary, "bad_actors": h_bad_actors, "mttr": h_mttr,
    "mtbf": h_mtbf, "availability": h_availability, "low_stock": h_low_stock, "inventory_value": h_inventory_value,
    "inventory_list": h_inventory_list, "asset_list": h_asset_list, "asset_count": h_asset_count, "asset_info": h_asset_info,
    "pm_plans": h_pm_plans, "pm_compliance": h_pm_compliance, "technician_perf": h_technician_perf,
    "failure_keywords": h_failure_keywords, "risk": h_risk, "backlog": h_backlog, "performance_report": h_performance_report,
    "summary": h_summary, "app_howto": h_howto,
}


# ---------- Optional LLM (free-form questions) ----------
def get_migo_api_key():
    k = st.session_state.get("migo_api_key", "")
    if k: return k
    try:
        return st.secrets.get("ANTHROPIC_API_KEY", "")
    except Exception:
        return ""

def migo_llm(q, history, data, model):
    import requests
    wo, inv, assets = data["wo"], data["inv"], data["assets"]
    k = migo_kpis(wo)
    ctx = {
        "kpis": {a: round(b, 2) for a, b in k.items()},
        "work_orders": {"total": len(wo), "open": int((wo["status"] == "Open").sum()) if not wo.empty else 0,
                        "total_cost": round(float(wo["total_cost"].sum()), 2) if not wo.empty else 0},
        "top_cost_assets": wo.groupby("asset_name")["total_cost"].sum().sort_values(ascending=False).head(10).round(2).to_dict() if not wo.empty else {},
        "breakdowns_by_asset": wo[wo["is_breakdown"]]["asset_name"].value_counts().head(10).to_dict() if not wo.empty else {},
        "low_stock": inv[inv["quantity"] < 10][["name", "quantity"]].head(15).to_dict("records") if not inv.empty else [],
        "assets_count": len(assets),
        "pm_plans": len(data["plans"]),
    }
    system = ("You are Migo, the AI maintenance assistant inside the Americana ERP - PM Module (Americana Foods, EP Plants). "
              "Answer using ONLY the plant data provided plus general maintenance-engineering knowledge (CMMS, PM, MTTR, MTBF, OEE, spare parts, reliability). "
              "If a question is outside maintenance or this app, politely decline. If the needed data is not provided, say so. Be concise, use short bullets and numbers.")
    msgs = [{"role": m["role"], "content": m.get("text") or ""} for m in history[-6:] if m.get("text")]
    msgs.append({"role": "user", "content": f"PLANT DATA (JSON):\n{json.dumps(ctx, default=_json_default)}\n\nQUESTION: {q}"})
    r = requests.post("https://api.anthropic.com/v1/messages",
                      headers={"x-api-key": get_migo_api_key(), "anthropic-version": "2023-06-01", "content-type": "application/json"},
                      json={"model": model, "max_tokens": 700, "system": system, "messages": msgs}, timeout=45)
    r.raise_for_status()
    return "".join(b.get("text", "") for b in r.json().get("content", []))


# ---------- Orchestrator ----------
def migo_process(q, prev, data, history, model):
    intent_m, faq_m = get_migo_models()
    ql = q.lower().strip()
    cand = build_candidates(data["assets"], data["wo"])
    found = find_assets(ql, cand)
    if not found and prev.get("assets") and re.search(r"\b(it|its|this machine|that machine|this asset|same machine|same asset|them)\b", ql):
        found = prev["assets"]
    time_r = extract_time_range(ql)
    topn, topn_explicit = extract_topn(ql, 10)
    group = find_group_filter(ql, data["assets"])
    want_chart = bool(re.search(r"\b(chart|plot|graph|draw|visuali[sz]e|diagram)\b", ql))
    table_only = bool(re.search(r"\b(table|list)\b", ql)) and not want_chart

    intent, conf, engine = None, 0.0, "NLP"
    for pat, lab in RULES:
        if re.search(pat, ql):
            intent, conf, engine = lab, 1.0, "Rule"; break
    ranking = intent_m.rank(ql)
    if intent is None and ranking and ranking[0][1] >= INTENT_THRESHOLD:
        intent, conf = ranking[0]
    # context memory for follow-ups ("and last month?", "what about Oven?")
    reused = False
    carry_ok = prev.get("intent") not in (None, "greeting", "thanks", "help", "unknown", "app_howto", "llm_freeform", "error")
    has_entity = bool(found or time_r or group or want_chart or topn_explicit)
    short = len(ql.split()) <= 6
    if carry_ok and has_entity and short and engine != "Rule" and conf < 0.6:
        intent, conf, engine, reused = prev["intent"], 0.5, "Context", True
    if intent is None and found:
        intent, conf = "asset_info", 0.5
    # a recognised machine with a generic "asset info"-like intent
    if intent in ("asset_list", "asset_count") and found and not group: intent = "asset_info"

    ctx = {"q": q, "ql": ql, "wo": data["wo"], "inv": data["inv"], "assets": data["assets"], "plans": data["plans"],
           "assets_found": found, "time": time_r, "topn": (topn, topn_explicit), "group": group,
           "chart": want_chart, "table_only": table_only, "faq_m": faq_m}
    if intent is None:
        key = get_migo_api_key()
        if key:
            try:
                txt = migo_llm(q, history, data, model)
                return R(txt), "llm_freeform", 0.0, {"intent": prev.get("intent"), "assets": found or prev.get("assets", [])}, "LLM"
            except Exception as e:
                err = f"\n\n*(LLM unavailable: {str(e)[:80]})*"
        else:
            err = ""
        sugg = [INTENT_EXAMPLES[l][0].capitalize() for l, s in ranking[:3] if s > 0.05 and l in INTENT_EXAMPLES]
        txt = "I'm not sure I understood that. I can help with costs, work orders, breakdowns, assets, inventory, PM plans and technicians."
        if sugg: txt += "\n\nDid you mean:\n" + "\n".join(f"- *{s}*" for s in sugg)
        txt += "\n\nType **help** to see everything I can do." + err
        resp = R(txt); resp["follow"] = sugg or DEFAULT_FOLLOW
        return resp, "unknown", 0.0, {"intent": prev.get("intent"), "assets": prev.get("assets", [])}, "NLP"

    resp = HANDLERS[intent](ctx)
    resp["follow"] = FOLLOW.get(intent, DEFAULT_FOLLOW)
    return resp, intent, conf, {"intent": intent, "assets": found}, engine


# --- MODULE 1: DASHBOARD ---
if menu == "📊 Dashboard":
    st.title("📊 Enterprise Performance Hub")

    # 1. Initialize KPI Targets
    if 'target_mttr' not in st.session_state:
        st.session_state.target_mttr = 2.0
    if 'target_mtbf' not in st.session_state:
        st.session_state.target_mtbf = 200.0
    if 'target_oee' not in st.session_state:
        st.session_state.target_oee = 85.0

    # 2. Helpers
    def clean_text(text):
        if not isinstance(text, str): return str(text)
        return text.encode('latin-1', 'replace').decode('latin-1')

    def create_strategy_pdf(action_items, general_stats, ml_findings):
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Arial", 'B', 16)
        pdf.cell(0, 10, clean_text("AI Maintenance Strategy Report"), 0, 1, 'C')
        pdf.set_font("Arial", 'I', 10)
        pdf.cell(0, 10, clean_text(f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M')}"), 0, 1, 'C')
        pdf.line(10, 30, 200, 30); pdf.ln(10)

        pdf.set_font("Arial", 'B', 12)
        pdf.cell(0, 10, clean_text("1. Plant KPI Snapshot"), 0, 1, 'L')
        pdf.set_font("Arial", '', 10)
        for key, value in general_stats.items():
            pdf.cell(0, 6, clean_text(f" - {key}: {value}"), 0, 1)
        pdf.ln(5)

        pdf.set_font("Arial", 'B', 12)
        pdf.cell(0, 10, clean_text("2. Machine Learning Insights"), 0, 1, 'L')
        pdf.set_font("Arial", '', 10)
        pdf.multi_cell(0, 6, clean_text(f"Top Failure Keyword: {ml_findings['nlp_keyword']}"))
        pdf.multi_cell(0, 6, clean_text(f"Primary Cost Driver: {ml_findings['cost_driver']}"))
        pdf.ln(5)

        pdf.set_font("Arial", 'B', 12)
        pdf.cell(0, 10, clean_text("3. Recommended Action Plan"), 0, 1, 'L')
        pdf.set_font("Arial", '', 10)
        for item in action_items:
            clean_markdown = item.replace('**', '').replace('🔴', '[CRITICAL] ').replace('🟠', '[WARN] ').replace('🟢', '[OK] ').replace('ℹ️', '[INFO] ')
            pdf.multi_cell(0, 6, clean_text(clean_markdown))
            pdf.ln(2)
        return pdf.output(dest='S').encode('latin-1', 'replace')

    # 3. Load Data
    df_wo = load_collection('work_orders')
    df_inv = load_collection('spare_parts')

    # 4. Pre-process
    if not df_wo.empty:
        df_wo['created_at'] = pd.to_datetime(df_wo['created_at'], errors='coerce')
        df_wo['closed_date'] = pd.to_datetime(df_wo.get('closed_date'), errors='coerce')
        df_wo['technician'] = df_wo['fixing_technician'].fillna(df_wo['technician']).fillna('Unknown')
        df_wo['total_cost'] = pd.to_numeric(df_wo['total_cost'], errors='coerce').fillna(0.0)

    # 5. KPIs
    total_spend = df_wo['total_cost'].sum() if not df_wo.empty else 0
    active_wo = len(df_wo[df_wo['status'] == 'Open']) if not df_wo.empty else 0
    if not df_wo.empty:
        df_bd = df_wo[df_wo['type'].str.contains('Breakdown', case=False, na=False)]
    else: df_bd = pd.DataFrame()
    bd_count = len(df_bd)

    if not df_inv.empty:
        df_inv['quantity'] = pd.to_numeric(df_inv['quantity'], errors='coerce').fillna(0)
        df_inv['cost'] = pd.to_numeric(df_inv.get('cost', df_inv.get('unit_cost', 0)), errors='coerce').fillna(0)
        low_stock = len(df_inv[df_inv['quantity'] < 10])
        inv_value = (df_inv['quantity'] * df_inv['cost']).sum()
    else: low_stock = 0; inv_value = 0.0

    avg_age = 0
    if not df_wo.empty:
        open_orders = df_wo[df_wo['status'] == 'Open'].copy()
        if not open_orders.empty:
            open_orders['age'] = (datetime.now() - open_orders['created_at']).dt.days
            avg_age = open_orders['age'].mean()
            if pd.isna(avg_age): avg_age = 0

    active_machines = df_wo[df_wo['status'] == 'Open']['asset_name'].nunique() if not df_wo.empty else 0
    manpower = df_wo['technician'].nunique() if not df_wo.empty else 0

    # 6. Render
    st.markdown("""<style>div.css-1r6slb0.e1tzin5v2 { background-color: #FFFFFF; border: 1px solid #E0E0E0; padding: 20px; border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); } div[data-testid="stMetricValue"] { font-size: 28px; color: #2c3e50; font-weight: 700; } div[data-testid="stMetricLabel"] { font-size: 14px; color: #7f8c8d; }</style>""", unsafe_allow_html=True)

    c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
    with c1: st.metric("💰 Total Spend", f"${total_spend:,.0f}", delta="YTD Cost")
    with c2: st.metric("🔨 Open Orders", active_wo, delta=f"{len(df_wo) if not df_wo.empty else 0} Total", delta_color="inverse")
    with c3: st.metric("🚨 Breakdowns", bd_count, delta="Unplanned Events", delta_color="inverse")
    with c4: st.metric("💵 Inventory Value", f"${inv_value:,.0f}", delta="Total Value")
    with c5: st.metric("📉 Low Stock", low_stock, delta="Spare Parts", delta_color="inverse")
    with c6: st.metric("⚙️ Active Machines", active_machines, delta="With Open WO")
    with c7: st.metric("👥 Manpower", manpower, delta="Technicians")

    st.markdown("---")

    tab_rel, tab_eff, tab_tech, tab_pm, tab_cost, tab_ai = st.tabs(["⚡ Reliability Center", "📈 Efficiency & Mix", "👥 Technician Performance", "📅 PM Compliance", "💼 Cost Analytics", "🤖 AI Insight & Action"])

    mttr_val, mtbf_val, oee_val = 0, 0, 85.0
    if not df_bd.empty:
        df_closed_bd = df_bd[df_bd['status'] == 'Closed'].copy()
        if not df_closed_bd.empty:
            df_closed_bd = df_closed_bd.dropna(subset=['closed_date'])
            df_closed_bd['hours'] = (df_closed_bd['closed_date'] - df_closed_bd['created_at']).dt.total_seconds() / 3600
            mttr_val = df_closed_bd['hours'].mean()
            if pd.isna(mttr_val): mttr_val = 0

        first_date = df_wo['created_at'].min() if not df_wo.empty else datetime.now()
        if pd.isna(first_date): first_date = datetime.now()
        total_hours = (datetime.now() - first_date).total_seconds() / 3600
        if len(df_bd) > 0: mtbf_val = total_hours / len(df_bd)
        uptime_hours = total_hours - (df_closed_bd['hours'].sum() if not df_closed_bd.empty else 0)
        availability = (uptime_hours / total_hours) if total_hours > 0 else 1.0
        oee_val = availability * 0.95 * 0.98 * 100

    with tab_rel:
        with st.expander("⚙️ Configure KPI Targets (Click to Adjust)", expanded=False):
            t_col1, t_col2, t_col3 = st.columns(3)
            st.session_state.target_mttr = t_col1.number_input("MTTR Target (Hrs)", value=st.session_state.target_mttr, step=0.5)
            st.session_state.target_mtbf = t_col2.number_input("MTBF Target (Hrs)", value=st.session_state.target_mtbf, step=10.0)
            st.session_state.target_oee = t_col3.number_input("OEE Target (%)", value=st.session_state.target_oee, step=1.0)

        st.subheader("Asset Reliability Metrics")
        def plot_gauge(value, title, max_val, color_hex):
            return go.Figure(go.Indicator(
                mode = "gauge+number", value = value,
                title = {'text': title, 'font': {'size': 20, 'color': "#000000", 'family': "Arial"}},
                number = {'font': {'color': "#2c3e50", 'size': 70}},
                gauge = {'axis': {'range': [None, max_val], 'tickwidth': 1, 'tickcolor': "#333333"}, 'bar': {'color': color_hex}, 'bgcolor': "white", 'borderwidth': 0, 'bordercolor': "#333333", 'steps': [{'range': [0, max_val*0.7], 'color': "#f0f0f0"}, {'range': [max_val*0.7, max_val], 'color': "#e0e0e0"}]}
            )).update_layout(height=250, margin=dict(l=20,r=20,t=50,b=20), paper_bgcolor="rgba(0,0,0,0)", font={'family': "Arial"})

        col_g1, col_g2, col_g3 = st.columns(3)
        with col_g1: st.plotly_chart(plot_gauge(mttr_val, "MTTR (Avg Hours)", 10, "#00b894" if mttr_val <= st.session_state.target_mttr else "#d63031"), use_container_width=True)
        with col_g2: st.plotly_chart(plot_gauge(mtbf_val, "MTBF (Avg Hours)", 500, "#00b894" if mtbf_val >= st.session_state.target_mtbf else "#d63031"), use_container_width=True)
        with col_g3: st.plotly_chart(plot_gauge(oee_val, "OEE Availability %", 100, "#00b894" if oee_val >= st.session_state.target_oee else "#d63031"), use_container_width=True)

        st.divider()
        st.markdown("### 🚜 Machine Comparison & Bad Actors")
        if not df_bd.empty:
            df_bd_closed = df_bd[df_bd['status'] == 'Closed'].copy()
            if not df_bd_closed.empty:
                df_bd_closed = df_bd_closed.dropna(subset=['closed_date'])
                df_bd_closed['downtime_hrs'] = (df_bd_closed['closed_date'] - df_bd_closed['created_at']).dt.total_seconds() / 3600
                asset_stats = df_bd_closed.groupby('asset_name').agg(Total_Downtime=('downtime_hrs', 'sum'), Breakdown_Count=('id', 'count'), Total_Repair_Cost=('total_cost', 'sum')).reset_index()
                c_rel1, c_rel2 = st.columns(2)
                with c_rel1: st.plotly_chart(px.bar(asset_stats, x='asset_name', y='Total_Downtime', color='Breakdown_Count', title="Total Downtime (Hrs)", color_continuous_scale='Reds'), use_container_width=True)
                with c_rel2: st.plotly_chart(px.scatter(asset_stats, x='Breakdown_Count', y='Total_Repair_Cost', size='Total_Downtime', color='asset_name', hover_name='asset_name', title="Cost vs Frequency"), use_container_width=True)
            else: st.info("No closed breakdown data.")
        else: st.info("No breakdowns recorded.")

    with tab_eff:
        c_eff1, c_eff2 = st.columns(2)
        with c_eff1:
            pm_count = len(df_wo[df_wo['type'].str.contains('PM', case=False, na=False)]) if not df_wo.empty else 0
            cm_count = len(df_wo[df_wo['type'].str.contains('Breakdown', case=False, na=False)]) if not df_wo.empty else 0
            if pm_count + cm_count > 0: st.plotly_chart(go.Figure(data=[go.Pie(labels=['PM', 'Breakdown'], values=[pm_count, cm_count], hole=.5, marker=dict(colors=['#0984e3', '#d63031']))]).update_layout(height=300), use_container_width=True)
            else: st.info("Not enough data.")
        with c_eff2:
            st.markdown("##### ⏳ Avg Age of Open Orders (Days)")
            current_color = "#2ecc71" if avg_age <= 7 else "#f39c12" if avg_age <= 14 else "#e74c3c"
            st.plotly_chart(go.Figure(go.Indicator(mode="gauge+number", value=avg_age, title={'text': "Backlog Aging"}, number={'font': {'size': 48, 'color': current_color}}, gauge={'axis': {'range': [None, 30]}, 'bar': {'color': current_color}})).update_layout(height=250), use_container_width=True)

    with tab_tech:
        if not df_wo.empty:
            tech_perf = df_wo[df_wo['status'] == 'Closed']['technician'].value_counts().reset_index()
            tech_perf.columns = ['Technician', 'Orders Closed']
            if not tech_perf.empty: st.plotly_chart(px.bar(tech_perf, x='Orders Closed', y='Technician', orientation='h', text='Orders Closed', color='Orders Closed', color_continuous_scale='Blues'), use_container_width=True)
            else: st.info("No closed orders.")
        else: st.info("No data.")

    with tab_pm:
        c_pm1, c_pm2 = st.columns([1, 2])
        if not df_wo.empty: df_pm = df_wo[df_wo['type'].str.contains('PM', case=False, na=False)]
        else: df_pm = pd.DataFrame(columns=['status'])
        pm_closed = len(df_pm[df_pm['status'] == 'Closed'])
        pm_open = len(df_pm[df_pm['status'] == 'Open'])
        pm_rate = (pm_closed/len(df_pm)*100 if len(df_pm)>0 else 0)
        with c_pm1: st.metric("Total PMs", len(df_pm)); st.metric("Completion Rate", f"{pm_rate:.1f}%")
        with c_pm2:
            if len(df_pm) > 0: st.plotly_chart(go.Figure(data=[go.Pie(labels=['Completed', 'Pending'], values=[pm_closed, pm_open], hole=.7, marker=dict(colors=['#00b894', '#fdcb6e']))]).update_layout(title_text="PM Execution Ratio", height=300), use_container_width=True)
            else: st.info("No PM Data.")

    with tab_cost:
        col_bar1, col_bar2 = st.columns(2)
        with col_bar1:
            if not df_wo.empty:
                cost_df = df_wo.groupby('asset_name')['total_cost'].sum().sort_values(ascending=True).tail(5)
                st.plotly_chart(go.Figure(go.Bar(x=cost_df.values, y=cost_df.index, orientation='h', marker=dict(color=cost_df.values, colorscale='Blues'), texttemplate='$%{x:,.0f}')).update_layout(title="Top 5 Spenders", height=350), use_container_width=True)
            else: st.info("No cost data.")
        with col_bar2:
            if not df_wo.empty:
                st.plotly_chart(px.treemap(df_wo['type'].value_counts().reset_index(), path=['type'], values='count', color='count', color_continuous_scale='Mint').update_layout(height=350), use_container_width=True)

    with tab_ai:
        if len(df_wo) < 5: st.warning("⚠️ Need at least 5 Work Orders for AI Analysis.")
        else:
            col_ai1, col_ai2 = st.columns([2, 1])
            top_issue_keyword = "None"; top_cost_factor = "Unknown"
            with col_ai1:
                bd_text = df_wo[df_wo['type'].str.contains('Breakdown', case=False, na=False)]['description'].dropna().tolist()
                bd_text = [t for t in bd_text if isinstance(t, str) and t.strip()]
                if bd_text:
                    try:
                        vectorizer = TfidfVectorizer(stop_words='english', max_features=10)
                        X = vectorizer.fit_transform(bd_text)
                        df_tfidf = pd.DataFrame(X.todense(), columns=vectorizer.get_feature_names_out())
                        top_keywords = df_tfidf.mean().sort_values(ascending=False).head(5)
                        st.plotly_chart(px.bar(x=top_keywords.values, y=top_keywords.index, orientation='h', labels={'x': 'Freq', 'y': 'Keyword'}, title="Top Failure Keywords", color=top_keywords.values), use_container_width=True)
                        top_issue_keyword = top_keywords.index[0] if not top_keywords.empty else "General"
                    except: st.info("Not enough text data.")

                df_ml = df_wo[['asset_name', 'type', 'total_cost']].dropna()
                if len(df_ml) > 5:
                    le = LabelEncoder()
                    df_ml['a'] = le.fit_transform(df_ml['asset_name']); df_ml['t'] = le.fit_transform(df_ml['type'])
                    rf = RandomForestRegressor(n_estimators=100).fit(df_ml[['a', 't']], df_ml['total_cost'])
                    feat_df = pd.DataFrame({'Feature': ['Asset', 'Type'], 'Imp': rf.feature_importances_})
                    st.plotly_chart(px.pie(feat_df, values='Imp', names='Feature', title="Cost Drivers"), use_container_width=True)
                    top_cost_factor = feat_df.sort_values('Imp', ascending=False).iloc[0]['Feature']

            with col_ai2:
                st.markdown("### 📋 Action Plan")
                action_items = []
                if mttr_val > 5.0: action_items.append(f"🔴 **CRITICAL:** MTTR ({mttr_val:.1f}h) high. Focus on '{top_issue_keyword}'.")
                elif mttr_val > st.session_state.target_mttr: action_items.append(f"🟠 **WARN:** MTTR ({mttr_val:.1f}h) > Target.")
                else: action_items.append("🟢 **OK:** MTTR healthy.")
                if pm_rate < 90: action_items.append(f"🟠 **PM:** Compliance {pm_rate:.1f}% low.")
                if low_stock > 0: action_items.append(f"🔴 **STOCK:** {low_stock} items low.")
                action_items.append(f"ℹ️ **COST:** Driven by {top_cost_factor}.")

                for i in action_items: st.markdown(i)
                st.download_button("📥 Report", create_strategy_pdf(action_items, {"MTTR": f"{mttr_val}h"}, {"nlp_keyword": top_issue_keyword, "cost_driver": top_cost_factor}), "AI_Report.pdf", "application/pdf")

# --- MODULE 2: MIGO CHATBOT (AI / NLP + SAVED CHAT HISTORY) ---
elif menu == "🤖 Migo Chatbot":
    st.title("🤖 Migo - Your Intelligent Maintenance Assistant")
    st.caption("NLP engine: intent classification (TF-IDF) · fuzzy asset recognition · date understanding · context memory · predictive analytics. All chats are saved in your data file.")

    sessions = st.session_state.store.setdefault("chat_sessions", [])
    if "migo_active" not in st.session_state: st.session_state.migo_active = None
    if "migo_view" not in st.session_state: st.session_state.migo_view = "💬 Chat"
    if "migo_model" not in st.session_state: st.session_state.migo_model = "claude-sonnet-5-5"

    def _migo_new_chat():
        st.session_state.migo_active = None

    def _migo_open(sid):
        st.session_state.migo_active = sid
        st.session_state.migo_view = "💬 Chat"

    def _migo_chip(text):
        st.session_state.migo_pending = text

    def _render_msg(m, key):
        if m.get("text"): st.markdown(m["text"])
        if m.get("chart"):
            st.plotly_chart(make_fig(m["chart"]), use_container_width=True, key=f"mc_{key}")
        if m.get("table"):
            st.dataframe(one_based(pd.DataFrame(m["table"])), use_container_width=True)
        if m["role"] == "assistant" and m.get("intent") and m["intent"] != "greeting":
            c = m.get("conf", 0)
            st.caption(f"🧠 {m.get('engine', 'NLP')} · intent: {m['intent']}" + (f" · confidence {int(min(c, 1) * 100)}%" if c else ""))

    def _transcript(s):
        lines = [f"Migo chat: {s['title']}", f"Started: {s['created_at']}", ""]
        for m in s["messages"]:
            who = "You" if m["role"] == "user" else "Migo"
            lines.append(f"[{m.get('time', '')}] {who}: {m.get('text', '')}")
            if m.get("table"): lines.append(pd.DataFrame(m["table"]).to_string(index=False))
            lines.append("")
        return "\n".join(lines).encode("utf-8")

    view = st.radio("Migo view", ["💬 Chat", "📜 Chat History"], horizontal=True, key="migo_view", label_visibility="collapsed")

    with st.expander("⚙️ AI Engine settings (optional LLM for free-form questions)", expanded=False):
        st.markdown("The **local NLP engine** works offline and answers all app-related questions. "
                    "Optionally add an Anthropic API key so Migo can also answer open-ended questions it can't map to a known topic. "
                    "The key stays in memory only - it is **never saved** in your data file.")
        c_k1, c_k2 = st.columns([2, 1])
        k_in = c_k1.text_input("Anthropic API key", type="password", value=st.session_state.get("migo_api_key", ""))
        st.session_state.migo_api_key = k_in
        st.session_state.migo_model = c_k2.text_input("Model", value=st.session_state.migo_model)
        st.caption("Status: " + ("🟢 LLM fallback enabled" if get_migo_api_key() else "⚪ Local NLP only"))

    # ------------------------------------------------------------ CHAT VIEW
    if view == "💬 Chat":
        active = next((s for s in sessions if s["id"] == st.session_state.migo_active), None)
        msgs = active["messages"] if active else []

        top1, top2 = st.columns([4, 1])
        top1.markdown(f"**{active['title']}**" if active else "**New conversation**")
        top2.button("➕ New Chat", on_click=_migo_new_chat, use_container_width=True)

        if not msgs:
            with st.chat_message("assistant"):
                st.markdown("Hello! I'm **Migo** 👋 Ask me anything about maintenance costs, work orders, breakdowns, assets, inventory, PM plans or technicians - or try one of these:")
            cols = st.columns(3)
            for i, qp in enumerate(QUICK_PROMPTS):
                cols[i % 3].button(qp, key=f"qp_{i}", on_click=_migo_chip, args=(qp,), use_container_width=True)

        for i, m in enumerate(msgs):
            with st.chat_message(m["role"]):
                _render_msg(m, f"{active['id']}_{i}")

        if msgs and msgs[-1]["role"] == "assistant" and msgs[-1].get("follow"):
            st.caption("💡 Suggested follow-ups")
            fcols = st.columns(len(msgs[-1]["follow"][:3]))
            for j, f in enumerate(msgs[-1]["follow"][:3]):
                fcols[j].button(f, key=f"fu_{len(msgs)}_{j}", on_click=_migo_chip, args=(f,), use_container_width=True)

        prompt = st.chat_input("Ask Migo anything about maintenance, stock, assets, costs…")
        if st.session_state.get("migo_pending"):
            prompt = st.session_state.pop("migo_pending")

        if prompt:
            now_s = datetime.now().strftime("%Y-%m-%d %H:%M")
            if active is None:
                active = {"id": uuid.uuid4().hex, "title": prompt[:48] + ("…" if len(prompt) > 48 else ""),
                          "created_at": now_s, "updated_at": now_s, "messages": [], "ctx": {}}
                sessions.append(active)
                st.session_state.migo_active = active["id"]
            active["messages"].append({"role": "user", "time": now_s, "text": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)
            with st.chat_message("assistant"):
                with st.spinner("Migo is analysing your data…"):
                    try:
                        data = migo_load()
                        resp, intent, conf, new_ctx, engine = migo_process(
                            prompt, active.get("ctx", {}), data, active["messages"][:-1], st.session_state.migo_model)
                    except Exception as e:
                        resp = {"text": f"⚠️ Sorry, I hit an error while analysing that: `{e}`", "table": None, "chart": None, "follow": DEFAULT_FOLLOW}
                        intent, conf, new_ctx, engine = "error", 0.0, active.get("ctx", {}), "NLP"
            active["messages"].append({"role": "assistant", "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                                       "text": resp.get("text"), "table": resp.get("table"), "chart": resp.get("chart"),
                                       "follow": resp.get("follow", []), "intent": intent, "conf": conf, "engine": engine})
            active["ctx"] = json.loads(json.dumps(new_ctx, default=_json_default))
            active["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            st.session_state.dirty += 1
            st.rerun()

    # ------------------------------------------------------------ HISTORY VIEW
    else:
        st.subheader("📜 Saved Conversations")
        if not sessions:
            st.info("No saved chats yet. Start a conversation in the **💬 Chat** view - it will appear here. Remember to **📥 Save Data to File** to keep your chats.")
        else:
            term = st.text_input("🔎 Search chats", placeholder="Search by title or message text…").strip().lower()
            shown = [s for s in sorted(sessions, key=lambda x: x.get("updated_at", ""), reverse=True)
                     if not term or term in s["title"].lower() or any(term in (m.get("text") or "").lower() for m in s["messages"])]
            if not shown:
                st.warning("No chats match your search.")
            else:
                labels = [f"{s['title']}  ·  {s.get('updated_at', '')}  ·  {len(s['messages'])} msgs" for s in shown]
                pick = st.selectbox("Conversation", range(len(shown)), format_func=lambda i: labels[i])
                s = shown[pick]
                b1, b2, b3 = st.columns(3)
                b1.button("▶️ Continue this chat", on_click=_migo_open, args=(s["id"],), use_container_width=True)
                b2.download_button("📄 Download transcript", _transcript(s), file_name=f"migo_chat_{s['created_at'].replace(':', '').replace(' ', '_')}.txt", mime="text/plain", use_container_width=True)
                if b3.button("🗑️ Delete this chat", use_container_width=True):
                    sessions.remove(s)
                    if st.session_state.migo_active == s["id"]: st.session_state.migo_active = None
                    st.session_state.dirty += 1
                    st.rerun()
                st.caption(f"Started {s['created_at']} · last activity {s.get('updated_at', '')}")
                st.divider()
                for i, m in enumerate(s["messages"]):
                    with st.chat_message(m["role"]):
                        _render_msg(m, f"h{s['id']}_{i}")
            with st.expander("Danger zone"):
                if st.checkbox("I understand this deletes ALL saved Migo chats") and st.button("Delete all chats"):
                    sessions.clear(); st.session_state.migo_active = None
                    st.session_state.dirty += 1
                    st.rerun()

# --- MODULE 4: ASSETS & QR CODES ---
elif menu == "🛠️ Asset Master Data":
    st.title("🛠️ Asset Management")
    tab1, tab2, tab3 = st.tabs(["📤 Upload Master Data", "📋 Master Data", "🖨️ QR Generator"])
    df_assets = load_collection('assets')

    with tab1:
        st.subheader("Import Assets from File")
        uploaded_file = st.file_uploader("Choose a file", type=['csv', 'xlsx'], key="asset_import_upload")
        if uploaded_file is not None:
            try:
                df_upload = read_upload(uploaded_file)
                st.write("Preview:"); st.dataframe(one_based(df_upload.head()))
                if st.button("Import Data"):
                    lookup = {str(c).strip().lower(): c for c in df_upload.columns}
                    def pick(*names):
                        for n in names:
                            if n in lookup: return lookup[n]
                        return None
                    c_name = pick('name', 'machine name', 'machine', 'asset', 'asset name', 'description')
                    c_tag = pick('tag', 'tag number', 'tag num', 'tag no', 'tag no.', 'tag_number', 'tagnumber')
                    c_site = pick('site', 'plant', 'location')
                    c_sec = pick('section')
                    c_line = pick('line')
                    c_fac = pick('factory')
                    def val(row, col, default):
                        if col is None or pd.isna(row[col]): return default
                        return str(row[col])
                    for index, row in df_upload.iterrows():
                        data = {"name": val(row, c_name, 'N/A'), "tag": val(row, c_tag, 'N/A'), "site": val(row, c_site, 'General'), "created_at": str(datetime.now())}
                        if c_sec is not None: data["section"] = val(row, c_sec, '')
                        if c_line is not None: data["line"] = val(row, c_line, '')
                        if c_fac is not None: data["factory"] = val(row, c_fac, '')
                        save_document('assets', data, notify=False)
                    log_notification(f"🛠️ {len(df_upload)} assets imported", toast=False)
                    st.success("Imported!"); time.sleep(1); st.rerun()
            except Exception as e: st.error(f"Error: {e}")

        st.divider(); st.subheader("Add Single Asset Manually")
        with st.form("new_asset_manual"):
            a_name = st.text_input("Machine Name"); a_tag = st.text_input("Tag Number")
            a_site = st.selectbox("Site", ["Snacks", "Pellet", "Processing", "Other"])
            if st.form_submit_button("Save Asset"):
                save_document('assets', {"name": a_name, "tag": a_tag, "site": a_site, "created_at": str(datetime.now())})
                st.success("Saved"); st.rerun()

    with tab2:
        if not df_assets.empty: st.dataframe(one_based(df_assets), use_container_width=True)
        else: st.info("No assets found.")

    with tab3:
        st.subheader("Generate Machine QR Codes")
        if df_assets.empty:
            st.warning("Add assets first.")
        else:
            with st.expander("🖼️ Logo inside the QR (Americana Foods)", expanded=False):
                lg = st.file_uploader("Upload the Americana Foods logo (PNG with transparent background works best)", type=["png", "jpg", "jpeg"], key="qr_logo_upload")
                if lg is not None: st.session_state.qr_logo = lg.getvalue()
                if st.session_state.get("qr_logo") and st.button("Remove uploaded logo", key="qr_logo_reset"):
                    st.session_state.qr_logo = b""; st.rerun()
                st.caption("Until you upload the real logo, a placeholder 'AF' badge is used. High error-correction (30%) keeps every code scannable with the logo in the centre.")
            logo_bytes = st.session_state.get("qr_logo", b"")

            st.markdown("#### Single asset")
            df_assets['qr_display'] = df_assets['name'].astype(str) + " / " + df_assets['tag'].fillna('').astype(str)
            qr_selection = st.selectbox("Select Asset", df_assets['qr_display'].unique())
            if st.button("Generate QR"):
                row = df_assets[df_assets['qr_display'] == qr_selection].iloc[0]
                did, nm, loc, payload = asset_label_fields(row)
                byte_im = make_label_png(did, nm, payload, logo_bytes)
                st.image(byte_im, width=300, caption=nm)
                st.download_button("Download", byte_im, f"qr_{did}.png", "image/png")

            st.divider()
            st.markdown("#### 🖨️ Bulk QR labels - all assets (printable PDF)")
            f_df = df_assets
            fc = st.columns(3)
            for (col, lab), box in zip([("site", "Site"), ("section", "Section"), ("line", "Line")], fc):
                if col in f_df.columns:
                    opts = sorted(f_df[col].dropna().astype(str).unique())
                    sel = box.multiselect(lab, opts, key=f"qr_f_{col}")
                    if sel: f_df = f_df[f_df[col].astype(str).isin(sel)]
            layout = st.selectbox("Labels per A4 page", ["12 labels (3 x 4) - recommended", "6 labels (2 x 3) - large", "20 labels (4 x 5) - small"])
            cols_n, rows_n = {"12": (3, 4), "6": (2, 3), "20": (4, 5)}[layout.split()[0]]
            st.caption(f"{len(f_df)} assets selected · about {-(-len(f_df) // (cols_n * rows_n))} page(s)." + (" Large lists can take a minute." if len(f_df) > 300 else ""))
            if st.button("🧾 Generate All QR Labels", key="qr_bulk_btn"):
                if f_df.empty:
                    st.warning("No assets selected.")
                else:
                    bar = st.progress(0.0, text="Generating QR labels…")
                    pdf_bytes = create_qr_labels_pdf(f_df, logo_bytes, cols_n, rows_n, progress=lambda p: bar.progress(p, text=f"Generating QR labels… {int(p * 100)}%"))
                    bar.empty()
                    st.session_state.qr_pdf = {"bytes": pdf_bytes, "count": len(f_df), "stamp": datetime.now().strftime("%Y%m%d_%H%M")}
            if st.session_state.get("qr_pdf"):
                q = st.session_state.qr_pdf
                st.success(f"✅ {q['count']} QR labels ready.")
                st.download_button("📥 Download All QR Labels (PDF)", q["bytes"], f"Americana_QR_Labels_{q['stamp']}.pdf", "application/pdf", key="qr_bulk_dl")

# --- MODULE 3: PLANNED MAINTENANCE ---
elif menu == "📅 Planned Maintenance":
    st.title("📅 PM Scheduler")
    df_machines = get_machine_data()
    df_plans = load_collection('pm_plans')
    tab1, tab2, tab3 = st.tabs(["📝 Create Plan", "📋 Active Schedule", "📅 Calendar"])

    with tab1:
        st.subheader("New Maintenance Plan")
        if df_machines.empty: st.warning("No assets found.")
        else:
            df_machines['d'] = df_machines['Machine Name'].astype(str) + " " + df_machines['Tag Number'].astype(str)
            sel = st.selectbox("Machine", df_machines['d'].unique())
            freq = st.selectbox("Frequency", ["Weekly", "Monthly", "Yearly"])
            tasks = []
            for i in range(3):
                t = st.text_input(f"Task {i+1}")
                if t: tasks.append({"desc": t, "done": False})
            if st.button("Save Plan"):
                save_document('pm_plans', {"asset_name": sel, "frequency": freq, "tasks_data": tasks, "next_due": str(datetime.now().date()), "status": "Active"})
                st.success("Saved"); st.rerun()

    with tab2:
        if not df_plans.empty:
            for i, row in df_plans.iterrows():
                with st.expander(f"{row['asset_name']} - {row['frequency']}"):
                    plan_tasks = safe_list(row.get('tasks_data'))
                    if plan_tasks: st.table(one_based(pd.DataFrame(plan_tasks)))
                    if st.button("Generate WO", key=f"g{i}"):
                        wo = {"custom_id": generate_overhaul_id(st.session_state.wo_counter), "asset_name": row['asset_name'], "type": f"PM ({row['frequency']})", "status": "Open", "tasks_data": plan_tasks, "created_at": str(datetime.now()), "total_cost": 0.0}
                        save_document('work_orders', wo); st.session_state.wo_counter += 1; st.success("Generated")
        else: st.info("No plans.")

    with tab3:
        if not df_plans.empty: st.dataframe(one_based(df_plans[['asset_name', 'frequency', 'next_due']]))

# --- MODULE 4: WORK ORDERS ---
elif menu == "🔧 Work Orders":
    st.title("🔧 Work Orders")
    tab1, tab2 = st.tabs(["📋 Open Orders", "✅ History"])
    df_wo = load_collection('work_orders')

    with tab1:
        open_wos = df_wo[df_wo['status'] == 'Open'] if not df_wo.empty else pd.DataFrame()
        if not open_wos.empty:
            for i, row in open_wos.iterrows():
                with st.expander(f"{row.get('custom_id')} - {row.get('asset_name')}"):
                    desc = row.get('description')
                    if isinstance(desc, str): st.write(desc)
                    tasks = safe_list(row.get('tasks_data'))
                    new_tasks = []
                    for t in tasks:
                        d = st.checkbox(t.get('desc'), t.get('done'), key=f"t{row['id']}{t.get('desc')}")
                        new_tasks.append({"desc": t.get('desc'), "done": d})

                    df_p = load_collection('spare_parts')
                    if not df_p.empty:
                        p = st.selectbox("Part", df_p['name'], key=f"p{row['id']}")
                        q = st.number_input("Qty", 1, key=f"q{row['id']}")
                        if st.button("Add Part", key=f"b{row['id']}"):
                            unit = float(pd.to_numeric(df_p[df_p['name']==p].iloc[0].get('cost', 0), errors='coerce') or 0)
                            cost = unit * q
                            parts = list(safe_list(row.get('parts_used')))
                            parts.append({"name": p, "qty": q, "unit_cost": unit, "total_cost": cost})
                            cur_total = pd.to_numeric(row.get('total_cost', 0), errors='coerce')
                            cur_total = 0.0 if pd.isna(cur_total) else float(cur_total)
                            update_document('work_orders', row['id'], {'parts_used': parts, 'total_cost': cur_total + cost})
                            st.rerun()

                    tech = st.text_input("Tech", key=f"te{row['id']}")
                    if st.button("Close Job", key=f"cl{row['id']}"):
                        update_document('work_orders', row['id'], {'status': 'Closed', 'tasks_data': new_tasks, 'fixing_technician': tech, 'closed_date': str(datetime.now())})
                        st.rerun()
        else: st.info("No open orders.")

    with tab2:
        closed = df_wo[df_wo['status'] == 'Closed'] if not df_wo.empty else pd.DataFrame()
        if not closed.empty:
            st.dataframe(one_based(closed))
            s = st.selectbox("Select for PDF", closed['id'])
            if st.button("Generate PDF"):
                st.download_button("Download", create_wo_pdf(closed[closed['id']==s].iloc[0]), "wo.pdf")
        else: st.info("No closed orders yet.")

# --- MODULE 5: INVENTORY ---
elif menu == "📦 Inventory":
    st.title("📦 Spare Parts Inventory")
    t1, t2 = st.tabs(["List", "Add"])
    with t1:
        df = load_collection('spare_parts')
        if not df.empty:
            st.dataframe(one_based(df))
            p = st.selectbox("Part", df['name'])
            q = st.number_input("New Qty")
            if st.button("Update"):
                pid = df[df['name']==p].iloc[0]['id']
                update_document('spare_parts', pid, {'quantity': q}); st.success("Updated"); st.rerun()
        else: st.info("No spare parts yet.")
    with t2:
        with st.form("add_p"):
            n = st.text_input("Name"); c = st.number_input("Cost")
            if st.form_submit_button("Save"):
                save_document('spare_parts', {"name": n, "cost": c, "quantity": 0}); st.success("Saved")

# --- MODULE 6: BREAKDOWN ---
elif menu == "🚨 Breakdown Reporting":
    st.title("🚨 Report Breakdown")
    a = st.text_input("Asset"); d = st.text_area("Issue")
    if st.button("Report"):
        save_document('work_orders', {"type": "Breakdown", "asset_name": a, "description": d, "status": "Open", "created_at": str(datetime.now()), "priority": "High", "total_cost": 0.0})
        st.success("Reported")

# --- MODULE 7: MANAGER ---
elif menu == "💼 Maintenance Manager":
    st.title("💼 OPEX Forecast")
    base = st.number_input("2025 Actuals", 1000000)
    inf = st.slider("Inflation", 0, 20, 10) / 100
    risk = st.number_input("Risk", 50000)
    total = base * (1 + inf) + risk
    st.metric("2026 Forecast", f"${total:,.0f}")
    st.plotly_chart(go.Figure(go.Waterfall(measure=["relative", "relative", "relative", "total"], x=["Base", "Inflation", "Risk", "Total"], y=[base, base*inf, risk, total])))
