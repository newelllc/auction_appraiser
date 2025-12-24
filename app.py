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
# 1. PAGE CONFIG & NEWEL BRANDING
# ==========================================
st.set_page_config(page_title="Newel Appraiser MVP", layout="wide")

def apply_newel_branding():
    st.markdown(f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@700&family=EB+Garamond:wght@400;500&display=swap');
        .stApp {{ background-color: #F8F2E8 !important; color: #1C1C1E !important; font-family: 'EB Garamond', serif; }}
        h1, h2, h3, .brand-header {{ font-family: 'Cormorant Garamond', serif !important; color: #8B0000 !important; text-transform: uppercase; letter-spacing: 0.05em; }}
        [data-testid="stSidebar"] {{ background-color: #FBF5EB !important; border-right: 1px solid #C2C2C2; }}
        div.stButton > button {{ background-color: #1C1C1E !important; color: white !important; border-radius: 0px !important; border: none !important; font-family: 'Cormorant Garamond', serif !important; padding: 0.5rem 2rem !important; }}
        div.stButton > button:hover {{ background-color: #8B0000 !important; }}
        .pill {{ background-color: #EFDAAC; padding: 4px 10px; border-radius: 15px; font-size: 0.85rem; font-weight: bold; color: #1C1C1E; margin-right: 5px; display: inline-block; }}
        .result-card {{ background-color: white; padding: 1.5rem; border: 1px solid #C2C2C2; margin-bottom: 1rem; border-radius: 2px; }}
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
# 3. SERVICE: GEMINI CLASSIFICATION (STABLE 2.5 FLASH)
# ==========================================
def upgrade_comps_with_gemini(matches: list[dict]) -> list[dict]:
    # Configure using the GEMINI_API_KEY environment variable/secret
    genai.configure(api_key=_get_secret("GEMINI_API_KEY"))
    
    # Use the 2025 stable flash model
    model = genai.GenerativeModel('gemini-2.5-flash')
    
    context = [{"title": m["title"], "source": m["source"], "link": m["link"]} for m in matches]
    prompt = f"""
    You are an antique furniture appraisal expert. Classify these matches into "auction" or "retail".
    Extract: kind (auction/retail), confidence (0.0-1.0), auction_low, auction_high, auction_reserve, retail_price.
    Input Data: {json.dumps(context)}
    Return ONLY a JSON object with a key "results" containing the ordered list of objects.
    """

    try:
        response = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        ai_data = json.loads(response.text).get("results", [])
        for i, match in enumerate(matches):
            if i < len(ai_data): match.update(ai_data[i])
        return matches
    except Exception as e:
        st.error(f"Gemini AI Classification Error: {e}")
        return matches

# ==========================================
# 4. SERVICE: EXPORT (Analyst-Friendly Schema)
# ==========================================
def export_to_google_sheets(results: dict):
    sheet_id = _get_secret("GOOGLE_SHEET_ID")
    trace = results["traceability"]
    matches = trace["search_summary"]["top_matches"]
    img_url = trace["s3"]["presigned_url"]
    
    auctions = [m for m in matches if m.get("kind") == "auction"][:3]
    retails = [m for m in matches if m.get("kind") == "retail"][:3]

    def build_row(items, is_auc):
        row = [results["timestamp"], f'=IMAGE("{img_url}")', img_url]
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
# 5. UI MAIN
# ==========================================
with st.sidebar:
    st.markdown("<h2 class='brand-header'>Newel Appraiser</h2>", unsafe_allow_html=True)
    if os.path.exists("logo.png"): st.image("logo.png", use_container_width=True)
    st.divider()
    sku = st.session_state.get("uploaded_image_meta", {}).get("filename", "N/A")
    st.markdown(f"**SKU Label (Filename):** `{sku}`")

st.header("1. Upload Item Image")
uploaded_file = st.file_uploader("Upload item photo", type=["jpg", "png"])

if uploaded_file:
    st.session_state["uploaded_image_bytes"] = uploaded_file.getvalue()
    st.session_state["uploaded_image_meta"] = {"filename": uploaded_file.name, "content_type": uploaded_file.type}
    st.image(uploaded_file, width=400)

st.header("2. Run Appraisal")
if st.button("Run Appraisal", disabled=not uploaded_file):
    with st.spinner("Gemini AI searching & classifying..."):
        # Image Hosting
        s3 = boto3.client("s3", region_name=_get_secret("AWS_REGION"), 
                          aws_access_key_id=_get_secret("AWS_ACCESS_KEY_ID"), 
                          aws_secret_access_key=_get_secret("AWS_SECRET_ACCESS_KEY"))
        key = f"uploads/{uuid.uuid4().hex}_{uploaded_file.name}"
        s3.put_object(Bucket=_get_secret("S3_BUCKET"), Key=key, Body=st.session_state["uploaded_image_bytes"])
        url = s3.generate_presigned_url('get_object', Params={'Bucket': _get_secret("S3_BUCKET"), 'Key': key}, ExpiresIn=3600)
        
        # Search & Extraction
        lens = requests.get("https://serpapi.com/search.json", 
                            params={"engine": "google_lens", "url": url, "api_key": _get_secret("SERPAPI_API_KEY")}).json()
        raw_matches = [{"title": i.get("title"), "source": i.get("source"), "link": i.get("link"), "thumbnail": i.get("thumbnail")} 
                       for i in lens.get("visual_matches", [])[:15]]
        
        st.session_state["results"] = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "traceability": {"s3": {"presigned_url": url}, "search_summary": {"top_matches": upgrade_comps_with_gemini(raw_matches)}}
        }

st.header("3. Results")
res = st.session_state.get("results")
if res:
    matches = res["traceability"]["search_summary"]["top_matches"]
    t_auc, t_ret = st.tabs(["🔨 Auction", "🛋️ Retail"])
    for tab, kind in [(t_auc, "auction"), (t_ret, "retail")]:
        with tab:
            for m in [m for m in matches if m.get("kind") == kind]:
                st.markdown(f"""<div class="result-card"><div style="display: flex; gap: 15px;"><img src="{m['thumbnail']}" width="80"><div>
                            <b>{m['title']}</b><br><small>{m['source']}</small><br><div style="margin-top:5px;">
                            {"<span class='pill'>Low: " + str(m['auction_low']) + "</span>" if m.get('auction_low') else ""}
                            {"<span class='pill'>Price: " + str(m['retail_price']) + "</span>" if m.get('retail_price') else ""}
                            </div><a href="{m['link']}" target="_blank">View Listing</a></div></div></div>""", unsafe_allow_html=True)
    if st.button("🚀 Export to Google Sheets"):
        export_to_google_sheets(res)
        st.toast("Success: Sheet Updated!")
