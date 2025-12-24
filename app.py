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
# 1. PAGE CONFIG & NEWEL BRANDING (EB GARAMOND)
# ==========================================
st.set_page_config(page_title="Newel Appraiser MVP", layout="wide")

def apply_newel_branding():
    st.markdown("""
        <link href="https://fonts.googleapis.com/css2?family=EB+Garamond:wght@400;700&display=swap" rel="stylesheet">
        <style>
        /* Base Page Styling */
        .stApp {
            background-color: #FBF5EB !important; /* cite: 331 */
            font-family: 'EB Garamond', serif !important;
        }

        /* Force All Text to Dark Gray for Readability */
        .stApp, .stApp p, .stApp span, .stApp label, .stApp div {
            color: #1C1C1E !important; /* cite: 332 */
            font-family: 'EB Garamond', serif !important;
        }

        /* Sidebar Styling */
        [data-testid="stSidebar"] {
            background-color: #F8F2E8 !important; /* cite: 321 */
            border-right: 1px solid #C2C2C2; /* cite: 338 */
        }

        /* Remove Dark Overlays from Form Elements */
        [data-testid="stFileUploader"] { background-color: transparent !important; }
        [data-testid="stFileUploader"] section {
            background-color: #F8F2E8 !important;
            border: 1px dashed #C2C2C2 !important;
        }

        /* Newel Red Headers */
        h1, h2, h3, .brand-header {
            font-family: 'EB Garamond', serif !important;
            color: #8B0000 !important; /* cite: 323 */
            text-transform: uppercase;
            letter-spacing: 0.1em;
        }

        /* Primary Button: Black with White Text */
        div.stButton > button {
            background-color: #1C1C1E !important; /* cite: 332 */
            color: #FBF5EB !important; /* cite: 331 */
            border-radius: 0px !important;
            border: none !important;
            padding: 0.75rem 2rem !important;
            font-family: 'EB Garamond', serif !important;
            font-weight: 700;
            text-transform: uppercase;
            width: 100%;
        }
        div.stButton > button:hover {
            background-color: #8B0000 !important; /* cite: 325 */
        }

        /* Result Cards and Pills */
        .result-card {
            background-color: white !important;
            padding: 1.5rem;
            border: 1px solid #C2C2C2;
            margin-bottom: 1rem;
            color: #1C1C1E !important;
        }
        .pill {
            background-color: #EFDAAC !important; /* cite: 322 */
            padding: 4px 10px;
            border-radius: 15px;
            font-size: 0.85rem;
            font-weight: bold;
            color: #1C1C1E !important;
            display: inline-block;
            margin-top: 5px;
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
    model = genai.GenerativeModel('gemini-2.0-flash') # cite: 11
    
    context = [{"title": m["title"], "source": m["source"], "link": m["link"]} for m in matches]
    prompt = f"""
    Appraisal Expert: Classify matches into "auction" or "retail".
    Extract: kind (auction/retail), confidence (0.0-1.0), auction_low, auction_high, auction_reserve, retail_price.
    Data: {json.dumps(context)}
    Return ONLY a JSON object with a key "results" containing the ordered list of objects.
    """
    try:
        response = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        ai_data = json.loads(response.text).get("results", [])
        for i, match in enumerate(matches):
            if i < len(ai_data): match.update(ai_data[i])
        return matches
    except Exception as e:
        st.error(f"Gemini AI Error: {e}")
        return matches

# ==========================================
# 4. SERVICE: GOOGLE SHEETS EXPORT (3 COMPS SCHEMA)
# ==========================================
def export_to_google_sheets(results: dict):
    sheet_id = _get_secret("GOOGLE_SHEET_ID")
    trace = results.get("traceability", {})
    matches = trace.get("search_summary", {}).get("top_matches", [])
    img_url = trace.get("s3", {}).get("presigned_url", "")
    ts = results.get("timestamp")
    
    auctions = [m for m in matches if m.get("kind") == "auction"][:3] # cite: 61
    retails = [m for m in matches if m.get("kind") == "retail"][:3] # cite: 61

    def build_row(items, is_auc):
        row = [ts, f'=IMAGE("{img_url}")', img_url] # cite: 124
        for i in range(3):
            if i < len(items):
                m = items[i]
                row.extend([m.get("title"), m.get("link")]) # cite: 28, 36
                if is_auc: row.extend([m.get("auction_low"), m.get("auction_high"), m.get("auction_reserve")]) # cite: 28
                else: row.append(m.get("retail_price")) # cite: 36
            else:
                row.extend([""] * (5 if is_auc else 3))
        return row

    # Auth via Service Account Secrets [cite: 97]
    sa_info = st.secrets["google_service_account"]
    creds = service_account.Credentials.from_service_account_info(sa_info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    creds.refresh(Request())
    
    for tab, items, is_auc in [("Auction", auctions, True), ("Retail", retails, False)]:
        requests.post(f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{tab}!A:Z:append",
                      params={"valueInputOption": "USER_ENTERED"}, headers={"Authorization": f"Bearer {creds.token}"},
                      json={"values": [build_row(items, is_auc)]}, timeout=30) # cite: 99

# ==========================================
# 5. UI MAIN LOGIC
# ==========================================
with st.sidebar:
    st.markdown("<h2 class='brand-header'>NEWEL</h2>", unsafe_allow_html=True)
    if os.path.exists("logo.png"): st.image("logo.png", use_container_width=True)
    st.markdown("<p style='font-size: 0.8rem; opacity: 0.8;'>EST 1939</p>", unsafe_allow_html=True)
    st.divider()
    sku = st.session_state.get("uploaded_image_meta", {}).get("filename", "N/A")
    st.markdown(f"**SKU Label:** `{sku}`") # cite: 130

st.markdown("<h1 class='brand-header'>Newel Appraiser</h1>", unsafe_allow_html=True)

# 1. Upload Section
st.header("1. Upload Item Image")
uploaded_file = st.file_uploader("Upload item photo", type=["jpg", "png"])

if uploaded_file:
    st.session_state["uploaded_image_bytes"] = uploaded_file.getvalue()
    st.session_state["uploaded_image_meta"] = {"filename": uploaded_file.name, "content_type": uploaded_file.type}
    st.image(uploaded_file, width=400)

# 2. Run Appraisal Section
st.header("2. Run Appraisal")
if st.button("Run Appraisal", disabled=not uploaded_file):
    with st.spinner("Processing..."):
        s3 = boto3.client("s3", region_name=_get_secret("AWS_REGION"), 
                          aws_access_key_id=_get_secret("AWS_ACCESS_KEY_ID"), 
                          aws_secret_access_key=_get_secret("AWS_SECRET_ACCESS_KEY"))
        key = f"uploads/{uuid.uuid4().hex}_{uploaded_file.name}"
        s3.put_object(Bucket=_get_secret("S3_BUCKET"), Key=key, Body=st.session_state["uploaded_image_bytes"])
        url = s3.generate_presigned_url('get_object', Params={'Bucket': _get_secret("S3_BUCKET"), 'Key': key}, ExpiresIn=3600)
        
        lens = requests.get("https://serpapi.com/search.json", 
                            params={"engine": "google_lens", "url": url, "api_key": _get_secret("SERPAPI_API_KEY")}).json()
        
        raw_matches = [{"title": i.get("title"), "source": i.get("source"), "link": i.get("link"), "thumbnail": i.get("thumbnail")} 
                       for i in lens.get("visual_matches", [])[:15]]
        
        st.session_state["results"] = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "traceability": {"s3": {"presigned_url": url}, "search_summary": {"top_matches": upgrade_comps_with_gemini(raw_matches)}}
        }

# 3. Results & Export Section
st.header("3. Results")
res = st.session_state.get("results")
if res:
    matches = res["traceability"]["search_summary"]["top_matches"]
    t_auc, t_ret, t_misc = st.tabs(["🔨 Auction Results", "🛋️ Retail Listings", "🔎 Other Matches"])
    
    for tab, kind in [(t_auc, "auction"), (t_ret, "retail")]:
        with tab:
            subset = [m for m in matches if m.get("kind") == kind]
            if not subset: st.info(f"No {kind} matches found.")
            for m in subset:
                st.markdown(f"""
                <div class="result-card">
                    <div style="display: flex; gap: 20px;">
                        <img src="{m['thumbnail']}" width="100" style="object-fit: contain;">
                        <div>
                            <b style="font-size: 1.2rem;">{m['title']}</b><br>
                            <span>Source: {m['source']}</span><br>
                            {"<span class='pill'>Low: " + str(m['auction_low']) + " | High: " + str(m['auction_high']) + "</span>" if m.get('auction_low') else ""}
                            {"<span class='pill'>Retail Price: " + str(m['retail_price']) + "</span>" if m.get('retail_price') else ""}
                            <br><a href="{m['link']}" target="_blank" style="color: #8B0000; font-weight: bold;">VIEW LISTING</a>
                        </div>
                    </div>
                </div>
                """, unsafe_allow_html=True)

    if st.button("🚀 Export to Google Sheets"): # cite: 129
        with st.spinner("Exporting rows..."):
            try:
                export_to_google_sheets(res)
                st.toast("Successfully exported to Sheets!") # cite: 43
                st.markdown(f"[Click here to view your Master Sheet]({_get_secret('GOOGLE_SHEET_URL')})") # cite: 44
            except Exception as e:
                st.error(f"Export failed: {e}")
else:
    st.info("No appraisal run yet.")
