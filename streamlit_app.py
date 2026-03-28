import streamlit as st
import pandas as pd
import sys
import subprocess
import time
from datetime import datetime
import io
import hashlib
import json

# --- 1. CONFIGURATION & AUTO-INSTALL ---
st.set_page_config(page_title="Americana ERP - PM Module", page_icon="🏭", layout="wide")

def install(package):
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", package])
    except:
        st.error(f"Failed to install {package}. Please install manually.")

# --- IMPORTS ---
# Core Firebase
try:
    import firebase_admin
    from firebase_admin import credentials, firestore
except ImportError:
    install("firebase-admin")
    import firebase_admin
    from firebase_admin import credentials, firestore

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

# --- 2. FIREBASE INITIALIZATION (Cached Resource) ---
@st.cache_resource
def get_db():
    if not firebase_admin._apps:
        if "gcp_service_account" in st.secrets:
            key_dict = dict(st.secrets["gcp_service_account"])
            cred = credentials.Certificate(key_dict)
            firebase_admin.initialize_app(cred)
        else:
            return None
    try:
        return firestore.client()
    except:
        return None

db = get_db()

if not db:
    st.warning("⚠️ Firebase Secrets not found. Please set up secrets.toml.")

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

def log_notification(message):
    try:
        note_data = {
            "message": message,
            "timestamp": datetime.now(),
            "read": False
        }
        if db:
            db.collection('notifications').add(note_data)
            fetch_notifications.clear()
        st.toast(message, icon="🔔")
    except Exception as e:
        print(f"Notification Error: {e}")

@st.cache_data(ttl=600) 
def load_collection(collection_name):
    try:
        if db is None: return pd.DataFrame()
        docs = db.collection(collection_name).stream()
        data = []
        for doc in docs:
            item = doc.to_dict()
            item['id'] = doc.id
            data.append(item)
        return pd.DataFrame(data)
    except Exception as e:
        return pd.DataFrame()

@st.cache_data(ttl=300)
def fetch_notifications():
    try:
        if db:
            notes_ref = db.collection('notifications').order_by('timestamp', direction=firestore.Query.DESCENDING).limit(10)
            return [doc.to_dict() for doc in notes_ref.stream()]
        return []
    except:
        return []

def save_document(collection_name, data):
    try:
        if db:
            db.collection(collection_name).add(data)
            msg = f"New record added to {collection_name}"
            if collection_name == 'pm_plans': msg = f"📅 PM Plan Created: {data.get('asset_name', 'Unknown')}"
            elif collection_name == 'work_orders': msg = f"🔧 WO Generated: {data.get('asset_name', 'Unknown')}"
            elif collection_name == 'breakdowns': msg = f"🚨 Breakdown Reported: {data.get('asset_name', 'Unknown')}"
            elif collection_name == 'assets': msg = f"🛠️ New Asset Imported: {data.get('tag', 'Unknown')}"
            elif collection_name == 'spare_parts': msg = f"📦 New Part Added: {data.get('name', 'Unknown')}"
            
            log_notification(msg)
            load_collection.clear()
            
    except Exception as e:
        st.error(f"Error saving data: {e}")

def update_document(collection_name, doc_id, data):
    try:
        if db:
            db.collection(collection_name).document(doc_id).update(data)
            status = data.get('status', 'Updated')
            log_notification(f"✅ {collection_name[:-1].title()} {status}: ID {str(doc_id)[:5]}...")
            load_collection.clear()
            
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
                    st.info("DB Empty. Use Master: admin / admin123")

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
    pdf.cell(50, 8, wo_data.get('type', ''), 0, 1)
    
    pdf.set_font("Arial", 'B', 10)
    pdf.cell(40, 8, "Description:", 0, 0)
    pdf.set_font("Arial", '', 10)
    pdf.multi_cell(0, 8, wo_data.get('description', ''))
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
        for t in tasks:
            if isinstance(t, dict):
                pdf.cell(10, 8, str(t.get('id', '')), 1, 0, 'C')
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
            cost = float(p.get('unit_cost', 0))
            line_total = float(p.get('total_cost', 0))
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

    pdf.set_font("Arial", 'B', 10)
    pdf.cell(90, 10, f"Technician: {wo_data.get('fixing_technician', wo_data.get('technician', ''))}", 0, 0)
    pdf.cell(90, 10, f"Approver: {wo_data.get('approver', '')}", 0, 1)
    
    return pdf.output(dest='S').encode('latin-1')

# --- 7. DATA HANDLING ---
def generate_overhaul_id(wo_number):
    now = datetime.now()
    return f"{wo_number:04d}{now.hour:02d}{now.minute:02d}{now.day:02d}{now.month:02d}{now.year}"

