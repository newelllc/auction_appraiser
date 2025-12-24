import os
import uuid
import json
import boto3
import requests
import streamlit as st
import google.generativeai as genai
from datetime import datetime
from google.oauth2 import service_account
from google.auth.transport.requests import Request

# ==========================================
# 1. PAGE CONFIG & NEUTRAL BRANDING
# ==========================================
st.set_page_config(page_title="Newel Appraiser MVP", layout="wide")

def apply_newel_branding():
    st.markdown("""
        <link href="https://fonts.googleapis.com/css2?family=EB+Garamond:ital,wght@0,400;0,700;1,400;1,700&display=swap" rel="stylesheet">
        <style>
        /* Global Reset */
        .stApp {
            background-color: #FBF5EB !important;
            font-family: 'EB Garamond', serif !important;
        }
        .stApp * {
            color: #1C1C1E !important;
            font-family: 'EB Garamond', serif !important;
        }

        /* Sidebar Branding */
        [data-testid="stSidebar"] {
            background-color: #F8F2E8 !important;
            border-right: 1px solid #C2C2C2;
        }
        .sidebar-logo {
            color: #8B0000 !important;
            font-size: 2.8rem;
            font-weight: 700;
            text-align: center;
            letter-spacing: 0.15em;
            margin-bottom: 0px;
            text-transform: uppercase;
        }

        /* Headers - Newel Red */
        h1, h2, h3, .brand-header {
            color: #8B0000 !important;
            text-transform: uppercase;
            letter-spacing: 0.12em;
            font-weight: 700 !important;
        }

        /* Primary Action Buttons (Rectangular Black) */
        div.stButton > button {
            background-color: #1C1C1E !important;
            color: #FBF5EB !important;
            border-radius: 0px !important;
            border: none !important;
            padding: 0.9rem 2.2rem !important;
            font-weight: 700;
            text-transform: uppercase;
            width: 100%;
            letter-spacing: 0.1em;
        }
        div.stButton > button:hover {
            background-color: #8B0000 !important;
            color: #FBF5EB !important;
        }

        /* File Uploader Clean-up */
        [data-testid="stFileUploader"] { background-color: transparent !important; }
        [data-testid="stFileUploader"] section {
            background-color: #FBF5EB !important;
            border: 1px dashed #C2C2C2 !important;
        }

        /* Results Display */
        .result-card {
            background-color: white !important;
            padding: 24px;
            border: 1px solid #C2C2C2;
            margin-bottom: 18px;
            border-radius: 0px;
        }
        .pill {
            background-color: #EFDAAC !important;
            padding: 6px 14px;
            border-radius: 24px;
            font-weight: 700;
            color: #1C1C1E !important;
            display: inline-block;
            margin: 6px 6px 0 0;
            font-size: 0.9rem;
        }
        a {
            color: #8B0000 !important;
            text-decoration: underline;
            font-weight: 700;
        }
        </style>
    """, unsafe_allow_html=True)

apply_newel_branding()

# ==========================================
# 2. CORE UTILITIES
# ==========================================
def _get_secret(name: str) -> str:
    if name in st.secrets: return str(st.secrets[name])
    val = os.getenv(name)
    if not val: raise RuntimeError(f"Missing required secret: {name}")
    return val

# ==========================================
# 3. SERVICE: GEMINI CLASSIFICATION
# ==========================================
def upgrade_comps_with_gemini(matches: list[dict]) -> list[dict]:
    genai.configure(api_key=_get_secret("GEMINI_API_KEY"))
    model = genai.GenerativeModel("gemini-2.0-flash")
    context = [{"title": m.get("title"), "source": m.get("source")} for m in matches]
    prompt = f"Appraiser Expert: Classify matches into 'auction' or 'retail'. Extract: auction_low, auction_high, retail_price. Data: {json.dumps(context)}. Return JSON with key 'results'."
    try:
        response = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        ai_data = json.loads(response.text).get("results", [])
        for i, m in enumerate(matches):
            if i < len(ai_data): m.update(ai_data[i])
        return matches
    except Exception as e:
        st.error(f"AI Classification Error: {e}")
        return matches

