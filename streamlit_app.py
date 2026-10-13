import streamlit as st
import pandas as pd
import sys
import subprocess
import time
from datetime import datetime
import io
import hashlib
import json
import re
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

def read_upload_all(f):
    """Reads ALL sheets/tabs of an Excel file (Snacks, Pellet and any future sub-plant tab) or a CSV.
    Returns (combined DataFrame with a '_sheet' column, {sheet name: row count})."""
    if f.name.lower().endswith(".csv"):
        d = pd.read_csv(f); d["_sheet"] = "CSV"
        return d, {"CSV": len(d)}
    if openpyxl is None:
        raise ImportError("Excel support is missing. Add 'openpyxl' to requirements.txt (or upload a CSV file).")
    frames, counts = [], {}
    for name, d in pd.read_excel(f, sheet_name=None).items():
        d = d.dropna(how="all")
        if d.empty: continue
        d = d.copy()
        d.columns = [str(c).strip() for c in d.columns]
        d["_sheet"] = str(name).strip()
        frames.append(d); counts[str(name).strip()] = len(d)
    return (pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()), counts

def normalize_assets_df(df):
    """Maps any asset list (all tabs combined) to: name, tag, factory, site, section, line.
    Column names are matched flexibly; Site falls back to the tab name (e.g. 'Snacks', 'Pellet')."""
    cols = {}
    for c in df.columns:
        cols.setdefault(re.sub(r"\s+", " ", str(c)).strip().lower(), []).append(c)
    def col(*names):
        cs = [c for n in names for c in cols.get(n, [])]
        if not cs: return pd.Series([""] * len(df), index=df.index)
        ser = df[cs].bfill(axis=1).iloc[:, 0]
        ser = ser.fillna("").astype(str).str.replace(r"\s+", " ", regex=True).str.strip()
        return ser.replace({"nan": "", "None": "", "NaN": ""})
    out = pd.DataFrame({
        "name": col("name", "machine name", "machine", "asset", "asset name", "equipment", "description"),
        "tag": col("tag", "tag number", "tag num", "tag no", "tag no.", "tag_number", "tagnumber", "asset id"),
        "factory": col("factory", "plant factory"),
        "site": col("site", "plant", "sub plant", "sub-plant", "subplant"),
        "section": col("section"),
        "line": col("line"),
    })
    if "_sheet" in df.columns:
        sheet = df["_sheet"].astype(str).str.strip()
        generic = sheet.str.lower().str.match(r"^(sheet\d*|csv|data|master|master data)$")
        out["site"] = out["site"].where(out["site"] != "", sheet.where(~generic, ""))
    out = out[(out["name"] != "") | (out["tag"] != "")].reset_index(drop=True)
    return out

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
COLLECTIONS = ["users", "assets", "pm_plans", "work_orders", "spare_parts", "breakdowns", "notifications", "chat_sessions", "settings", "documents", "opex"]

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
    st.session_state.pub_mark = None   # force re-publish of technician QR data after a restore

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
        "💼 Maintenance Manager": "linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%)",
        "💰 OPEX & Spend Analysis": "linear-gradient(135deg, #eef2f7 0%, #d5e3f0 100%)"
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

        /* METRIC CARDS: wrap long values/labels instead of cutting them with "..." */
        div[data-testid="stMetricValue"] > div, div[data-testid="stMetricLabel"] p {{
            white-space: normal !important; overflow: visible !important; text-overflow: clip !important; word-break: break-word;
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

# =====================================================================
# --- 4b. SCAN-TO-REPORT: operator page (NO login) + shared inbox ---
# QR codes open  <APP_URL>/?r=<TAG>&n=<MACHINE NAME>  -> breakdown form.
# Reports land in a server-side inbox shared by all sessions; the maintenance
# manager imports them as Open Breakdown work orders (Breakdown Reporting page).
# =====================================================================
import os
import base64
import tempfile
import threading
from urllib.parse import quote

_AMERICANA_LOGO_B64 = (
    "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"
)

INBOX_FILE = os.path.join(tempfile.gettempdir(), "americana_breakdown_inbox.json")

@st.cache_resource
def _inbox_lock():
    return threading.Lock()

def inbox_load():
    try:
        with open(INBOX_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return []

def _inbox_write(items):
    tmp = INBOX_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(items, fh)
    os.replace(tmp, INBOX_FILE)

def inbox_add(rec):
    with _inbox_lock():
        items = inbox_load(); items.append(rec); _inbox_write(items)

def inbox_remove(ids):
    ids = set(ids)
    with _inbox_lock():
        _inbox_write([i for i in inbox_load() if i.get("id") not in ids])

def get_setting(key, default=""):
    for d in st.session_state.store.get("settings", []):
        if key in d: return d[key]
    return default

def set_setting(key, value):
    lst = st.session_state.store.setdefault("settings", [])
    if lst: lst[0][key] = value
    else: lst.append({key: value})
    st.session_state.dirty += 1

APP_URL_DEFAULT = "https://gearbox-cmms.streamlit.app"

def get_app_url():
    u = get_setting("app_url", "")
    if not u:
        try: u = st.secrets.get("APP_URL", "")
        except Exception: u = ""
    return (u or APP_URL_DEFAULT).strip().rstrip("/")

def build_scan_url(base, tag, name):
    return f"{base.rstrip('/')}/?r={quote(str(tag))}&n={quote(str(name))}"

def _compress_photo(file):
    try:
        im = Image.open(file).convert("RGB"); im.thumbnail((900, 900))
        buf = io.BytesIO(); im.save(buf, "JPEG", quality=70, optimize=True)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return ""

def scan_page(tag, name):
    st.markdown("""<style>
    [data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"] {display:none}
    .block-container {max-width: 760px; padding-top: 1.5rem}
    .stApp {background: linear-gradient(to right, #ffecd2 0%, #fcb69f 100%); font-family: "72","Arial","Helvetica",sans-serif}
    .asset-card {background:#fff; border-left:6px solid #0a6ed1; border-radius:6px; padding:12px 16px; margin:8px 0 14px 0; box-shadow:0 1px 3px rgba(0,0,0,.2)}
    .asset-card b {font-size:1.15rem}
    .stButton>button, .stFormSubmitButton>button {background-color:#d63031; color:#fff; border:none; font-weight:bold; border-radius:4px; width:100%; padding:.6rem}
    </style>""", unsafe_allow_html=True)
    st.image(base64.b64decode(_AMERICANA_LOGO_B64), width=200)
    st.markdown("## 🏭 Machine access")
    st.markdown(f"<div class='asset-card'><b>{name}</b><br>Tag: <code>{tag}</code></div>", unsafe_allow_html=True)

    tab_rep, tab_info = st.tabs(["🚨 Report Breakdown", "🔧 Technician Info"])
    with tab_rep: _operator_form(tag, name)
    with tab_info: _tech_view(tag, name)

def _operator_form(tag, name):
    done = st.session_state.get("op_done")
    if done:
        st.success(f"✅ Report sent to the maintenance team.\n\n**Reference: {done['ref']}**")
        st.caption(f"{done['name']} · {done['time']}")
        if st.button("Report another issue"):
            st.session_state.op_done = None; st.rerun()
        return

    with st.form("operator_breakdown"):
        who = st.text_input("Your name / ID *")
        status = st.radio("Machine status *", ["🛑 Machine stopped", "⚠️ Running with a problem"])
        cat = st.selectbox("Problem type", ["Mechanical", "Electrical", "Pneumatic / Hydraulic", "Instrumentation / Control", "Process / Operational", "Other"])
        safety = st.checkbox("⛔ Safety hazard")
        desc = st.text_area("What happened? *", placeholder="Describe the problem: noise, smell, alarm code, when it started…", height=120)
        photo = st.file_uploader("Photo (optional)", type=["jpg", "jpeg", "png"])
        sent = st.form_submit_button("🚨 SEND BREAKDOWN REPORT")
    if sent:
        if not who.strip() or len(desc.strip()) < 5:
            st.error("Please enter your name and describe the problem.")
        else:
            now = datetime.now()
            rec = {"id": uuid.uuid4().hex, "ref": f"BD-{now.strftime('%y%m%d-%H%M%S')}", "tag": tag, "name": name,
                   "reported_by": who.strip(), "category": cat, "machine_status": status.split(" ", 1)[1],
                   "priority": "Critical" if safety else ("High" if "stopped" in status else "Medium"),
                   "description": desc.strip(), "photo_b64": _compress_photo(photo) if photo is not None else "",
                   "submitted_at": str(now)}
            inbox_add(rec)
            st.session_state.op_done = {"ref": rec["ref"], "name": name, "time": now.strftime("%Y-%m-%d %H:%M")}
            st.rerun()

# ---- Technician / engineer access: published snapshot (read-only, PIN protected) ----
import hashlib as _hl
SNAP_DIR = os.path.join(tempfile.gettempdir(), "americana_snapshot")
SNAP_FILE = os.path.join(SNAP_DIR, "snapshot.json")
DOCS_DIR = os.path.join(SNAP_DIR, "docs")
DOC_TYPES = ["Manual", "Diagram / Drawing", "Datasheet", "Procedure / SOP", "Photo", "Certificate / Report", "Other"]
DOC_ICONS = {"Manual": "📘", "Diagram / Drawing": "📐", "Datasheet": "📄", "Procedure / SOP": "📋", "Photo": "🖼️", "Certificate / Report": "🏅", "Other": "📎"}

def pin_hash(pin):
    return _hl.sha256(("americana|" + str(pin)).encode()).hexdigest()

def publish_snapshot():
    """Copies the data technicians may view (assets, work orders, PM plans, parts, documents) to the server
    so phones scanning a QR can read it. Documents are written to disk once."""
    s = st.session_state.store
    os.makedirs(DOCS_DIR, exist_ok=True)
    docs = s.get("documents", [])
    wos = []
    for w in s.get("work_orders", []):
        d = dict(w)
        if d.get("status") != "Open": d.pop("photo_b64", None)
        wos.append(d)
    snap = {"published_at": str(datetime.now()), "pin_hash": get_setting("tech_pin_hash", ""),
            "assets": s.get("assets", []), "work_orders": wos, "pm_plans": s.get("pm_plans", []),
            "spare_parts": [{k: p.get(k) for k in ("name", "sku", "quantity", "location", "cost")} for p in s.get("spare_parts", [])],
            "documents": [{k: v for k, v in d.items() if k != "data_b64"} for d in docs],
            "parts_used": [{"tag": r.get("tag"), "item_code": r.get("item_code"), "description": r.get("description"), "qty": -(r.get("issue_qty") or 0), "date": r.get("gl_date")}
                           for r in s.get("opex", []) if r.get("tag") and r.get("period") in sorted({x.get("period") for x in s.get("opex", []) if x.get("period")})[-12:]]}
    with _inbox_lock():
        keep = set()
        for d in docs:
            fn = d["id"] + ".bin"; keep.add(fn); p = os.path.join(DOCS_DIR, fn)
            if not os.path.exists(p):
                with open(p, "wb") as fh: fh.write(base64.b64decode(d.get("data_b64", "")))
        for fn in os.listdir(DOCS_DIR):
            if fn not in keep:
                try: os.remove(os.path.join(DOCS_DIR, fn))
                except Exception: pass
        tmp = SNAP_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh: json.dump(snap, fh, default=_json_default)
        os.replace(tmp, SNAP_FILE)
    st.session_state.pub_info = {"time": snap["published_at"][:16], "assets": len(snap["assets"]), "wos": len(wos), "docs": len(docs)}

@st.cache_resource(show_spinner=False)
def _snap_read(mtime):
    with open(SNAP_FILE, "r", encoding="utf-8") as fh: return json.load(fh)

def snap_get():
    try: return _snap_read(os.path.getmtime(SNAP_FILE))
    except Exception: return None

def _fmt_size(n):
    n = float(n or 0)
    return f"{n / 1048576:.1f} MB" if n >= 1048576 else f"{n / 1024:.0f} KB"

def _tech_view(tag, name):
    snap = snap_get()
    if not snap or not snap.get("pin_hash"):
        st.info("🔒 Maintenance information is not available yet. Please ask the maintenance manager to enable technician access.")
        return
    if not st.session_state.get("tech_ok"):
        st.markdown("🔒 **Technician / engineer access**")
        pin = st.text_input("Enter the technician PIN", type="password", key="tech_pin_in")
        if st.button("Unlock", key="tech_unlock"):
            if st.session_state.get("tech_fail", 0) >= 5: st.error("Too many wrong attempts. Please try again later.")
            elif pin_hash(pin) == snap["pin_hash"]:
                st.session_state.tech_ok = True; st.session_state.tech_fail = 0; st.rerun()
            else:
                st.session_state.tech_fail = st.session_state.get("tech_fail", 0) + 1; st.error("Wrong PIN.")
        return

    tl = str(tag).lower()
    asset = next((a for a in snap["assets"] if str(a.get("tag", "")).lower() == tl), None)
    aname = str((asset or {}).get("name") or name).lower().strip()
    wos = [w for w in snap["work_orders"] if tl in str(w.get("asset_name", "")).lower() or (isinstance(w.get("meta"), dict) and str(w["meta"].get("tag", "")).lower() == tl)]
    plans = [p for p in snap["pm_plans"] if tl in str(p.get("asset_name", "")).lower()]
    docs = [d for d in snap["documents"] if (d.get("scope") == "tag" and str(d.get("tag", "")).lower() == tl) or (d.get("scope") == "name" and str(d.get("name", "")).lower().strip() == aname)]
    open_ = [w for w in wos if w.get("status") == "Open"]
    bds = [w for w in wos if "breakdown" in str(w.get("type", "")).lower()]
    now = datetime.now()

    def age(w):
        c = pd.to_datetime(w.get("created_at"), errors="coerce")
        return None if pd.isna(c) else (now - c).days
    rep = []
    for w in bds:
        c, e = pd.to_datetime(w.get("created_at"), errors="coerce"), pd.to_datetime(w.get("closed_date"), errors="coerce")
        if w.get("status") == "Closed" and pd.notna(c) and pd.notna(e): rep.append((e - c).total_seconds() / 3600)
    st.caption(f"✅ Unlocked · data published {str(snap['published_at'])[:16]}")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Open WOs", len(open_)); m2.metric("Breakdowns", len(bds))
    m3.metric("MTTR (h)", f"{sum(rep) / len(rep):.1f}" if rep else "-")
    m4.metric("Total cost", f"${sum(float(w.get('total_cost') or 0) for w in wos):,.0f}")
    with st.expander("ℹ️ Asset data", expanded=False):
        if asset:
            st.dataframe(pd.DataFrame({"Field": [k.title() for k in asset if k not in ("id", "created_at")], "Value": [str(v) for k, v in asset.items() if k not in ("id", "created_at")]}), use_container_width=True, hide_index=True)
        else: st.write(f"{name} · {tag} (not found in the published asset list)")

    used = [u for u in snap.get("parts_used", []) if str(u.get("tag", "")).lower() == tl]
    t_open, t_pm, t_hist, t_docs, t_used, t_parts = st.tabs([f"🔧 Open WOs ({len(open_)})", f"📅 PM ({len(plans)})", "📜 History", f"📚 Documents ({len(docs)})", f"🔩 Parts used ({len(used)})", "📦 Stock"])
    with t_open:
        if not open_: st.success("No open work orders for this machine. 🎉")
        for w in sorted(open_, key=lambda x: str(x.get("created_at", ""))):
            a = age(w)
            with st.expander(f"{w.get('custom_id', '')} · {w.get('type', '')} · {w.get('priority', 'Normal')}" + (f" · {a} d old" if a is not None else "")):
                if isinstance(w.get("description"), str) and w["description"]: st.write(w["description"])
                info = [str(w[k]) for k in ("reported_by", "machine_status") if isinstance(w.get(k), str) and w.get(k)]
                if info: st.caption("Reported by " + " · ".join(info))
                for t in (w.get("tasks_data") or []):
                    if isinstance(t, dict): st.markdown(("✅ " if t.get("done") else "⬜ ") + str(t.get("desc", "")))
                if isinstance(w.get("photo_b64"), str) and w["photo_b64"]:
                    try: st.image(base64.b64decode(w["photo_b64"]), width=300)
                    except Exception: pass
                for p in (w.get("parts_used") or []):
                    if isinstance(p, dict): st.caption(f"🔩 {p.get('name')} × {p.get('qty')}")
    with t_pm:
        if not plans: st.info("No PM plans for this machine.")
        for p in plans:
            with st.expander(f"{p.get('frequency', '')} PM · next due {p.get('next_due', '-')} · {p.get('status', '')}"):
                for t in (p.get("tasks_data") or []):
                    if isinstance(t, dict): st.markdown("▫️ " + str(t.get("desc", "")))
    with t_hist:
        cl = sorted([w for w in wos if w.get("status") == "Closed"], key=lambda x: str(x.get("closed_date", "")), reverse=True)[:20]
        if not cl: st.info("No closed work orders yet.")
        else:
            st.dataframe(one_based(pd.DataFrame([{"WO #": w.get("custom_id"), "Type": w.get("type"), "Description": w.get("description"), "Closed": str(w.get("closed_date", ""))[:16],
                                                  "Technician": w.get("fixing_technician"), "Cost ($)": w.get("total_cost")} for w in cl])), use_container_width=True)
    with t_docs:
        if not docs: st.info("No documents uploaded for this machine yet.")
        for d in sorted(docs, key=lambda x: (x.get("doc_type", ""), x.get("title", ""))):
            path = os.path.join(DOCS_DIR, str(d["id"]) + ".bin")
            with st.expander(f"{DOC_ICONS.get(d.get('doc_type'), '📎')} {d.get('title')} · {d.get('doc_type')} · {_fmt_size(d.get('size'))}"):
                if not os.path.exists(path):
                    st.warning("File not available on the server. Ask the manager to publish again."); continue
                is_img = str(d.get("mime", "")).startswith("image/")
                if is_img and (d.get("size") or 0) < 3_000_000:
                    with open(path, "rb") as fh: st.image(fh.read())
                if st.checkbox("Prepare download", key=f"prep_{d['id']}"):
                    with open(path, "rb") as fh: data = fh.read()
                    st.download_button(f"⬇️ {d.get('filename')}", data, d.get("filename"), d.get("mime") or "application/octet-stream", key=f"dl_{d['id']}")
    with t_used:
        if not used: st.info("No spare parts issued to this machine in the loaded spend reports.")
        else:
            ud = pd.DataFrame(used)
            gu = ud.groupby(["item_code", "description"]).agg(Qty=("qty", "sum"), Issues=("qty", "size"), Last=("date", "max")).reset_index().sort_values("Last", ascending=False)
            st.caption(f"{len(used)} issue lines · {len(gu)} different parts (most recent first)")
            st.dataframe(one_based(gu.rename(columns={"item_code": "Item code", "description": "Description", "Last": "Last issued"})), use_container_width=True)
    with t_parts:
        q = st.text_input("Search spare parts", key="tech_part_q").strip().lower()
        parts = [p for p in snap.get("spare_parts", []) if not q or q in str(p.get("name", "")).lower() or q in str(p.get("sku", "")).lower()][:40]
        if parts: st.dataframe(one_based(pd.DataFrame(parts).rename(columns=str.title)), use_container_width=True)
        else: st.info("No matching parts.")

_scan_tag = st.query_params.get("r")
if _scan_tag:
    scan_page(_scan_tag, st.query_params.get("n") or _scan_tag)
    st.stop()

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
            raw, _counts = read_upload_all(uploaded_file)
            n = normalize_assets_df(raw)
            return pd.DataFrame({
                "Machine Name": n["name"], "Brand": "Generic", "Manufacturer": "Generic",
                "Tag Number": n["tag"], "Frequency": "Standard", "Site": n["site"], "Section": n["section"],
                "Line": n["line"], "Factory": n["factory"]})
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

    _pending_qr = len(inbox_load())
    if _pending_qr:
        st.markdown(f"🚨 **{_pending_qr} new QR breakdown report(s)** - open *Breakdown Reporting*")
        if not st.session_state.get("_qr_toasted"):
            st.toast(f"{_pending_qr} new breakdown report(s) from QR scans", icon="🚨"); st.session_state._qr_toasted = True

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
         menu_options.append("💰 OPEX & Spend Analysis")
         menu_options.append("💼 Maintenance Manager")

    menu = st.radio("ERP Modules", menu_options, label_visibility="collapsed")

# --- AUTO-PUBLISH technician QR data (when a PIN is set) ---
if get_setting("tech_pin_hash", "") and get_setting("auto_publish", True) and st.session_state.get("pub_mark") != st.session_state.dirty:
    try:
        publish_snapshot(); st.session_state.pub_mark = st.session_state.dirty
    except Exception as _e:
        print(f"Publish error: {_e}")

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
    """Built-in Americana logo (PNG, transparent background)."""
    import base64
    return base64.b64decode(_AMERICANA_LOGO_B64)

def logo_on_white(logo_bytes, max_h=None):
    """Logo flattened on white (RGB) - used for printed headers."""
    lg = Image.open(io.BytesIO(logo_bytes or default_logo_png())).convert("RGBA")
    bg = Image.new("RGBA", lg.size, (255, 255, 255, 255)); bg.alpha_composite(lg)
    return bg.convert("RGB")

@st.cache_data(show_spinner=False, max_entries=3000)
def make_qr_png(payload, logo_bytes=b""):
    """QR with HIGH error correction (30%) and the logo centred on a white rounded badge (22% of width)."""
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=8, border=2)
    qr.add_data(payload); qr.make(fit=True)
    img = qr.make_image(fill_color="#1b2a3a", back_color="white")
    img = (img.get_image() if hasattr(img, "get_image") else img).convert("RGB")
    W = img.size[0]
    logo = Image.open(io.BytesIO(logo_bytes or default_logo_png())).convert("RGBA")
    target = int(W * 0.34)
    logo.thumbnail((target, target), Image.LANCZOS)
    pad = max(4, int(target * 0.06))
    bw, bh = logo.size[0] + 2 * pad, logo.size[1] + 2 * pad
    badge = Image.new("RGBA", (bw, bh), (255, 255, 255, 0))
    ImageDraw.Draw(badge).rounded_rectangle((0, 0, bw - 1, bh - 1), radius=int(min(bw, bh) * 0.25), fill=(255, 255, 255, 255))
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
    payload = build_scan_url(get_app_url(), tag or aid, name or display_id)   # scan -> breakdown report form
    return display_id, name, loc, payload

def make_label_png(display_id, name, payload, logo_bytes=b""):
    """Single printable label image: header band, QR with logo, asset ID below."""
    qr = Image.open(io.BytesIO(make_qr_png(payload, logo_bytes))).convert("RGB")
    W = qr.size[0]; pad = 30; head = 120; foot = 150
    canvas = Image.new("RGB", (W + 2 * pad, head + W + foot), "white"); d = ImageDraw.Draw(canvas)
    hl = logo_on_white(logo_bytes); hh_px = head - 30
    hl = hl.resize((int(hl.size[0] * hh_px / hl.size[1]), hh_px), Image.LANCZOS)
    if hl.size[0] > canvas.size[0] - 40: hl = hl.resize((canvas.size[0] - 40, int(hl.size[1] * (canvas.size[0] - 40) / hl.size[0])), Image.LANCZOS)
    canvas.paste(hl, ((canvas.size[0] - hl.size[0]) // 2, (head - hl.size[1]) // 2))
    d.rectangle((0, head - 6, canvas.size[0], head), fill=(0, 94, 170))
    def center(text, y, font, fill):
        bb = d.textbbox((0, 0), text, font=font); d.text(((canvas.size[0] - (bb[2] - bb[0])) / 2 - bb[0], y), text, font=font, fill=fill)
    canvas.paste(qr, (pad, head + 5))
    f_id = _qr_font(44)
    while d.textbbox((0, 0), display_id, font=f_id)[2] > canvas.size[0] - 20 and f_id.size > 16:
        f_id = _qr_font(f_id.size - 4)
    center(display_id, head + W + 12, f_id, (27, 42, 58))
    center(name[:40], head + W + 72, _qr_font(26, bold=False), (90, 100, 110))
    center("Scan: report breakdown / maintenance info", head + W + 112, _qr_font(24), (214, 48, 49))
    d.rectangle((0, 0, canvas.size[0] - 1, canvas.size[1] - 1), outline=(180, 190, 200), width=3)
    buf = io.BytesIO(); canvas.save(buf, "PNG"); return buf.getvalue()

def _lat(t): return str(t).encode("latin-1", "replace").decode("latin-1")

def create_qr_labels_pdf(df, logo_bytes=b"", cols=3, rows=4, progress=None):
    """A4 sheet(s) of labels ready to print and stick on machines."""
    pdf = FPDF("P", "mm", "A4"); pdf.set_auto_page_break(False)
    mx, my = 8, 10
    cw, ch = (210 - 2 * mx) / cols, (297 - 2 * my) / rows
    per, hh = cols * rows, 10
    tmp = tempfile.mkdtemp(); n = len(df)
    hl = logo_on_white(logo_bytes); hl_path = os.path.join(tmp, "header_logo.png"); hl.save(hl_path, "PNG")
    lh = hh - 3.0; lw = lh * hl.size[0] / hl.size[1]
    if lw > cw - 10: lw = cw - 10; lh = lw * hl.size[1] / hl.size[0]
    for i, (_, row) in enumerate(df.iterrows()):
        if i % per == 0: pdf.add_page()
        k = i % per; x = mx + (k % cols) * cw; y = my + (k // cols) * ch
        display_id, name, loc, payload = asset_label_fields(row)
        pdf.set_draw_color(170, 180, 190); pdf.set_line_width(0.3); pdf.rect(x + 1, y + 1, cw - 2, ch - 2)
        pdf.image(hl_path, x + (cw - lw) / 2, y + 1 + (hh - lh) / 2, lw, lh)
        pdf.set_fill_color(0, 94, 170); pdf.rect(x + 1, y + 1 + hh - 0.6, cw - 2, 0.9, "F")
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
        pdf.set_font("Arial", "B", 6.5); pdf.set_text_color(214, 48, 49)
        pdf.cell(cw - 2, 3.5, "SCAN: REPORT / MAINTENANCE INFO", 0, 2, "C")
        if progress and (i % 5 == 0 or i == n - 1): progress((i + 1) / max(n, 1))
    try:
        os.remove(hl_path); os.rmdir(tmp)
    except Exception: pass
    return pdf.output(dest="S").encode("latin-1", "replace")


# =====================================================================
# --- OPEX / SPEND ANALYSIS ENGINE ---
# Reads the monthly ERP spare-parts issue report (Dept = "Machine ((TAG))", General Dept = "Plant-Section-Line"),
# normalises it, links it to the asset master by tag number and provides the analytics used by the
# "OPEX & Spend Analysis" module, Migo and the technician QR page.
# =====================================================================
ITEM_GROUPS = {"6081": "Electrical & Electronics", "6082": "Mechanical", "6083": "Pneumatic, Hoses & Pipes", "6084": "Consumables & Materials"}
PLANT_BY_ACCOUNT = {"30001": "Snacks", "30002": "Pellet", "30000": "Central Workshops"}
AR_DEPT = {"الورش": "Workshops", "ماكينات البالت": "Pellet Machines", "ورشة لف المواتير": "Motor Rewinding Workshop",
           "ورشة": "Workshop", "عام": "General (no machine)"}
MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}

# Part-category rules (Arabic + English keywords) - first match wins; user rules (Settings) are checked first.
CATEGORY_RULES = [
    ("Printing & Coding", r"طباعة|طباعه|ايشيدا|إيشيدا|اشيدا|ماركم|markem|دومينو|domino|print|حبر|\bink\b|ريبون|ribbon|سلونايت|كارج|صداده|thermal|ثيرمال"),
    ("PLC, Cards & Electronics", r"\bplc\b|pwb|كارت|كارته|كارتة|\bcard\b|6es7|\bhmi\b|شاشة|screen|حساس|sensor|ريلاى|ريلية|ريليه|relay|\bscr\b|سولد استيت|كونتاكتور|contactor|فيوز|fuse|بريكر|breaker|تايمر|timer|ثرموكوبل|thermocouple|بروكسيمتى|proximity|encoder|ascon|tecnologic|سيفتى|safety|power supply|connector|socket|سوكت|كويل|coil|\bssr\b|بوتون|مفتاح كهرب"),
    ("Motors, Gearboxes & Drives", r"موتور|ماتور|motor|جير|gearbox|اكسترودر|extruder|مجموعة \d+|مجموعه \d+|inverter|انفرتر|درايف|\bdrive\b|مضخة|\bpump\b|مروحة|\bfan\b"),
    ("Bearings & Housings", r"رولمان|\bبلى\b|بلية|بلي|bearing|كرسى بلى|كرسي بلي|كراسي"),
    ("Oil Seals & Gaskets", r"اويل سيل|أويل سيل|oil seal|جوان|gasket|o-?ring|اورنج"),
    ("Belts & Chains", r"\bسير\b|حزام|\bbelt\b|جنزير|سلسلة|\bchain\b|كوبلنج|coupling"),
    ("Pneumatics, Hoses & Pipes", r"بستم|هواء|pneumatic|خرطوم|\bhose\b|سلندر|cylinder|فلتر|filter|ريجيوليتر|regulator|valve|محبس|صمام|solenoid|سولينويد|وصلة|fitting|\btube\b|\bpipe\b|ماسورة|مواسير|كوع|steam"),
    ("Mechanical Parts (gears, shafts, springs)", r"ترس|\bgear\b|عمود|shaft|فلانش|flange|سوسته|spring|ارتيلون|أرتيلون|مبروم|خوص|شيكا|بكره|بكرة|عجلة|wheel|roller|ساند|حلزون|بنطة|بنطه|سيخ|شبك|ذراع|غطاء|بالته|قاعده|قاعدة|هد ماكينة"),
    ("Heating, Sealing & Cutting", r"تيفلون|teflon|سكينه|سكينة|knife|blade|نصل|مقاومة|heater|\bjaw\b|فك|لحام|سيلكون|silicone|ربر|rubber|كاوتش|heat seal|band|سخان"),
    ("Fasteners & Hardware", r"مسمار|صامولة|وردة|بولت|\bbolt\b|\bscrew\b|\bnut\b|washer|زرجينة|جلبة|بوشة|\bbush\b|مفتاح الن|rivet|مسامير"),
    ("Cables & Wires", r"كابل|cable|سلك|\bwire\b|فيشة|\bplug\b|شريط"),
    ("Lubricants & Chemicals", r"زيت|\boil\b|شحم|grease|جريس|اسبراى|spray|\bcrc\b|لوكتايت|loctite|سوليشن|كيماوى"),
    ("Tools & Safety", r"مسدس|\btool\b|عدة|كماشة|مفك|drill|لقمة|قفاز|glove|خوذة"),
]

def _clean(s):
    return re.sub(r"[\u00a0\s]+", " ", str(s)).strip()

def translate_ar(s):
    s = _clean(s)
    if s in AR_DEPT: return AR_DEPT[s]
    m = re.search(r"ويندوز\s*(?:رقم\s*)?(\d+)", s)
    if m: return f"Windows Line {m.group(1)}"
    m = re.search(r"(?:اوشيدا|الأوشيدا|أوشيدا|الاوشيدا)\s*البالت\s*رقم\s*(\d+)", s)
    if m: return f"Ishida Pellet Line {m.group(1)}"
    if "F.E.N" in s: return "Pellet Line F.E.N"
    return s

def norm_section(s):
    t = _clean(s).lower()
    if t.startswith("pack"): return "Packaging"
    if t.startswith("proc"): return "Processing"
    return _clean(s).title()

def parse_general_dept(gd, acct=""):
    g = _clean(gd); parts = [p.strip() for p in g.split("-")]
    if len(parts) >= 3 and parts[0].lower() in ("snacks", "pellet"):
        return parts[0].title(), norm_section(parts[1]), "-".join(parts[2:]).strip()
    if len(parts) == 2 and parts[0].lower() in ("snacks", "pellet"):
        return parts[0].title(), norm_section(parts[1]), ""
    return PLANT_BY_ACCOUNT.get(str(acct).split(".")[0], "Other"), "Other", translate_ar(g)

def _find_header(df0):
    for i in range(min(len(df0), 30)):
        cells = [re.sub(r"\s+", " ", str(c)).strip().lower() for c in df0.iloc[i].tolist()]
        if ("item code" in cells or "description" in cells) and any(x in cells for x in ("net cost", "value", "total actual", "issue cost")):
            return i
    return None

def parse_opex_report(f):
    """Reads every sheet of the ERP report (header row detected automatically) -> normalised DataFrame."""
    name = getattr(f, "name", "report")
    if name.lower().endswith(".csv"):
        sheets = {"CSV": pd.read_csv(f, header=None, dtype=object)}
    else:
        if openpyxl is None: raise ImportError("Excel support is missing. Add 'openpyxl' to requirements.txt (or upload a CSV file).")
        sheets = pd.read_excel(f, sheet_name=None, header=None, dtype=object)
    frames = []
    for _, df0 in sheets.items():
        h = _find_header(df0)
        if h is None: continue
        d = df0.iloc[h + 1:].copy()
        d.columns = [re.sub(r"\s+", " ", str(c)).strip() for c in df0.iloc[h].tolist()]
        frames.append(d.dropna(how="all"))
    if not frames:
        raise ValueError("Could not find the report table. Expected columns like Item Code, Description, General Dept, Dept, Net Cost / Value, GL Date.")
    raw = pd.concat(frames, ignore_index=True)
    low = {}
    for c in raw.columns: low.setdefault(c.lower(), c)
    def col(*names):
        for n in names:
            if n in low: return raw[low[n]]
        return pd.Series([None] * len(raw), index=raw.index)
    num = lambda s: pd.to_numeric(s, errors="coerce")
    value = num(col("value")).fillna(num(col("total actual"))).fillna(num(col("net cost")))
    if value.notna().sum() == 0: raise ValueError("No spend column found (Value / Total Actual / Net Cost).")
    gl = pd.to_datetime(col("gl date", "date", "posting date"), errors="coerce")
    period = gl.dt.strftime("%Y-%m")
    if period.isna().all():   # fall back to Month column (+ year from file name or current year)
        yr = re.search(r"(20\d\d)", name); yr = int(yr.group(1)) if yr else datetime.now().year
        mm = col("month").astype(str).str.strip().str[:3].str.lower().map(MONTHS)
        period = mm.apply(lambda m: f"{yr}-{int(m):02d}" if pd.notna(m) else None)
        gl = pd.to_datetime(period + "-01", errors="coerce")
    out = pd.DataFrame(index=raw.index)
    out["item_code"] = col("item code", "item").astype(str).str.replace(r"\.0$", "", regex=True).str.strip().replace({"None": "", "nan": ""})
    out["description"] = col("description", "item description").map(lambda x: _clean(x) if x is not None and str(x) != "nan" else "")
    out["uom"] = col("uom").astype(str).str.strip().replace({"None": "", "nan": ""})
    gd = col("general dept", "general department").map(_clean)
    dept = col("dept", "department", "cost center").map(_clean)
    out["general_dept"], out["dept"] = gd, dept
    acct = col("category", "account code").astype(str).str.replace(r"\.0$", "", regex=True)
    out["account_code"] = acct.replace({"None": "", "nan": ""})
    tm = dept.str.extract(r"^(.*?)\s*\(\(\s*([^()]*?)\s*\)\)\s*$")
    tag = tm[1].fillna(dept.str.extract(r"(AMER-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+)")[0]).fillna("").str.upper().str.strip()
    out["tag"] = tag
    out["machine"] = [(m if isinstance(m, str) and m else translate_ar(dp)) for m, dp in zip(tm[0], dept)]
    parsed = [parse_general_dept(g, a) for g, a in zip(gd, out["account_code"])]
    out["plant"], out["section"], out["line"] = [p[0] for p in parsed], [p[1] for p in parsed], [p[2] for p in parsed]
    out["issue_qty"] = num(col("issue qty")); out["return_qty"] = num(col("return qty")).fillna(0)
    out["issue_cost"] = num(col("issue cost")); out["return_cost"] = num(col("return cost")).fillna(0)
    out["value"] = value
    out["gl_date"] = gl.dt.strftime("%Y-%m-%d"); out["period"] = period
    out["month"] = col("month").astype(str).str.strip().replace({"None": "", "nan": ""})
    out["quarter"] = col("quarter").astype(str).str.strip().replace({"None": "", "nan": ""})
    out["account_type"] = col("account type").astype(str).str.strip().replace({"None": "", "nan": ""})
    out["item_type"] = col("type").astype(str).str.strip().replace({"None": "", "nan": ""})
    out["budget"] = num(col("budget"))
    out["source"] = name
    out = out[out["value"].notna()].reset_index(drop=True)
    return out

def parse_custom_rules(text):
    rules = []
    for line in str(text or "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            if k.strip() and v.strip(): rules.append((v.strip(), re.escape(k.strip().lower())))
    return rules

def classify_part(desc, custom=()):
    s = str(desc).lower()
    for cat, pat in list(custom) + CATEGORY_RULES:
        if re.search(pat, s): return cat
    return "Other"

def opex_df():
    """Stored OPEX lines as an analysis DataFrame (adds item group, part category, master-match flag)."""
    rows = st.session_state.store.get("opex", [])
    cols = ["item_code", "description", "uom", "general_dept", "dept", "tag", "machine", "plant", "section", "line", "issue_qty", "return_qty",
            "issue_cost", "return_cost", "value", "gl_date", "period", "month", "quarter", "account_type", "item_type", "budget", "source",
            "item_group", "part_category", "machine_label", "in_master", "tagged"]
    if not rows: return pd.DataFrame(columns=cols)
    d = pd.DataFrame(rows)
    for c in ["issue_qty", "return_qty", "issue_cost", "return_cost", "value", "budget"]:
        d[c] = pd.to_numeric(d.get(c), errors="coerce")
    for c in ["tag", "machine", "plant", "section", "line", "description", "item_code", "general_dept", "dept", "period"]:
        d[c] = d[c].fillna("").astype(str) if c in d.columns else ""
    d["gl_date"] = pd.to_datetime(d["gl_date"], errors="coerce")
    d["item_group"] = d["item_code"].str[:4].map(ITEM_GROUPS).fillna("Other")
    custom = parse_custom_rules(get_setting("opex_rules", ""))
    mp = {u: classify_part(u, custom) for u in d["description"].unique()}
    d["part_category"] = d["description"].map(mp)
    assets = st.session_state.store.get("assets", [])
    master = {str(a.get("tag", "")).strip().upper() for a in assets if a.get("tag")}
    d["tagged"] = d["tag"] != ""
    d["in_master"] = d["tag"].str.upper().isin(master)
    d["machine_label"] = np.where(d["tagged"], d["machine"] + " (" + d["tag"] + ")", d["machine"] + " [" + d["line"] + "]")
    return d

def cur_label():
    return get_setting("currency", "EGP")

def fmt_cur(x, digits=0):
    try: return f"{cur_label()} {float(x):,.{digits}f}"
    except Exception: return str(x)

def fmt_short(x):
    """Compact amount for KPI cards: EGP 2.19 M / EGP 245 K / EGP 850."""
    try: v = float(x)
    except Exception: return str(x)
    a = abs(v)
    if a >= 1e6: return f"{cur_label()} {v / 1e6:,.2f} M"
    if a >= 1e4: return f"{cur_label()} {v / 1e3:,.0f} K"
    return f"{cur_label()} {v:,.0f}"

def machine_table(f):
    """One row per machine (tag) with spend, lines, share and cumulative share."""
    if f.empty: return pd.DataFrame()
    g = f.groupby("machine_label").agg(Machine=("machine", "first"), Tag=("tag", "first"), Plant=("plant", "first"), Section=("section", "first"),
                                       Line=("line", "first"), Spend=("value", "sum"), Lines=("value", "size"), In_Master=("in_master", "max")).reset_index()
    g = g.sort_values("Spend", ascending=False).reset_index(drop=True)
    tot = g["Spend"].sum()
    g["Share %"] = g["Spend"] / tot * 100 if tot else 0
    g["Cum %"] = g["Share %"].cumsum()
    return g

def opex_insights(f):
    """Returns (list of insight sentences, dict of anomaly tables)."""
    ins, tabs = [], {}
    if f.empty: return ins, tabs
    tot = f["value"].sum(); mt = machine_table(f[f["tagged"]])
    ins.append(f"Total spare-parts spend **{fmt_cur(tot)}** on **{len(f)}** issue lines across **{f['machine_label'].nunique()}** machines/cost centres.")
    untag = f.loc[~f["tagged"], "value"].sum()
    if tot and untag / tot > 0.05:
        ins.append(f"⚠️ **{untag / tot * 100:.1f}%** of the spend ({fmt_cur(untag)}) was issued to a department with **no machine tag** (general/workshop). Ask storekeepers to issue to a machine so costs can be traced.")
    if not mt.empty:
        n80 = int((mt["Cum %"] < 80).sum() + 1)
        ins.append(f"📊 **{n80} machines** ({n80 / len(mt) * 100:.0f}% of tagged machines) account for 80% of the tagged spend. Biggest: **{mt.iloc[0]['Machine']}** ({mt.iloc[0]['Tag']}) with {fmt_cur(mt.iloc[0]['Spend'])} ({mt.iloc[0]['Share %']:.1f}%).")
        q1, q3 = mt["Spend"].quantile(0.25), mt["Spend"].quantile(0.75); lim = q3 + 1.5 * (q3 - q1)
        out = mt[mt["Spend"] > lim][["Machine", "Tag", "Plant", "Line", "Spend", "Lines"]]
        if not out.empty:
            tabs["High-spend machines (statistical outliers)"] = out
            ins.append(f"🚩 **{len(out)} machines** are statistical outliers (spend above {fmt_cur(lim)}).")
    lim = max(f["value"].quantile(0.95), f["value"].mean() + 3 * f["value"].std(ddof=0)) if len(f) > 5 else f["value"].max()
    big = f[f["value"] >= lim].sort_values("value", ascending=False)
    if not big.empty:
        tabs["Big-ticket issues"] = big[["gl_date", "machine_label", "item_code", "description", "issue_qty", "value"]]
        ins.append(f"💰 **{len(big)} big-ticket issues** (>= {fmt_cur(lim)}) total **{fmt_cur(big['value'].sum())}** ({big['value'].sum() / tot * 100:.0f}% of spend).")
    rep = f[f["tagged"] & (f["item_code"] != "")].groupby(["machine_label", "item_code", "description"]).agg(Times=("value", "size"), Qty=("issue_qty", lambda s: -s.sum()), Spend=("value", "sum")).reset_index()
    rep = rep[rep["Times"] >= 3].sort_values("Spend", ascending=False)
    if not rep.empty:
        tabs["Repeat consumption (same part, same machine, 3+ issues)"] = rep
        ins.append(f"🔁 **{len(rep)} part/machine combinations** were issued 3+ times - possible chronic failures worth a root-cause review.")
    f2 = f.copy(); f2["uc"] = f2["value"] / f2["issue_qty"].abs().replace(0, np.nan)
    pv = f2[f2["item_code"] != ""].groupby(["item_code", "description"]).agg(Min=("uc", "min"), Max=("uc", "max"), Lines=("uc", "size")).reset_index()
    pv = pv[(pv["Lines"] >= 2) & (pv["Min"] > 0) & (pv["Max"] / pv["Min"] > 1.5)]
    if not pv.empty:
        pv["Max/Min"] = pv["Max"] / pv["Min"]
        tabs["Unit-cost variance (same item, different price)"] = pv.sort_values("Max/Min", ascending=False)
        ins.append(f"🏷️ **{len(pv)} items** show unit-cost differences above 50% between issues (stock valuation / purchase price check).")
    dup = f[f.duplicated(["item_code", "dept", "general_dept", "gl_date", "issue_qty", "value"], keep=False) & (f["item_code"] != "")]
    if not dup.empty:
        tabs["Possible duplicate issue lines"] = dup.sort_values(["item_code", "dept"])[["gl_date", "machine_label", "item_code", "description", "issue_qty", "value"]]
        ins.append(f"♊ **{len(dup)} lines** look like duplicates (same item, department, date, qty and value) - verify they are not double postings.")
    z = f[f["value"] <= 0]
    if not z.empty:
        tabs["Zero / negative value lines"] = z[["gl_date", "machine_label", "item_code", "description", "issue_qty", "value"]]
        ins.append(f"🧾 **{len(z)} lines** have zero or negative value (returns or items without a valuation).")
    nm = f[f["tagged"] & ~f["in_master"]]
    if not nm.empty:
        ins.append(f"🛠️ **{nm['tag'].nunique()} machine tags** in the report ({fmt_cur(nm['value'].sum())}) are **not in the Asset Master** - add them so their QR codes, PM and work orders can be linked.")
    return ins, tabs

def _wo_tag(r):
    m = re.search(r"(AMER-[A-Z0-9]+(?:-[A-Z0-9]+)+)", str(r.get("asset_name", "")).upper())
    if m: return m.group(1)
    meta = r.get("meta")
    return str(meta.get("tag", "")).upper() if isinstance(meta, dict) else ""

def cmms_link_table(f):
    """Per machine: OPEX spend vs CMMS work orders (count, breakdowns, recorded WO cost)."""
    mt = machine_table(f[f["tagged"]])
    if mt.empty: return mt
    wo = load_collection("work_orders")
    if wo.empty or "asset_name" not in wo.columns:
        mt["WOs"] = 0; mt["Open WOs"] = 0; mt["Breakdowns"] = 0; mt["WO cost"] = 0.0
    else:
        wo = wo.copy(); wo["tagx"] = wo.apply(_wo_tag, axis=1)
        wo["total_cost"] = pd.to_numeric(wo["total_cost"], errors="coerce").fillna(0)
        wo["bd"] = wo["type"].astype(str).str.contains("Breakdown", case=False, na=False)
        g = wo[wo["tagx"] != ""].groupby("tagx").agg(WOs=("id", "count"), **{"Open WOs": ("status", lambda s: int((s == "Open").sum()))}, Breakdowns=("bd", "sum"), **{"WO cost": ("total_cost", "sum")})
        mt = mt.merge(g, how="left", left_on=mt["Tag"].str.upper(), right_index=True).drop(columns=["key_0"], errors="ignore")
        for c in ["WOs", "Open WOs", "Breakdowns", "WO cost"]: mt[c] = mt[c].fillna(0)
    mt["Spend per breakdown"] = np.where(mt["Breakdowns"] > 0, mt["Spend"] / mt["Breakdowns"].replace(0, np.nan), np.nan)
    return mt

def opex_excel(f, ins, tabs, cmms):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame({"Insight": [re.sub(r"[*]", "", i) for i in ins]}).to_excel(xw, sheet_name="Insights", index=False)
        f.groupby(["plant", "section", "line"]).agg(Spend=("value", "sum"), Lines=("value", "size"), Machines=("machine_label", "nunique")).reset_index().sort_values("Spend", ascending=False).to_excel(xw, sheet_name="By Line", index=False)
        mt = machine_table(f)
        if not mt.empty: mt.drop(columns=["machine_label"]).to_excel(xw, sheet_name="By Machine", index=False)
        f.groupby("part_category").agg(Spend=("value", "sum"), Lines=("value", "size")).reset_index().sort_values("Spend", ascending=False).to_excel(xw, sheet_name="By Part Category", index=False)
        f.groupby("item_group").agg(Spend=("value", "sum"), Lines=("value", "size")).reset_index().sort_values("Spend", ascending=False).to_excel(xw, sheet_name="By Item Group", index=False)
        f.groupby(["item_code", "description"]).agg(Qty=("issue_qty", lambda s: -s.sum()), Spend=("value", "sum"), Machines=("machine_label", "nunique")).reset_index().sort_values("Spend", ascending=False).head(500).to_excel(xw, sheet_name="Top Items", index=False)
        if f["period"].nunique() > 1: f.groupby("period").agg(Spend=("value", "sum"), Lines=("value", "size")).reset_index().to_excel(xw, sheet_name="By Month", index=False)
        if cmms is not None and not cmms.empty: cmms.drop(columns=["machine_label"], errors="ignore").to_excel(xw, sheet_name="OPEX vs CMMS", index=False)
        for i, (k, v) in enumerate(tabs.items()):
            v.to_excel(xw, sheet_name=(re.sub(r"[\\/*?:\[\]]", "-", k.split(" (")[0])[:31] or f"Anomaly{i}"), index=False)
        un = f[f["tagged"] & ~f["in_master"]].groupby(["tag", "machine", "plant", "section", "line"]).agg(Spend=("value", "sum")).reset_index()
        if not un.empty: un.to_excel(xw, sheet_name="Not in Asset Master", index=False)
        for ws in xw.book.worksheets:
            ws.freeze_panes = "A2"
            for col_cells in ws.columns:
                w = max(len(str(c.value)) if c.value is not None else 0 for c in col_cells[:60])
                ws.column_dimensions[col_cells[0].column_letter].width = min(max(w + 2, 10), 60)
    return buf.getvalue()

def opex_pdf(f, ins):
    pdf = FPDF(); pdf.add_page()
    pdf.set_font("Arial", "B", 16); pdf.cell(0, 10, "Americana Foods - EP Plants", 0, 1)
    pdf.set_font("Arial", "B", 13); pdf.cell(0, 8, _lat("Spare Parts Spend (OPEX) Analysis"), 0, 1)
    pdf.set_font("Arial", "I", 9); pdf.cell(0, 6, _lat(f"Periods: {', '.join(sorted(f['period'].unique()))}  |  Generated {datetime.now():%Y-%m-%d %H:%M}"), 0, 1)
    pdf.line(10, pdf.get_y() + 1, 200, pdf.get_y() + 1); pdf.ln(4)
    pdf.set_font("Arial", "B", 11); pdf.cell(0, 7, "Key findings", 0, 1); pdf.set_font("Arial", "", 9)
    for i in ins:
        t = re.sub(r"[*]", "", i).encode("ascii", "ignore").decode().strip(" ")
        pdf.multi_cell(0, 5, _lat("- " + t)); pdf.ln(1)
    def table(title, df, widths, heads):
        pdf.ln(3); pdf.set_font("Arial", "B", 11); pdf.cell(0, 7, title, 0, 1)
        pdf.set_fill_color(220, 228, 236); pdf.set_font("Arial", "B", 8)
        for w, h in zip(widths, heads): pdf.cell(w, 6, h, 1, 0, "C", True)
        pdf.ln(); pdf.set_font("Arial", "", 8)
        for row in df.itertuples(index=False):
            for w, v in zip(widths, row):
                txt = f"{v:,.0f}" if isinstance(v, (int, float, np.floating, np.integer)) and not isinstance(v, bool) else _lat(str(v))[:int(w / 1.9)]
                pdf.cell(w, 5.5, txt, 1, 0, "R" if isinstance(v, (int, float, np.floating, np.integer)) else "L")
            pdf.ln()
    pl = f.groupby(["plant", "section", "line"]).agg(S=("value", "sum"), L=("value", "size")).reset_index().sort_values("S", ascending=False).head(20)
    table("Spend by plant / section / line", pl, [30, 35, 55, 35, 25], ["Plant", "Section", "Line", f"Spend ({cur_label()})", "Lines"])
    mt = machine_table(f[f["tagged"]]).head(15)
    if not mt.empty:
        table("Top 15 machines", mt[["Machine", "Tag", "Line", "Spend", "Share %"]].assign(**{"Share %": mt["Share %"].round(1)}), [45, 55, 30, 30, 25], ["Machine", "Tag", "Line", f"Spend ({cur_label()})", "Share %"])
    pc = f.groupby("part_category")["value"].sum().sort_values(ascending=False).reset_index()
    table("Spend by part category", pc, [110, 50], ["Category", f"Spend ({cur_label()})"])
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
    "spend_summary": ["how much did we spend on spare parts", "opex total", "spare parts spending summary", "spending analysis",
                      "total opex this month", "spare parts cost by plant", "how much did spare parts cost"],
    "spend_machines": ["top machines by spare parts spend", "which machines consume the most spare parts", "highest opex machines",
                       "spare parts cost of machine", "how much spare parts did this machine use"],
    "spend_category": ["spare parts spend by category", "what type of parts cost the most", "opex by item group",
                       "which part category is most expensive", "spend by part type"],
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
    ("how to upload manuals documents diagrams for a machine", "Go to **🛠️ Asset Master Data → 📚 Documents**, pick the machine (or all machines with the same name), choose the type (Manual, Diagram…), select the files and click **Save document(s)**."),
    ("how do technicians see work orders and manuals by scanning qr code", "Set a PIN in **🛠️ Asset Master Data → 📡 Technician Access** and publish. Technicians scan the machine QR, open the **🔧 Technician Info** tab and enter the PIN to see open work orders, PM plans, history, spare parts and documents."),
    ("how to upload opex spend report spare parts analysis", "Go to **💰 OPEX & Spend Analysis → Upload Report** (managers), choose the monthly spare-parts issue report (.xlsx) and click **Import report**. Then use the Overview, Machines, Parts, Insights and OPEX vs CMMS tabs, or ask me, e.g. *top machines by spare parts spend*."),
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
FOLLOW.update({
    "spend_summary": ["Top machines by spare parts spend", "Spare parts spend by category", "Give me an executive summary"],
    "spend_machines": ["Spare parts spend by category", "Which machines are at highest risk?", "Spare parts spending summary"],
    "spend_category": ["Top machines by spare parts spend", "Spare parts spending summary"],
})
DEFAULT_FOLLOW = ["Give me an executive summary", "Show open work orders", "List low stock items"]

# Regex rules: (pattern, intent) -> checked before the ML classifier (first match wins)
RULES = [
    (r"^(hi|hello|hey|salam|good (morning|afternoon|evening))\b[\s\w!.,]{0,14}$", "greeting"),
    (r"\b(thanks|thank you|thx)\b", "thanks"),
    (r"\bhow (do|can|to|should|could)\b.*\b(create|add|close|report|upload|import|save|resume|generate|download|change|set|export|view|find|review|print)\b", "app_howto"),
    (r"\bhow to\b", "app_howto"),
    (r"(opex|spare ?parts?|parts?)\b.*\b(categor|by type|by group|kind of)|\b(categor\w*|item group)\b.*\b(opex|spare ?parts?|spend)", "spend_category"),
    (r"\bopex\b.*\b(machine|asset|equipment)|\b(machines?|assets?|equipment)\b.*\b(opex|spare ?parts? (spend|cost|consum))|spare ?parts? (spend|cost|consum)\w* (of|for|on) ", "spend_machines"),
    (r"\bopex\b|spare ?parts? (spend|spending|cost|costs|expense|expenses|consumption|consumed)|spend(ing)? on spare", "spend_summary"),
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
    return {"wo": wo, "inv": inv, "assets": assets, "plans": plans, "opex": opex_df()}


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


def build_candidates(assets, wo, opex=None):
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
    if opex is not None and not opex.empty:
        for tg, mn in opex.loc[opex["tag"] != "", ["tag", "machine"]].drop_duplicates().itertuples(index=False):
            for k in (str(mn), str(tg)):
                cand.setdefault(k.strip().lower(), (str(mn), str(tg)))
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
                variants = {vl, norm_section(v).lower()} if col == "section" else {vl}   # "Packging" (typo in master) also matches "packaging"
                if vl and vl not in ("nan", "general", "unknown") and any(re.search(r"(?<![a-z0-9])" + re.escape(x) + r"(?![a-z0-9])", ql) for x in variants):
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
        tbl = f.groupby([c for c in ["factory", "site"] if c in f.columns]).size().reset_index(name="Assets").rename(columns=str.title)
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

# ---------- OPEX intents (spare-parts spend) ----------
def _opex_scope(ctx):
    d = ctx.get("opex")
    if d is None or d.empty: return d
    if ctx["time"]:
        s, e, _ = ctx["time"]; d = d[(d["gl_date"] >= s) & (d["gl_date"] <= e)]
    g = ctx["group"]
    if g.get("site"): d = d[d["plant"].str.lower() == g["site"].lower()]
    if g.get("section"): d = d[d["section"].str.lower() == norm_section(g["section"]).lower()]
    if g.get("line"): d = d[d["line"].str.lower() == g["line"].lower()]
    if ctx["assets_found"]:
        tg = re.findall(r"AMER-[A-Z0-9]+(?:-[A-Z0-9]+)+", ctx["ql"].upper())
        if tg: d = d[d["tag"].isin(tg)]
        else:
            names = {a["name"].lower() for a in ctx["assets_found"] if a["name"]}; tags = {a["tag"].upper() for a in ctx["assets_found"] if a["tag"]}
            d = d[d["machine"].str.lower().isin(names) | d["tag"].isin(tags)]
    return d

_NO_OPEX = "No OPEX (spare-parts spend) report is loaded yet. Upload it in **💰 OPEX & Spend Analysis → Upload Report**."

def h_spend_summary(ctx):
    d = _opex_scope(ctx)
    if ctx.get("opex") is None or ctx["opex"].empty: return R(_NO_OPEX)
    if d.empty: return R("No spare-parts spend found" + scope_label(ctx) + ".")
    tot = d["value"].sum(); mt = machine_table(d[d["tagged"]])
    pl = d.groupby("plant")["value"].sum().sort_values(ascending=False)
    tbl = pl.reset_index(); tbl.columns = ["Plant", f"Spend ({cur_label()})"]
    top = f" Top machine: **{mt.iloc[0]['Machine']}** ({mt.iloc[0]['Tag']}) with {fmt_cur(mt.iloc[0]['Spend'])}." if not mt.empty else ""
    return R(f"Spare-parts spend{scope_label(ctx)}: **{fmt_cur(tot)}** on **{len(d)}** issue lines across **{d['machine_label'].nunique()}** machines/cost centres.{top} Periods: {', '.join(sorted(d['period'].unique()))}.",
             tbl, chart_spec("pie", pl.index, pl.values, "Spare-parts spend by plant") if charts_ok(ctx) else None)

def h_spend_machines(ctx):
    d = _opex_scope(ctx)
    if ctx.get("opex") is None or ctx["opex"].empty: return R(_NO_OPEX)
    if d.empty: return R("No spare-parts spend found" + scope_label(ctx) + ".")
    if ctx["assets_found"]:
        parts = d.groupby(["item_code", "description"]).agg(Qty=("issue_qty", lambda s: -s.sum()), Spend=("value", "sum")).reset_index().sort_values("Spend", ascending=False).head(15)
        return R(f"Spare-parts spend{scope_label(ctx)}: **{fmt_cur(d['value'].sum())}** on **{len(d)}** issue lines ({d['machine_label'].nunique()} machine(s)). Biggest parts below.", parts.rename(columns={"item_code": "Item code", "description": "Description"}))
    n, _ = ctx["topn"]; mt = machine_table(d[d["tagged"]]).head(n)
    if mt.empty: return R("No machine-tagged spend found.")
    tbl = mt[["Machine", "Tag", "Plant", "Line", "Spend", "Share %"]]
    return R(f"Top **{len(mt)}** machines by spare-parts spend{scope_label(ctx)}: **{mt.iloc[0]['Machine']}** leads with **{fmt_cur(mt.iloc[0]['Spend'])}** ({mt.iloc[0]['Share %']:.1f}%).",
             tbl, chart_spec("barh", mt["Machine"] + " " + mt["Tag"].str[-7:], mt["Spend"], "Top machines by spare-parts spend") if charts_ok(ctx) else None)

def h_spend_category(ctx):
    d = _opex_scope(ctx)
    if ctx.get("opex") is None or ctx["opex"].empty: return R(_NO_OPEX)
    if d.empty: return R("No spare-parts spend found" + scope_label(ctx) + ".")
    g = d.groupby("part_category")["value"].sum().sort_values(ascending=False)
    tbl = g.reset_index(); tbl.columns = ["Part category", f"Spend ({cur_label()})"]; tbl["Share %"] = (g.values / g.sum() * 100).round(1)
    return R(f"Biggest spare-parts category{scope_label(ctx)}: **{g.index[0]}** with **{fmt_cur(g.iloc[0])}** ({g.iloc[0] / g.sum() * 100:.0f}% of spend).",
             tbl, chart_spec("barh", g.index, g.values, "Spend by part category") if charts_ok(ctx) else None)


HANDLERS = {
    "spend_summary": h_spend_summary, "spend_machines": h_spend_machines, "spend_category": h_spend_category,
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
    cand = build_candidates(data["assets"], data["wo"], data.get("opex"))
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
           "chart": want_chart, "table_only": table_only, "faq_m": faq_m, "opex": data.get("opex")}
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
    tab1, tab2, tab3, tab4, tab5 = st.tabs(["📤 Upload Master Data", "📋 Master Data", "🖨️ QR Generator", "📚 Documents", "📡 Technician Access"])
    df_assets = load_collection('assets')

    with tab1:
        st.subheader("Import Assets from File")
        uploaded_file = st.file_uploader("Choose a file (all tabs are read: Snacks, Pellet and any future sub-plant tab)", type=['csv', 'xlsx'], key="asset_import_upload")
        if uploaded_file is not None:
            try:
                raw_up, tab_counts = read_upload_all(uploaded_file)
                df_upload = normalize_assets_df(raw_up)
                st.success("Tabs found: " + " · ".join(f"**{k}** ({v} rows)" for k, v in tab_counts.items()))
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Total rows", len(df_upload)); m2.metric("Tabs / sub-plants", len(tab_counts))
                m3.metric("Factories", df_upload["factory"].replace("", pd.NA).nunique())
                m4.metric("Duplicate tags", int(df_upload[df_upload["tag"] != ""]["tag"].str.lower().duplicated().sum()))
                if not df_upload.empty:
                    st.write("Summary (Factory › Site):")
                    summ = df_upload.groupby(["factory", "site"]).size().reset_index(name="Assets")
                    st.dataframe(one_based(summ.rename(columns=str.title)), use_container_width=True)
                st.write("Preview:"); st.dataframe(one_based(df_upload.head(10).rename(columns=str.title)))
                if st.button("Import Data"):
                    existing = {str(d.get("tag", "")).strip().lower(): d for d in st.session_state.store.get("assets", []) if str(d.get("tag", "")).strip()}
                    added = updated = skipped = 0
                    seen = set()
                    for rec in df_upload.to_dict("records"):
                        key = rec["tag"].lower()
                        if key and key in seen: skipped += 1; continue      # duplicate tag inside the file
                        if key: seen.add(key)
                        data = {k: rec[k] for k in ["name", "tag", "factory", "site", "section", "line"] if rec[k] != ""}
                        data.setdefault("name", "N/A"); data.setdefault("site", "General")
                        if key and key in existing:                          # already stored -> update, never duplicate
                            existing[key].update(data); updated += 1; st.session_state.dirty += 1
                        else:
                            data["created_at"] = str(datetime.now())
                            save_document('assets', data, notify=False); added += 1
                    log_notification(f"🛠️ Assets imported: {added} new, {updated} updated", toast=False)
                    st.success(f"Imported! {added} new · {updated} updated · {skipped} duplicate rows skipped"); time.sleep(1.5); st.rerun()
            except Exception as e: st.error(f"Error: {e}")

        st.divider(); st.subheader("Add Single Asset Manually")
        with st.form("new_asset_manual"):
            a_name = st.text_input("Machine Name"); a_tag = st.text_input("Tag Number")
            a_site = st.selectbox("Site", ["Snacks", "Pellet", "Processing", "Other"])
            if st.form_submit_button("Save Asset"):
                save_document('assets', {"name": a_name, "tag": a_tag, "site": a_site, "created_at": str(datetime.now())})
                st.success("Saved"); st.rerun()

    with tab2:
        if not df_assets.empty:
            view_df = df_assets
            fcols = st.columns(4)
            for (col, lab), box in zip([("factory", "Factory"), ("site", "Site / Sub-plant"), ("section", "Section"), ("line", "Line")], fcols):
                if col in view_df.columns:
                    opts = sorted(view_df[col].dropna().astype(str).unique())
                    sel = box.multiselect(lab, opts, key=f"md_f_{col}")
                    if sel: view_df = view_df[view_df[col].astype(str).isin(sel)]
            if "site" in df_assets.columns:
                counts = df_assets["site"].astype(str).value_counts()
                mc = st.columns(min(len(counts) + 1, 6))
                mc[0].metric("Total assets", len(df_assets))
                for i, (k, v) in enumerate(counts.items()):
                    if i + 1 < len(mc): mc[i + 1].metric(k, int(v))
            st.caption(f"Showing {len(view_df)} of {len(df_assets)} assets")
            st.dataframe(one_based(view_df), use_container_width=True)
        else: st.info("No assets found.")

    with tab3:
        st.subheader("Generate Machine QR Codes")
        if df_assets.empty:
            st.warning("Add assets first.")
        else:
            with st.expander("🖼️ Logo on the QR labels (Americana logo built-in)", expanded=False):
                lg = st.file_uploader("Optional: replace the built-in logo (PNG with transparent background works best)", type=["png", "jpg", "jpeg"], key="qr_logo_upload")
                if lg is not None: st.session_state.qr_logo = lg.getvalue()
                if st.session_state.get("qr_logo") and st.button("Back to built-in logo", key="qr_logo_reset"):
                    st.session_state.qr_logo = b""; st.rerun()
                st.caption("The Americana logo is built in and appears in the centre of every QR and in the label header. High error-correction (30%) keeps every code scannable.")
            logo_bytes = st.session_state.get("qr_logo", b"")

            with st.expander("🔗 Scan-to-report link (what the QR opens)", expanded=False):
                cur_url = get_app_url()
                new_url = st.text_input("App address operators open after scanning", value=cur_url)
                if new_url.strip().rstrip("/") != cur_url and new_url.strip():
                    set_setting("app_url", new_url.strip().rstrip("/")); st.rerun()
                st.caption("Example link encoded in a QR:"); st.code(build_scan_url(cur_url, "AMER-EP01-SP11-CLX", "Clextral"))
                st.caption("Scanning opens the breakdown form for that machine (no login). Labels printed before this change must be reprinted.")

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

    with tab4:
        st.subheader("📚 Technical Documents (manuals, diagrams, procedures)")
        docs = st.session_state.store.setdefault("documents", [])
        if df_assets.empty:
            st.warning("Add assets first.")
        else:
            df_assets["qr_display"] = df_assets["name"].astype(str) + " / " + df_assets["tag"].fillna("").astype(str)
            if "doc_up_n" not in st.session_state: st.session_state.doc_up_n = 0
            scope = st.radio("Document applies to", ["This asset only", "All assets with the same machine name"], horizontal=True)
            sel_asset = st.selectbox("Asset", df_assets["qr_display"].unique(), key="doc_asset")
            c_d1, c_d2 = st.columns(2)
            d_type = c_d1.selectbox("Document type", DOC_TYPES)
            d_title = c_d2.text_input("Title (optional - file name is used if empty)")
            up_files = st.file_uploader("Select files (PDF, images, Word, Excel, CAD…) - max 25 MB each", accept_multiple_files=True,
                                        type=["pdf", "png", "jpg", "jpeg", "docx", "doc", "xlsx", "xls", "pptx", "dwg", "dxf", "txt", "csv", "zip"], key=f"doc_up_{st.session_state.doc_up_n}")
            if st.button("💾 Save document(s)", key="doc_save"):
                if not up_files:
                    st.warning("Select at least one file.")
                else:
                    arow = df_assets[df_assets["qr_display"] == sel_asset].iloc[0]
                    saved = 0
                    for f in up_files:
                        raw = f.getvalue()
                        if len(raw) > 25 * 1024 * 1024:
                            st.error(f"{f.name} is larger than 25 MB - skipped."); continue
                        docs.append({"id": uuid.uuid4().hex, "title": (d_title.strip() or f.name) if len(up_files) == 1 else f.name, "doc_type": d_type,
                                     "scope": "tag" if scope.startswith("This") else "name", "tag": str(arow.get("tag", "")) if scope.startswith("This") else "",
                                     "name": str(arow.get("name", "")), "filename": f.name, "mime": f.type or "application/octet-stream", "size": len(raw),
                                     "uploaded_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "uploaded_by": st.session_state.get("user", ""),
                                     "data_b64": base64.b64encode(raw).decode()})
                        saved += 1
                    if saved:
                        st.session_state.dirty += 1; st.session_state.doc_up_n += 1
                        log_notification(f"📚 {saved} document(s) uploaded for {arow.get('name', '')}", toast=False)
                        st.success(f"Saved {saved} document(s)."); time.sleep(1); st.rerun()
        st.divider()
        total_mb = sum(d.get("size", 0) for d in docs) / 1048576
        st.markdown(f"**Stored documents: {len(docs)} · {total_mb:.1f} MB**")
        if total_mb > 50: st.warning("Documents are saved inside your data file - it is getting large. Consider keeping only the essential manuals.")
        if docs:
            q = st.text_input("🔎 Search documents", key="doc_q").strip().lower()
            shown = [d for d in docs if not q or q in (d.get("title", "") + d.get("name", "") + d.get("tag", "") + d.get("doc_type", "")).lower()]
            st.dataframe(one_based(pd.DataFrame([{"Title": d["title"], "Type": d["doc_type"], "Applies to": (d["tag"] or f"All '{d['name']}'") if d["scope"] == "tag" else f"All '{d['name']}'",
                                                  "Machine": d["name"], "File": d["filename"], "Size": _fmt_size(d["size"]), "Uploaded": d["uploaded_at"]} for d in shown])), use_container_width=True)
            if shown:
                pick = st.selectbox("Select a document", range(len(shown)), format_func=lambda i: f"{shown[i]['title']} ({shown[i]['doc_type']})", key="doc_pick")
                dd = shown[pick]; raw = base64.b64decode(dd.get("data_b64", ""))
                if str(dd.get("mime", "")).startswith("image/"): st.image(raw, width=320)
                b_a, b_b = st.columns(2)
                b_a.download_button("⬇️ Download", raw, dd["filename"], dd.get("mime") or "application/octet-stream", key="doc_dl_mgr", use_container_width=True)
                if b_b.button("🗑️ Delete document", key="doc_del", use_container_width=True):
                    docs.remove(dd); st.session_state.dirty += 1; st.rerun()

    with tab5:
        st.subheader("📡 Technician / Engineer QR Access")
        st.markdown("Scanning a machine QR opens a page with two tabs: **🚨 Report Breakdown** (operators, no PIN) and **🔧 Technician Info** "
                    "(PIN protected, read-only): open work orders, PM plans, history, spare parts availability and the uploaded documents for that machine.")
        pin_set = bool(get_setting("tech_pin_hash", ""))
        st.info("🔑 A technician PIN is set." if pin_set else "No PIN set yet - technician access is disabled until you set one.")
        p1, p2 = st.columns([3, 1])
        new_pin = p1.text_input("Set / change the technician PIN (min. 4 characters)", type="password", key="tech_pin_new")
        if p2.button("Save PIN", key="tech_pin_save"):
            if len(new_pin.strip()) < 4: st.error("The PIN must have at least 4 characters.")
            else:
                set_setting("tech_pin_hash", pin_hash(new_pin.strip())); publish_snapshot(); st.session_state.pub_mark = st.session_state.dirty
                st.success("PIN saved and data published. Share the PIN with technicians and engineers only."); st.rerun()
        auto = st.checkbox("Auto-publish after every change (recommended)", value=bool(get_setting("auto_publish", True)))
        if auto != bool(get_setting("auto_publish", True)):
            set_setting("auto_publish", auto)
        if st.button("📡 Publish now", key="tech_publish", disabled=not pin_set):
            publish_snapshot(); st.session_state.pub_mark = st.session_state.dirty; st.success("Published.")
        info = st.session_state.get("pub_info")
        if info:
            i1, i2, i3, i4 = st.columns(4)
            i1.metric("Last published", info["time"]); i2.metric("Assets", info["assets"]); i3.metric("Work orders", info["wos"]); i4.metric("Documents", info["docs"])
        st.caption("⚠️ Published data lives on the app server. If the app restarts or sleeps it is cleared - load your data file and it is published again automatically (when a PIN is set).")

# --- MODULE 8: OPEX & SPEND ANALYSIS ---
elif menu == "💰 OPEX & Spend Analysis":
    st.title("💰 OPEX & Spend Analysis")
    st.caption("Upload the monthly spare-parts issue report (ERP) to analyse spending per plant, line, machine, part category and compare it with the CMMS.")
    cur = cur_label()
    d_all = opex_df()
    st.markdown("""<style>
    div[data-testid="stMetricValue"] {font-size: 1.7rem !important;}
    div[data-testid="stMetricValue"] > div, div[data-testid="stMetricLabel"] p, div[data-testid="stMetricDelta"] > div {white-space: normal !important; overflow: visible !important; text-overflow: clip !important; word-break: break-word;}
    </style>""", unsafe_allow_html=True)

    @st.cache_data(show_spinner="Reading report…")
    def _parse_cached(raw_bytes, fname):
        bio = io.BytesIO(raw_bytes); bio.name = fname
        return parse_opex_report(bio)

    # ---------------- global filters ----------------
    f = d_all
    if not d_all.empty:
        with st.expander("🔎 Filters (apply to all tabs below)", expanded=False):
            fc = st.columns(5)
            sel_p = fc[0].multiselect("Period", sorted(d_all["period"].unique()), key="ox_f_period")
            sel_pl = fc[1].multiselect("Plant", sorted(d_all["plant"].unique()), key="ox_f_plant")
            sel_se = fc[2].multiselect("Section", sorted(d_all["section"].unique()), key="ox_f_section")
            sel_li = fc[3].multiselect("Line", sorted(d_all["line"].unique()), key="ox_f_line")
            sel_ig = fc[4].multiselect("Item group", sorted(d_all["item_group"].unique()), key="ox_f_group")
            for col_, sel_ in [("period", sel_p), ("plant", sel_pl), ("section", sel_se), ("line", sel_li), ("item_group", sel_ig)]:
                if sel_: f = f[f[col_].isin(sel_)]
        st.markdown(f"**{len(f):,} lines · {fmt_cur(f['value'].sum())}**" + ("" if len(f) == len(d_all) else f"  (filtered from {len(d_all):,} lines)"))

    t_up, t_ov, t_mc, t_pt, t_in, t_cm, t_ex = st.tabs(["📤 Upload Report", "📊 Overview", "🏭 Machines", "🔩 Parts & Items", "🚩 Insights", "🔗 OPEX vs CMMS", "📥 Export"])

    # ---------------- upload ----------------
    with t_up:
        st.subheader("Upload spend report")
        u1, u2 = st.columns([1, 2])
        new_cur = u1.text_input("Currency label", value=cur, help="Shown next to every amount in this module (the report values are used as-is).")
        if new_cur.strip() and new_cur.strip() != cur: set_setting("currency", new_cur.strip()); st.rerun()
        mode = u2.radio("If the file contains months that are already loaded", ["Replace those months (recommended)", "Add to the existing data"], horizontal=True)
        up = st.file_uploader("Spare-parts issue report (.xlsx / .csv) - all sheets are read, the header row is detected automatically", type=["xlsx", "xls", "csv"], key="opex_upload")
        if up is not None:
            try:
                new = _parse_cached(up.getvalue(), up.name)
                st.success(f"Report understood: **{len(new):,} lines**, period(s) **{', '.join(sorted(new['period'].dropna().unique()))}**, total **{fmt_cur(new['value'].sum())}**.")
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Lines", f"{len(new):,}"); m2.metric("Machines (tags)", new.loc[new["tag"] != "", "tag"].nunique())
                m3.metric("Tagged spend", f"{new.loc[new['tag'] != '', 'value'].sum() / max(new['value'].sum(), 1e-9) * 100:.1f}%")
                master = {str(a.get("tag", "")).strip().upper() for a in st.session_state.store.get("assets", []) if a.get("tag")}
                nm = new[(new["tag"] != "") & ~new["tag"].isin(master)]
                m4.metric("Tags not in Asset Master", nm["tag"].nunique())
                st.dataframe(one_based(new.head(8)[["gl_date", "plant", "section", "line", "machine", "tag", "item_code", "description", "issue_qty", "value"]]), use_container_width=True)
                loaded = {r.get("period") for r in st.session_state.store.get("opex", [])}
                overlap = sorted(loaded & set(new["period"].dropna().unique()))
                if overlap and mode.startswith("Replace"): st.info(f"Months already loaded will be replaced: {', '.join(overlap)}")
                elif overlap: st.warning(f"Months already loaded: {', '.join(overlap)}. 'Add' will duplicate their lines.")
                if st.button("✅ Import report", key="opex_import"):
                    store = st.session_state.store.setdefault("opex", [])
                    if mode.startswith("Replace"):
                        pers = set(new["period"].dropna().unique()); store[:] = [r for r in store if r.get("period") not in pers]
                    recs = json.loads(json.dumps(new.where(pd.notna(new), None).to_dict("records"), default=_json_default))
                    store.extend(recs); st.session_state.dirty += 1
                    log_notification(f"💰 OPEX report imported: {len(recs)} lines", toast=False)
                    st.success("Imported. Open the other tabs for the analysis - and remember to 📥 Save Data to File."); time.sleep(1); st.rerun()
                if not nm.empty:
                    um = nm.groupby(["tag", "machine", "plant", "section", "line"]).agg(Spend=("value", "sum")).reset_index().sort_values("Spend", ascending=False)
                    with st.expander(f"🛠️ {len(um)} machines in this report are not in the Asset Master ({fmt_cur(um['Spend'].sum())})"):
                        st.dataframe(one_based(um), use_container_width=True)
                        if st.button("➕ Add these machines to the Asset Master", key="opex_add_assets"):
                            for r in um.itertuples():
                                save_document("assets", {"name": r.machine, "tag": r.tag, "factory": "EP", "site": r.plant, "section": r.section, "line": r.line, "created_at": str(datetime.now())}, notify=False)
                            log_notification(f"🛠️ {len(um)} assets added from the OPEX report", toast=False)
                            st.success(f"Added {len(um)} machines."); time.sleep(1); st.rerun()
            except Exception as e:
                st.error(f"Could not read the report: {e}")
        if not d_all.empty:
            st.divider(); st.markdown("#### Loaded data")
            lp = d_all.groupby("period").agg(Lines=("value", "size"), Spend=("value", "sum"), Machines=("machine_label", "nunique"), Source=("source", "first")).reset_index()
            st.dataframe(one_based(lp), use_container_width=True)
            dc1, dc2 = st.columns([2, 1])
            del_p = dc1.selectbox("Remove a month", ["-"] + list(lp["period"]), key="opex_del_period")
            if dc2.button("🗑️ Remove month", key="opex_del_btn") and del_p != "-":
                st.session_state.store["opex"] = [r for r in st.session_state.store["opex"] if r.get("period") != del_p]; st.session_state.dirty += 1; st.rerun()
            with st.expander("⚙️ Part-category keywords (optional)"):
                st.caption("Add your own rules, one per line: `keyword = Category`. They are checked before the built-in Arabic/English rules. Example: `رولمان = Bearings & Housings`")
                rules_txt = st.text_area("Custom rules", value=get_setting("opex_rules", ""), height=110, key="opex_rules_box")
                if st.button("Save rules", key="opex_rules_save"): set_setting("opex_rules", rules_txt); st.rerun()

    if d_all.empty:
        for t in (t_ov, t_mc, t_pt, t_in, t_cm, t_ex):
            with t: st.info("Upload and import a spend report in the **📤 Upload Report** tab first.")
    elif f.empty:
        for t in (t_ov, t_mc, t_pt, t_in, t_cm, t_ex):
            with t: st.warning("No data for the selected filters.")
    else:
        mt_all = machine_table(f)
        mt_tag = machine_table(f[f["tagged"]])
        # ---------------- overview ----------------
        with t_ov:
            tot = f["value"].sum()
            top_m = mt_tag.iloc[0] if not mt_tag.empty else None
            avg_m = mt_tag["Spend"].mean() if not mt_tag.empty else 0
            k1 = st.columns(3)
            k1[0].metric("💰 Total spend", fmt_short(tot), help=fmt_cur(tot, 2))
            k1[1].metric("🧾 Issue lines", f"{len(f):,}")
            k1[2].metric("⚙️ Machines with spend", f.loc[f["tagged"], "machine_label"].nunique())
            k2 = st.columns(3)
            k2[0].metric("📐 Avg per machine", fmt_short(avg_m), help=fmt_cur(avg_m, 2))
            k2[1].metric("🏆 Top machine", top_m["Machine"] if top_m is not None else "-", delta=fmt_short(top_m["Spend"]) if top_m is not None else None, delta_color="off",
                         help=(f"{top_m['Machine']} ({top_m['Tag']}): {fmt_cur(top_m['Spend'], 2)}" if top_m is not None else None))
            k2[2].metric("🏷️ Spend with machine tag", f"{f.loc[f['tagged'], 'value'].sum() / tot * 100:.1f}%")
            c1, c2 = st.columns(2)
            by_pl = f.groupby("plant")["value"].sum().reset_index()
            c1.plotly_chart(px.pie(by_pl, names="plant", values="value", hole=0.5, title="Spend by plant"), use_container_width=True)
            by_sec = f.groupby(["plant", "section"])["value"].sum().reset_index()
            c2.plotly_chart(px.bar(by_sec, x="plant", y="value", color="section", barmode="stack", title="Spend by plant & section", labels={"value": f"Spend ({cur})"}), use_container_width=True)
            by_line = f.groupby(["plant", "section", "line"])["value"].sum().reset_index().sort_values("value", ascending=False).head(15)
            by_line["Line"] = by_line["plant"] + " · " + by_line["section"] + " · " + by_line["line"]
            c3, c4 = st.columns(2)
            c3.plotly_chart(px.bar(by_line.iloc[::-1], x="value", y="Line", orientation="h", title="Top 15 lines by spend", labels={"value": f"Spend ({cur})"}, color="value", color_continuous_scale="Blues"), use_container_width=True)
            by_ig = f.groupby("item_group")["value"].sum().reset_index()
            c4.plotly_chart(px.pie(by_ig, names="item_group", values="value", hole=0.5, title="By ERP item group (Electrical / Mechanical / Pneumatic / Consumables)"), use_container_width=True)
            by_pc = f.groupby("part_category")["value"].sum().sort_values().reset_index()
            st.plotly_chart(px.bar(by_pc, x="value", y="part_category", orientation="h", title="Spend by part category (auto-classified)", labels={"value": f"Spend ({cur})", "part_category": ""}, color="value", color_continuous_scale="Teal"), use_container_width=True)
            if f["period"].nunique() > 1:
                tr = f.groupby("period")["value"].sum().reset_index()
                tr["MoM %"] = tr["value"].pct_change() * 100
                st.plotly_chart(px.bar(tr, x="period", y="value", text_auto=".3s", title="Monthly spend trend", labels={"value": f"Spend ({cur})"}), use_container_width=True)
                st.dataframe(one_based(tr.rename(columns={"period": "Period", "value": "Spend"})), use_container_width=True)
            else:
                st.caption("Upload more months to see trends and month-over-month changes.")
            st.markdown("#### 🎯 Budget vs Actual")
            n_per = max(f["period"].nunique(), 1)
            rep_bud = f.groupby("plant")["budget"].sum(min_count=1) if f["budget"].notna().any() else None
            plants = sorted(f["plant"].unique()); bud = get_setting("opex_budget", {}) or {}
            if rep_bud is None:
                with st.expander("Set monthly budget per plant", expanded=not bud):
                    bc = st.columns(max(len(plants), 1)); newb = {}
                    for i, p_ in enumerate(plants): newb[p_] = bc[i].number_input(f"{p_} - monthly budget ({cur})", min_value=0.0, value=float(bud.get(p_, 0.0)), step=10000.0, key=f"bud_{p_}")
                    if st.button("Save budgets", key="bud_save"): set_setting("opex_budget", newb); st.rerun()
            rows = []
            for p_ in plants:
                actual = f.loc[f["plant"] == p_, "value"].sum()
                b = float(rep_bud.get(p_, 0)) if rep_bud is not None else float(bud.get(p_, 0)) * n_per
                if b > 0: rows.append({"Plant": p_, "Budget": b, "Actual": actual, "Variance": actual - b, "Variance %": (actual - b) / b * 100, "Status": "🔴 Over" if actual > b else "🟢 Within"})
            if rows:
                bt = pd.DataFrame(rows)
                st.plotly_chart(go.Figure([go.Bar(name="Budget", x=bt["Plant"], y=bt["Budget"]), go.Bar(name="Actual", x=bt["Plant"], y=bt["Actual"])]).update_layout(barmode="group", title=f"Budget vs actual ({n_per} period(s))"), use_container_width=True)
                st.dataframe(one_based(bt), use_container_width=True)
            else:
                st.caption("The report has no Budget values. Enter monthly budgets above to see variances.")

        # ---------------- machines ----------------
        with t_mc:
            n_top = st.slider("Show top N machines", 5, 100, 20, key="ox_topn")
            show = mt_all.head(n_top).copy(); show["Spend"] = show["Spend"].round(0); show["Share %"] = show["Share %"].round(1); show["Cum %"] = show["Cum %"].round(1)
            st.dataframe(one_based(show.drop(columns=["machine_label"]).rename(columns={"In_Master": "In Asset Master"})), use_container_width=True)
            par = mt_all.head(n_top)
            figp = go.Figure()
            figp.add_bar(x=par["Machine"], y=par["Spend"], name="Spend", marker_color="#0a6ed1")
            figp.add_scatter(x=par["Machine"], y=par["Cum %"], name="Cumulative %", yaxis="y2", mode="lines+markers", line=dict(color="#d63031"))
            figp.update_layout(title="Pareto - top machines", yaxis=dict(title=f"Spend ({cur})"), yaxis2=dict(title="Cumulative %", overlaying="y", side="right", range=[0, 105]), legend=dict(orientation="h"))
            st.plotly_chart(figp, use_container_width=True)
            tm = f[f["value"] > 0].copy(); tm["Machine"] = tm["machine_label"]
            st.plotly_chart(px.treemap(tm, path=["plant", "section", "line", "Machine"], values="value", color="value", color_continuous_scale="Blues", title="Spend map: Plant › Section › Line › Machine").update_layout(height=560), use_container_width=True)
            st.divider(); st.markdown("#### 🔍 Machine drill-down")
            choices = list(mt_all["machine_label"]); pick = st.selectbox("Machine", choices, key="ox_machine_pick")
            fm_ = f[f["machine_label"] == pick]
            r0 = mt_all[mt_all["machine_label"] == pick].iloc[0]
            q1, q2, q3, q4 = st.columns(4)
            q1.metric("Spend", fmt_cur(fm_["value"].sum())); q2.metric("Issue lines", len(fm_)); q3.metric("Share of total", f"{r0['Share %']:.1f}%"); q4.metric("Rank", f"#{int(mt_all.index[mt_all['machine_label'] == pick][0]) + 1}")
            dc1, dc2 = st.columns(2)
            pcs = fm_.groupby("part_category")["value"].sum().reset_index()
            dc1.plotly_chart(px.pie(pcs, names="part_category", values="value", hole=0.5, title="By part category"), use_container_width=True)
            if fm_["period"].nunique() > 1: dc2.plotly_chart(px.bar(fm_.groupby("period")["value"].sum().reset_index(), x="period", y="value", title="By month"), use_container_width=True)
            else: dc2.dataframe(one_based(pcs.rename(columns={"part_category": "Category", "value": "Spend"}).sort_values("Spend", ascending=False)), use_container_width=True)
            st.dataframe(one_based(fm_.sort_values("value", ascending=False)[["gl_date", "item_code", "description", "uom", "issue_qty", "value"]].rename(columns={"gl_date": "Date", "item_code": "Item code", "description": "Description", "uom": "UOM", "issue_qty": "Qty issued", "value": "Value"})), use_container_width=True)

        # ---------------- parts ----------------
        with t_pt:
            pc1, pc2 = st.columns(2)
            cat = f.groupby("part_category").agg(Spend=("value", "sum"), Lines=("value", "size"), Items=("item_code", "nunique")).reset_index().sort_values("Spend", ascending=False)
            cat["Share %"] = (cat["Spend"] / cat["Spend"].sum() * 100).round(1)
            pc1.markdown("**By part category**"); pc1.dataframe(one_based(cat.rename(columns={"part_category": "Category"})), use_container_width=True)
            ig = f.groupby("item_group").agg(Spend=("value", "sum"), Lines=("value", "size"), Items=("item_code", "nunique")).reset_index().sort_values("Spend", ascending=False)
            ig["Share %"] = (ig["Spend"] / ig["Spend"].sum() * 100).round(1)
            pc2.markdown("**By ERP item group**"); pc2.dataframe(one_based(ig.rename(columns={"item_group": "Item group"})), use_container_width=True)
            oth = f[f["part_category"] == "Other"].groupby("description")["value"].sum().sort_values(ascending=False).head(10)
            if not oth.empty and oth.sum() / f["value"].sum() > 0.03:
                with st.expander(f"❔ Unclassified items ({fmt_cur(f.loc[f['part_category'] == 'Other', 'value'].sum())}) - top 10 (add keywords in Upload Report › Part-category keywords)"):
                    st.dataframe(one_based(oth.reset_index().rename(columns={"description": "Description", "value": "Spend"})), use_container_width=True)
            st.markdown("**Top items by spend**")
            ti = f[f["item_code"] != ""].groupby(["item_code", "description", "part_category"]).agg(Qty=("issue_qty", lambda s: -s.sum()), Spend=("value", "sum"), Machines=("machine_label", "nunique"), Issues=("value", "size")).reset_index().sort_values("Spend", ascending=False)
            st.dataframe(one_based(ti.head(30).rename(columns={"item_code": "Item code", "description": "Description", "part_category": "Category"})), use_container_width=True)
            st.markdown("**🔎 Search items**")
            qtxt = st.text_input("Search description or item code (Arabic or English)", key="ox_item_q").strip().lower()
            if qtxt:
                hit = f[f["description"].str.lower().str.contains(qtxt, regex=False) | f["item_code"].str.contains(qtxt, regex=False)]
                st.caption(f"{len(hit)} lines · {fmt_cur(hit['value'].sum())}")
                st.dataframe(one_based(hit.sort_values("value", ascending=False)[["gl_date", "machine_label", "item_code", "description", "issue_qty", "value"]].head(200)), use_container_width=True)

        # ---------------- insights ----------------
        ins, anomalies = opex_insights(f)
        with t_in:
            st.subheader("🧠 Key findings")
            for i in ins: st.markdown(f"- {i}")
            for title, tdf in anomalies.items():
                with st.expander(f"{title} ({len(tdf)})"):
                    st.dataframe(one_based(tdf.head(200)), use_container_width=True)
            un = f[~f["tagged"]].groupby(["plant", "line", "machine"])["value"].sum().sort_values(ascending=False).reset_index()
            if not un.empty:
                with st.expander(f"Spend without a machine tag ({fmt_cur(un['value'].sum())})"):
                    st.dataframe(one_based(un.rename(columns={"value": "Spend", "machine": "Cost centre"})), use_container_width=True)

        # ---------------- CMMS link ----------------
        cmms = cmms_link_table(f)
        with t_cm:
            st.subheader("OPEX spend vs CMMS work orders")
            if cmms.empty:
                st.info("No tagged spend to compare.")
            else:
                st.caption("Work orders are matched to machines by tag number. Spare parts issued without any work order point to a CMMS discipline gap.")
                no_wo = cmms[(cmms["WOs"] == 0)].sort_values("Spend", ascending=False)
                s1, s2, s3 = st.columns(3)
                s1.metric("Machines with spend", len(cmms)); s2.metric("…without any work order", len(no_wo))
                s3.metric("Spend with no work order", fmt_cur(no_wo["Spend"].sum()), delta=f"{no_wo['Spend'].sum() / max(cmms['Spend'].sum(), 1e-9) * 100:.0f}% of tagged spend", delta_color="off")
                view = cmms.drop(columns=["machine_label", "In_Master", "Cum %"], errors="ignore").copy()
                view["Spend"] = view["Spend"].round(0); view["Spend per breakdown"] = view["Spend per breakdown"].round(0)
                st.markdown("**All machines**"); st.dataframe(one_based(view.head(300)), use_container_width=True)
                st.markdown("**🚩 Spare parts issued but no work order recorded**")
                st.dataframe(one_based(no_wo.drop(columns=["machine_label", "In_Master", "Cum %", "WOs", "Open WOs", "Breakdowns", "WO cost", "Spend per breakdown"], errors="ignore").head(50)), use_container_width=True)
                brk = cmms[cmms["Breakdowns"] > 0].sort_values("Spend", ascending=False).head(15)
                if not brk.empty:
                    st.plotly_chart(px.scatter(brk, x="Breakdowns", y="Spend", size="Spend", hover_name="Machine", color="Plant", title="Spend vs breakdowns (machines with breakdown work orders)"), use_container_width=True)

        # ---------------- export ----------------
        with t_ex:
            st.subheader("Export analysis")
            st.caption("The Excel workbook contains: insights, by line, machine, part category, item group, top items, month, OPEX vs CMMS, anomaly lists and machines not in the Asset Master.")
            e1, e2 = st.columns(2)
            if e1.button("📊 Build Excel workbook", key="ox_xl_build"):
                st.session_state.ox_xlsx = opex_excel(f, ins, anomalies, cmms)
            if st.session_state.get("ox_xlsx"):
                e1.download_button("⬇️ Download Excel", st.session_state.ox_xlsx, f"OPEX_Analysis_{datetime.now():%Y%m%d}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="ox_xl_dl")
            if e2.button("📄 Build PDF summary", key="ox_pdf_build"):
                st.session_state.ox_pdf = opex_pdf(f, ins)
            if st.session_state.get("ox_pdf"):
                e2.download_button("⬇️ Download PDF", st.session_state.ox_pdf, f"OPEX_Summary_{datetime.now():%Y%m%d}.pdf", "application/pdf", key="ox_pdf_dl")

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
                    _info = [str(row.get(k)) for k in ('reported_by', 'machine_status', 'priority') if isinstance(row.get(k), str) and row.get(k)]
                    if _info: st.caption("Reported via QR · " + " · ".join(_info))
                    _ph = row.get('photo_b64')
                    if isinstance(_ph, str) and _ph:
                        try: st.image(base64.b64decode(_ph), width=280)
                        except Exception: pass
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
    st.subheader("📥 Reports received from QR scans")
    pending = inbox_load()
    if pending:
        st.warning(f"{len(pending)} new report(s) waiting. Import them to create Open Breakdown work orders.")
        tbl = pd.DataFrame([{"Ref": p.get("ref"), "Time": str(p.get("submitted_at", ""))[:16], "Asset": p.get("name"), "Tag": p.get("tag"),
                             "Reported by": p.get("reported_by"), "Type": p.get("category"), "Machine": p.get("machine_status"),
                             "Priority": p.get("priority"), "Issue": p.get("description"), "Photo": "📷" if p.get("photo_b64") else ""} for p in pending])
        st.dataframe(one_based(tbl), use_container_width=True)
        qc1, qc2 = st.columns(2)
        if qc1.button("✅ Import all as Work Orders", key="inbox_import"):
            asset_docs = {str(d.get("tag", "")).strip().lower(): d for d in st.session_state.store.get("assets", [])}
            for p in pending:
                ad = asset_docs.get(str(p.get("tag", "")).strip().lower(), {})
                wo = {"custom_id": generate_overhaul_id(st.session_state.wo_counter),
                      "asset_name": f"{p.get('name', '')} {p.get('tag', '')}".strip(), "type": "Breakdown", "status": "Open",
                      "description": f"[{p.get('category', '')}] {p.get('description', '')}", "created_at": p.get("submitted_at", str(datetime.now())),
                      "priority": p.get("priority", "High"), "reported_by": p.get("reported_by", ""), "machine_status": p.get("machine_status", ""),
                      "source": "QR scan", "photo_b64": p.get("photo_b64", ""), "total_cost": 0.0,
                      "meta": {"tag": p.get("tag", ""), "section": ad.get("section", ""), "line": ad.get("line", ""), "location": ad.get("site", ""), "manufacturer": ad.get("manufacturer", ""), "brand": ad.get("brand", "")}}
                save_document("work_orders", wo, notify=False)
                st.session_state.wo_counter += 1
            inbox_remove([p["id"] for p in pending])
            log_notification(f"🚨 {len(pending)} breakdown report(s) imported from QR scans")
            st.success(f"Imported {len(pending)} report(s) as Open Breakdown work orders."); time.sleep(1); st.rerun()
        if qc2.button("🗑️ Discard all", key="inbox_discard"):
            inbox_remove([p["id"] for p in pending]); st.rerun()
        st.caption("Remember to **📥 Save Data to File** after importing.")
    else:
        st.info("No new QR reports.")
    st.divider(); st.subheader("Report manually")
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
    waterfall = go.Waterfall(
        measure=["relative", "relative", "relative", "total"],
        x=["Base", "Inflation", "Risk", "Total"],
        y=[base, base * inf, risk, total],
    )
    st.plotly_chart(go.Figure(waterfall), use_container_width=True)

# ---- END OF FILE (app.py) - if you can see this line on GitHub, the file was uploaded completely ----