def get_machine_data():
    st.sidebar.markdown("---")
    st.sidebar.header("📂 PM Data Source")
    st.sidebar.caption("Upload file (Machine Name, Site, Section, etc.)")
    uploaded_file = st.sidebar.file_uploader("Upload Machine List (Optional)", type=['csv', 'xlsx'])
    
    if uploaded_file is not None:
        try:
            if uploaded_file.name.endswith('.csv'): df = pd.read_csv(uploaded_file)
            else: df = pd.read_excel(uploaded_file)
            return df
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
    
    # 3. Connection Status (RENAMED to ERP Connected)
    st.markdown("✅ **ERP Connected**")
    
    st.write("") # Spacer
    st.write("") 
    
    # 4. Alerts
    st.markdown("### 🔔 Alerts")
    notes_data = fetch_notifications()
    with st.expander("Latest Messages", expanded=False):
        if notes_data:
            for n_data in notes_data:
                time_str = n_data['timestamp'].strftime("%H:%M") if 'timestamp' in n_data else ""
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
        return pdf.output(dest='S').encode('latin-1')

    # 3. Load Data
    df_wo = load_collection('work_orders')
    df_inv = load_collection('spare_parts')

    # 4. Pre-process
    if not df_wo.empty:
        df_wo['created_at'] = pd.to_datetime(df_wo['created_at'], errors='coerce')
        df_wo['closed_date'] = pd.to_datetime(df_wo.get('closed_date'), errors='coerce')
        df_wo['technician'] = df_wo.get('fixing_technician', df_wo.get('technician', 'Unknown')).fillna('Unknown')
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
        
        first_date = df_wo['created_at'].min() if not df_wo.empty else datetime.now()
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
        else: df_pm = pd.DataFrame()
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