# ==========================================
# 4. SERVICE: EXPORT
# ==========================================
def export_to_google_sheets(results: dict):
    sheet_id = _get_secret("GOOGLE_SHEET_ID")
    trace = results.get("traceability", {})
    matches = trace.get("search_summary", {}).get("top_matches", [])
    img_url = trace.get("s3", {}).get("presigned_url", "")
    ts = results.get("timestamp")

    auctions = [m for m in matches if m.get("kind") == "auction"][:3]
    retails = [m for m in matches if m.get("kind") == "retail"][:3]

    def build_row(items, is_auc):
        row = [ts, f'=IMAGE("{img_url}")', img_url]
        for i in range(3):
            if i < len(items):
                m = items[i]
                row.extend([m.get("title"), m.get("link")])
                if is_auc: row.extend([m.get("auction_low"), m.get("auction_high"), m.get("auction_reserve")])
                else: row.append(m.get("retail_price"))
            else: row.extend([""] * (5 if is_auc else 3))
        return row

    sa_info = st.secrets["google_service_account"]
    creds = service_account.Credentials.from_service_account_info(sa_info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    creds.refresh(Request())

    for tab, items, is_auc in [("Auction", auctions, True), ("Retail", retails, False)]:
        requests.post(f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{tab}!A:Z:append",
                      params={"valueInputOption": "USER_ENTERED"}, headers={"Authorization": f"Bearer {creds.token}"},
                      json={"values": [build_row(items, is_auc)]}, timeout=30)

# ==========================================
# 5. UI COMPONENTS
# ==========================================
with st.sidebar:
    st.markdown("<div class='sidebar-logo'>NEWEL</div>", unsafe_allow_html=True)
    st.divider()
    sku = st.session_state.get("uploaded_image_meta", {}).get("filename", "N/A")
    st.markdown(f"**SKU Label:** `{sku}`")
    if st.button("START NEW APPRAISAL"):
        for key in st.session_state.keys(): del st.session_state[key]
        st.rerun()

st.markdown("<h1 class='brand-header'>Newel Appraiser</h1>", unsafe_allow_html=True)

# 1. Upload
st.header("1. Upload Item Image")
uploaded_file = st.file_uploader("Upload item photo for appraisal", type=["jpg", "jpeg", "png"])

if uploaded_file:
    st.session_state["uploaded_image_bytes"] = uploaded_file.getvalue()
    st.session_state["uploaded_image_meta"] = {"filename": uploaded_file.name, "content_type": uploaded_file.type}
    st.image(uploaded_file, width=420)

# 2. Run
st.header("2. Run Appraisal")
if st.button("RUN APPRAISAL", disabled=not uploaded_file):
    with st.spinner("Analyzing visually and classifying sources..."):
        s3 = boto3.client("s3", region_name=_get_secret("AWS_REGION"), 
                          aws_access_key_id=_get_secret("AWS_ACCESS_KEY_ID"), 
                          aws_secret_access_key=_get_secret("AWS_SECRET_ACCESS_KEY"))
        key = f"uploads/{uuid.uuid4().hex}_{uploaded_file.name}"
        s3.put_object(Bucket=_get_secret("S3_BUCKET"), Key=key, Body=st.session_state["uploaded_image_bytes"], ContentType=st.session_state["uploaded_image_meta"]["content_type"])
        url = s3.generate_presigned_url("get_object", Params={"Bucket": _get_secret("S3_BUCKET"), "Key": key}, ExpiresIn=3600)

        lens = requests.get("https://serpapi.com/search.json", params={"engine": "google_lens", "url": url, "api_key": _get_secret("SERPAPI_API_KEY")}).json()
        raw_matches = [{"title": i.get("title"), "source": i.get("source"), "link": i.get("link"), "thumbnail": i.get("thumbnail")} for i in lens.get("visual_matches", [])[:15]]

        st.session_state["results"] = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "traceability": {"s3": {"presigned_url": url}, "search_summary": {"top_matches": upgrade_comps_with_gemini(raw_matches)}},
        }

# 3. Results
st.header("3. Results")
res = st.session_state.get("results")
if res:
    matches = res["traceability"]["search_summary"]["top_matches"]
    t_auc, t_ret, t_misc = st.tabs(["Auction Results", "Retail Listings", "Other Matches"])

    def render_cards(subset, is_priced):
        if not subset:
            st.info("No relevant matches found in this category.")
            return
        for m in subset:
            auc_pill = f"<div class='pill'>Low: {m.get('auction_low')} | High: {m.get('auction_high')}</div>" if is_priced and m.get("auction_low") else ""
            ret_pill = f"<div class='pill'>Price: {m.get('retail_price')}</div>" if is_priced and m.get("retail_price") else ""
            st.markdown(f"""
                <div class="result-card">
                  <div style="display:flex; gap:20px;">
                    <img src="{m.get('thumbnail')}" width="120" style="object-fit:contain; border:1px solid #CFC7BC; background:#FFF;" />
                    <div>
                      <div style="font-weight:700; font-size:1.2rem; margin-bottom:4px;">{m.get('title')}</div>
                      <div style="font-style:italic; margin-bottom:8px;">Source: {m.get('source')}</div>
                      {auc_pill} {ret_pill}
                      <div style="margin-top:14px;"><a href="{m.get('link')}" target="_blank">VIEW LISTING</a></div>
                    </div>
                  </div>
                </div>
            """, unsafe_allow_html=True)

    with t_auc: render_cards([m for m in matches if m.get("kind") == "auction"], True)
    with t_ret: render_cards([m for m in matches if m.get("kind") == "retail"], True)
    with t_misc: render_cards([m for m in matches if m.get("kind") not in ("auction", "retail")], False)

    if st.button("🚀 EXPORT TO MASTER GOOGLE SHEET"):
        with st.spinner("Processing spreadsheet append..."):
            try:
                export_to_google_sheets(res)
                st.toast("Success: Row Appended")
                st.markdown(f"[VIEW MASTER TRACKING SHEET]({_get_secret('GOOGLE_SHEET_URL')})")
            except Exception as e: st.error(f"Spreadsheet Export Failed: {e}")
else:
    st.info("No appraisal run yet. Upload a photo above to begin.")