# --- MODULE 2: MIGO CHATBOT (UPGRADED) ---
elif menu == "🤖 Migo Chatbot":
    st.title("🤖 Migo - Your Intelligent Maintenance Assistant")
    st.caption("I can visualize data, answer questions about stock, cost, and assets.")
    
    # 1. Load Data
    df_wo = load_collection('work_orders')
    df_inv = load_collection('spare_parts')
    df_assets = load_collection('assets')

    # 2. Pre-process
    if not df_wo.empty:
        df_wo['total_cost'] = pd.to_numeric(df_wo['total_cost'], errors='coerce').fillna(0)
        df_wo['created_at'] = pd.to_datetime(df_wo['created_at'], errors='coerce')
        df_wo['closed_date'] = pd.to_datetime(df_wo.get('closed_date'), errors='coerce')
    if not df_inv.empty:
        df_inv['quantity'] = pd.to_numeric(df_inv['quantity'], errors='coerce').fillna(0)

    # 3. Chat History
    if "messages" not in st.session_state:
        st.session_state.messages = [
            {"role": "assistant", "content": "Hello! I'm Migo. Try asking me:\n- 'Draw a bar chart of cost by asset'\n- 'Show me low stock items'\n- 'Give report about machine performance'"}
        ]

    # 4. Render Chat
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            if isinstance(msg["content"], str):
                st.markdown(msg["content"])
            elif msg.get("type") == "plot":
                st.plotly_chart(msg["content"], use_container_width=True)
            elif msg.get("type") == "df":
                st.dataframe(msg["content"], use_container_width=True)

    # 5. Logic Processor
    if prompt := st.chat_input("Ask Migo..."):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("Analyzing data..."):
                time.sleep(0.5)
                q = prompt.lower()
                response_content = None
                response_type = "text"

                try:
                    # LOGIC BRANCH 1: VISUALIZATIONS
                    if "chart" in q or "plot" in q or "graph" in q or "draw" in q:
                        if "cost" in q and "asset" in q:
                            if df_wo.empty: response_content = "No cost data available."
                            else:
                                d = df_wo.groupby('asset_name')['total_cost'].sum().reset_index()
                                response_content = px.bar(d, x='asset_name', y='total_cost', title="Total Cost per Asset", color='total_cost')
                                response_type = "plot"
                        elif "status" in q:
                            if df_wo.empty: response_content = "No data."
                            else:
                                response_content = px.pie(df_wo, names='status', title="Work Order Status", hole=0.4)
                                response_type = "plot"
                        elif "type" in q or "breakdown" in q:
                            if df_wo.empty: response_content = "No data."
                            else:
                                response_content = px.bar(df_wo['type'].value_counts().reset_index(), x='type', y='count', title="Work Order Types")
                                response_type = "plot"
                        else:
                            response_content = "I can chart 'cost by asset', 'status', or 'types'. Please specify."

                    # LOGIC BRANCH 2: LISTS / TABLES
                    elif "list" in q or "show" in q or ("what" in q and ("items" in q or "orders" in q)):
                        if "open" in q:
                            d = df_wo[df_wo['status'] == 'Open'][['custom_id', 'asset_name', 'description']]
                            response_content = d if not d.empty else "No open orders."
                            response_type = "df"
                        elif "low stock" in q:
                            d = df_inv[df_inv['quantity'] < 10][['name', 'sku', 'quantity', 'location']]
                            response_content = d if not d.empty else "Inventory is healthy (no items < 10)."
                            response_type = "df"
                        elif "assets" in q or "machines" in q:
                            d = df_assets[['name', 'tag', 'site']]
                            response_content = d
                            response_type = "df"
                        else:
                            response_content = "I can list 'open orders', 'low stock', or 'assets'."

                    # LOGIC BRANCH 3: KPI / AGGREGATES
                    elif "total" in q or "how many" in q or "count" in q:
                        if "cost" in q or "spend" in q:
                            val = df_wo['total_cost'].sum()
                            response_content = f"Total Maintenance Spend: **${val:,.2f}**"
                        elif "open" in q:
                            val = len(df_wo[df_wo['status'] == 'Open'])
                            response_content = f"Active Work Orders: **{val}**"
                        elif "technician" in q:
                            val = df_wo['fixing_technician'].nunique()
                            response_content = f"Active Technicians: **{val}**"
                        else:
                            response_content = "I can calculate total cost, open orders, or technician count."

                    # LOGIC BRANCH 4: PERFORMANCE REPORTS (NEW!)
                    elif "report" in q or "performance" in q or "analyze" in q:
                        if df_wo.empty:
                            response_content = "I need more work order data to generate a performance report."
                        else:
                            costs = df_wo.groupby('asset_name')['total_cost'].sum()
                            bds = df_wo[df_wo['type'].str.contains('Breakdown', case=False, na=False)]['asset_name'].value_counts()
                            
                            mttr_series = pd.Series(dtype=float)
                            closed_bds = df_wo[(df_wo['type'].str.contains('Breakdown', case=False, na=False)) & (df_wo['status'] == 'Closed')].copy()
                            if not closed_bds.empty:
                                closed_bds['repair_hrs'] = (closed_bds['closed_date'] - closed_bds['created_at']).dt.total_seconds() / 3600
                                mttr_series = closed_bds.groupby('asset_name')['repair_hrs'].mean()
                            
                            report_df = pd.DataFrame({
                                'Total Cost ($)': costs, 
                                'Breakdown Count': bds,
                                'MTTR (Hours)': mttr_series
                            }).fillna(0).sort_values(by='Total Cost ($)', ascending=False)
                            
                            response_content = report_df
                            response_type = "df"
                            st.session_state.messages.append({"role": "assistant", "content": "Here is the machine performance report:", "type": "text"})

                    # FALLBACK
                    else:
                        response_content = "I didn't understand. Try: 'Chart cost by asset', 'List low stock', 'Total spend', or 'Give report about machine performance'."

                    if response_type == "text": st.markdown(response_content)
                    elif response_type == "plot": st.plotly_chart(response_content, use_container_width=True)
                    elif response_type == "df": st.dataframe(response_content, use_container_width=True)
                    
                    st.session_state.messages.append({"role": "assistant", "content": response_content, "type": response_type})

                except Exception as e:
                    st.error(f"Error processing request: {e}")

# --- MODULE 4: ASSETS & QR CODES ---
elif menu == "🛠️ Asset Master Data":
    st.title("🛠️ Asset Management")
    tab1, tab2, tab3 = st.tabs(["📤 Upload Master Data", "📋 Master Data", "🖨️ QR Generator"])
    df_assets = load_collection('assets')
    
    with tab1:
        st.subheader("Import Assets from File")
        uploaded_file = st.file_uploader("Choose a file", type=['csv', 'xlsx'])
        if uploaded_file is not None:
            try:
                if uploaded_file.name.endswith('.csv'): df_upload = pd.read_csv(uploaded_file)
                else: df_upload = pd.read_excel(uploaded_file)
                st.write("Preview:"); st.dataframe(df_upload.head())
                if st.button("Import Data"):
                    for index, row in df_upload.iterrows():
                        data = {"name": str(row.get('name', 'N/A')), "tag": str(row.get('tag', 'N/A')), "site": str(row.get('site', 'General')), "created_at": str(datetime.now())}
                        save_document('assets', data)
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
        if not df_assets.empty: st.dataframe(df_assets, use_container_width=True)
        else: st.info("No assets found.")
            
    with tab3:
        st.subheader("Generate Machine QR Codes")
        if not df_assets.empty:
            df_assets['qr_display'] = df_assets['name'].astype(str) + " / " + df_assets['tag'].fillna('').astype(str)
            qr_selection = st.selectbox("Select Asset", df_assets['qr_display'].unique())
            if st.button("Generate QR"):
                row = df_assets[df_assets['qr_display'] == qr_selection].iloc[0]
                qr = qrcode.make(f"ID:{row['id']}\nName:{row['name']}")
                buf = io.BytesIO(); qr.save(buf, format="PNG"); byte_im = buf.getvalue()
                st.image(byte_im, caption=row['name']); st.download_button("Download", byte_im, "qr.png")
        else: st.warning("Add assets first.")

# --- MODULE 3: PLANNED MAINTENANCE ---
elif menu == "📅 Planned Maintenance":
    st.title("📅 PM Scheduler")
    df_machines = get_machine_data()
    tab1, tab2, tab3 = st.tabs(["📝 Create Plan", "📋 Active Schedule", "📅 Calendar"])
    
    with tab1:
        st.subheader("New Maintenance Plan")
        if df_machines.empty: st.warning("No assets found.")
        else:
            df_machines['d'] = df_machines['Machine Name'] + " " + df_machines['Tag Number']
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
        df_plans = load_collection('pm_plans')
        if not df_plans.empty:
            for i, row in df_plans.iterrows():
                with st.expander(f"{row['asset_name']} - {row['frequency']}"):
                    st.table(pd.DataFrame(row.get('tasks_data', [])))
                    if st.button("Generate WO", key=f"g{i}"):
                        wo = {"custom_id": generate_overhaul_id(st.session_state.wo_counter), "asset_name": row['asset_name'], "type": f"PM ({row['frequency']})", "status": "Open", "tasks_data": row.get('tasks_data'), "created_at": str(datetime.now())}
                        save_document('work_orders', wo); st.session_state.wo_counter += 1; st.success("Generated")
        else: st.info("No plans.")

    with tab3:
        if not df_plans.empty: st.dataframe(df_plans[['asset_name', 'frequency', 'next_due']])

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
                    st.write(row.get('description'))
                    tasks = row.get('tasks_data', [])
                    new_tasks = []
                    for t in tasks:
                        d = st.checkbox(t.get('desc'), t.get('done'), key=f"t{row['id']}{t.get('desc')}")
                        new_tasks.append({"desc": t.get('desc'), "done": d})
                    
                    df_p = load_collection('spare_parts')
                    if not df_p.empty:
                        p = st.selectbox("Part", df_p['name'], key=f"p{row['id']}")
                        q = st.number_input("Qty", 1, key=f"q{row['id']}")
                        if st.button("Add Part", key=f"b{row['id']}"):
                            cost = float(df_p[df_p['name']==p].iloc[0].get('cost', 0)) * q
                            parts = row.get('parts_used', []); parts.append({"name": p, "qty": q, "total_cost": cost})
                            update_document('work_orders', row['id'], {'parts_used': parts, 'total_cost': float(row.get('total_cost', 0)) + cost})
                            st.rerun()
                    
                    tech = st.text_input("Tech", key=f"te{row['id']}")
                    if st.button("Close Job", key=f"cl{row['id']}"):
                        update_document('work_orders', row['id'], {'status': 'Closed', 'tasks_data': new_tasks, 'fixing_technician': tech, 'closed_date': str(datetime.now())})
                        st.rerun()
        else: st.info("No open orders.")

    with tab2:
        closed = df_wo[df_wo['status'] == 'Closed'] if not df_wo.empty else pd.DataFrame()
        if not closed.empty:
            st.dataframe(closed)
            s = st.selectbox("Select for PDF", closed['id'])
            if st.button("Generate PDF"):
                st.download_button("Download", create_wo_pdf(closed[closed['id']==s].iloc[0]), "wo.pdf")

# --- MODULE 5: INVENTORY ---
elif menu == "📦 Inventory":
    st.title("📦 Spare Parts Inventory")
    t1, t2 = st.tabs(["List", "Add"])
    with t1:
        df = load_collection('spare_parts')
        if not df.empty:
            st.dataframe(df)
            p = st.selectbox("Part", df['name'])
            q = st.number_input("New Qty")
            if st.button("Update"):
                pid = df[df['name']==p].iloc[0]['id']
                update_document('spare_parts', pid, {'quantity': q}); st.success("Updated"); st.rerun()
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
        save_document('work_orders', {"type": "Breakdown", "asset_name": a, "description": d, "status": "Open", "created_at": str(datetime.now()), "priority": "High"})
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
