import os
import uuid
import json
import boto3
import requests
import streamlit as st
from datetime import datetime
from openai import OpenAI
from google.oauth2 import service_account
from google.auth.transport.requests import Request

# ==========================================
# 1. PAGE CONFIG & NEWEL BRANDING [cite: 172, 339]
# ==========================================
st.set_page_config(page_title="Newel Appraiser MVP", layout="wide")

def apply_newel_branding():
    """Injects Newel Brand Guide styles[cite: 319, 320, 332]."""
    st.markdown(f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@700&family=EB+Garamond:wght@400;500&display=swap');

        .stApp {{
            background-color: #F8F2E8 !important; /* Brand Light Neutral [cite: 321] */
            color: #1C1C1E !important; /* Primary Text [cite: 332] */
            font-family: 'EB Garamond', serif;
        }}

        h1, h2, h3, .brand-header {{
            font-family: 'Cormorant Garamond', serif !important;
            color: #8B0000 !important; /* Newel Red [cite: 199] */
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }}

        /* Sidebar Styling [cite: 333, 338] */
        [data-testid="stSidebar"] {{
            background-color: #FBF5EB !important;
            border-right: 1px solid #C2C2C2;
        }}

        /* Button Styling [cite: 341, 365] */
        div.stButton > button {{
            background-color: #1C1C1E !important;
            color: white !important;
            border-radius: 0px !important;
            border: none !important;
            font-family: 'Cormorant Garamond', serif !important;
            padding: 0.5rem 2rem !important;
        }}
        
        div.stButton > button:hover {{
            background-color: #8B0000 !important;
        }}

        /* Financial Pills [cite: 136, 147] */
        .pill {{
            background-color: #EFDAAC; /* Accent [cite: 322] */
            padding: 4px 10px;
            border-radius: 15px;
            font-size: 0.85rem;
            font-weight: bold;
            color: #1C1C1E;
            margin-right: 5px;
            display: inline-block;
        }}

        .result-card {{
            background-color: white;
            padding: 1.5rem;
            border: 1px solid #C2C2C2;
            margin-bottom: 1rem;
            border-radius: 2px;
        }}
        </style>
    """, unsafe_allow_html=True)

apply_newel_branding()

# ==========================================
# 2. CORE UTILITIES & SECRETS [cite: 105, 452]
# ==========================================
def _get_secret(name: str) -> str:
    if name in st.secrets: return str(st.secrets[name])
    val = os.getenv(name)
    if not val: raise RuntimeError(f"Missing required secret: {name}")
    return val

# ==========================================
# 3. SERVICE: CLASSIFICATION & EXTRACTION [cite: 10, 15, 140]
# ==========================================
def upgrade_comps_with_openai(matches: list[dict]) -> list[dict]:
    """Uses OpenAI to classify and extract prices WITHOUT scraping[cite: 20, 152]."""
    client = OpenAI(api_key=_get_secret("OPENAI_API_KEY"))
    
    context = [{"title": m["title"], "source": m["source"], "link": m["link"]} for m in matches]

    prompt = f"""
    You are an antique furniture appraisal expert. Classify matches into "auction" or "retail".
    Extract specific financial fields found in titles or snippets.
    
    Input Data: {json.dumps(context)}

    Return a JSON object with a key "results" containing objects with:
    - kind: "auction" (past sales) or "retail" (active asking price)
    - confidence: 0.0 to 1.0
    - auction_low: number or null
    - auction_high: number or null
    - auction_reserve: number or null
    - retail_price: number or null
    - normalized_source: e.g., "1stdibs", "liveauctioneers", "chairish"
    """

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        ai_data = json.loads(response.choices[0].message.content).get("results", [])
        for i, match in enumerate(matches):
            if i < len(ai_data): match.update(ai_data[i])
        return matches
    except Exception as e:
        st.error(f"AI Extraction Error: {e}")
        return matches

# ==========================================
# 4. SERVICE: GOOGLE SHEETS EXPORT [cite: 22, 94, 153]
# ==========================================
def export_to_google_sheets(results: dict):
    """Appends to Sheet with explicit columns for first 3 comps[cite: 28, 61, 154]."""
    sheet_id = _get_secret("GOOGLE_SHEET_ID")
    trace = results["traceability"]
    matches = trace["search_summary"]["top_matches"]
    img_url = trace["s3"]["presigned_url"]
    ts = results["timestamp"]
    
    # Filter by AI-defined kind [cite: 13]
    auctions = [m for m in matches if m.get("kind") == "auction"][:3]
    retails = [m for m in matches if m.get("kind") == "retail"][:3]

    def build_row(kind_list, is_auction=True):
        row = [ts, f'=IMAGE("{img_url}")', img_url]
        for i in range(3):
            if i < len(kind_list):
                m = kind_list[i]
                row.extend([m.get("title"), m.get("link")])
                if is_auction:
                    row.extend([m.get("auction_low"), m.get("auction_high"), m.get("auction_reserve")])
                else:
                    row.append(m.get("retail_price"))
            else:
                row.extend([""] * (5 if is_auction else 3))
        return row

    # Service Account Auth [cite: 97, 106]
    sa_info = st.secrets["google_service_account"]
    creds = service_account.Credentials.from_service_account_info(
        sa_info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    creds.refresh(Request())
    
    for tab, items, is_auc in [("Auction", auctions, True), ("Retail", retails, False)]:
        row_data = build_row(items, is_auction=is_auc)
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{tab}!A:Z:append"
        requests.post(url, params={"valueInputOption": "USER_ENTERED"}, 
                      headers={"Authorization": f"Bearer {creds.token}"}, 
                      json={"values": [row_data]}, timeout=30)

# ==========================================
# 5. UI COMPONENTS [cite: 126, 459]
# ==========================================
with st.sidebar:
    st.markdown("<h2 class='brand-header'>Newel Appraiser</h2>", unsafe_allow_html=True)
    logo_path = "logo.png"
    if os.path.exists(logo_path):
        st.image(logo_path, use_container_width=True)
    else:
        st.caption("EST 1939") # [cite: 200]
    
    st.divider()
    sku = st.session_state.get("uploaded_image_meta", {}).get("filename", "N/A")
    st.markdown(f"**SKU Label (Filename):**\n`{sku}`") # [cite: 4]

st.header("1. Upload Item Image")
uploaded_file = st.file_uploader("Drop image here [cite: 411]", type=["jpg", "png"])

if uploaded_file:
    st.session_state["uploaded_image_bytes"] = uploaded_file.getvalue()
    st.session_state["uploaded_image_meta"] = {"filename": uploaded_file.name, "content_type": uploaded_file.type}
    st.image(uploaded_file, width=400)

st.header("2. Run Appraisal")
if st.button("Run Appraisal", disabled=not uploaded_file):
    with st.spinner("Searching & Classifying..."):
        # Image Hosting (S3) [cite: 87, 435]
        s3_client = boto3.client("s3", region_name=_get_secret("AWS_REGION"),
                                 aws_access_key_id=_get_secret("AWS_ACCESS_KEY_ID"),
                                 aws_secret_access_key=_get_secret("AWS_SECRET_ACCESS_KEY"))
        key = f"uploads/{uuid.uuid4().hex}_{uploaded_file.name}"
        s3_client.put_object(Bucket=_get_secret("S3_BUCKET"), Key=key, Body=st.session_state["uploaded_image_bytes"])
        presigned = s3_client.generate_presigned_url('get_object', Params={'Bucket': _get_secret("S3_BUCKET"), 'Key': key}, ExpiresIn=3600)
        
        # SerpApi Search [cite: 90, 412]
        params = {"engine": "google_lens", "url": presigned, "api_key": _get_secret("SERPAPI_API_KEY")}
        lens_raw = requests.get("https://serpapi.com/search.json", params=params).json()
        
        # Extraction & AI Upgrade [cite: 11, 140]
        raw_matches = []
        for item in lens_raw.get("visual_matches", [])[:20]:
            raw_matches.append({
                "title": item.get("title"), "source": item.get("source"), 
                "link": item.get("link"), "thumbnail": item.get("thumbnail")
            })
        
        upgraded_matches = upgrade_comps_with_openai(raw_matches)
        
        st.session_state["results"] = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "traceability": {
                "s3": {"presigned_url": presigned},
                "search_summary": {"top_matches": upgraded_matches},
                "raw_serpapi": lens_raw
            }
        }

st.header("3. Results [cite: 127]")
res = st.session_state.get("results")
if res:
    matches = res["traceability"]["search_summary"]["top_matches"]
    
    # Filters [cite: 5, 158]
    c1, c2 = st.columns(2)
    with c1:
        priced_only = st.toggle("Show only priced results [cite: 7, 160]", value=False)
    with c2:
        limit = st.select_slider("Results count [cite: 8, 161]", options=[10, 25, 50], value=10)

    # Tabs [cite: 131]
    t_auc, t_ret = st.tabs(["🔨 Auction", "🛋️ Retail"])
    
    for tab, kind in [(t_auc, "auction"), (t_ret, "retail")]:
        with tab:
            subset = [m for m in matches if m.get("kind") == kind]
            if priced_only:
                subset = [m for m in subset if m.get("auction_low") or m.get("retail_price")]
            
            for m in subset[:limit]:
                st.markdown(f"""
                <div class="result-card">
                    <div style="display: flex; gap: 15px;">
                        <img src="{m['thumbnail']}" width="80">
                        <div>
                            <b>{m['title']}</b><br>
                            <small>{m['source']}</small><br>
                            <div style="margin-top:5px;">
                                {"<span class='pill'>Low: " + str(m['auction_low']) + "</span>" if m.get('auction_low') else ""}
                                {"<span class='pill'>Price: " + str(m['retail_price']) + "</span>" if m.get('retail_price') else ""}
                            </div>
                            <a href="{m['link']}" target="_blank">View Listing</a>
                        </div>
                    </div>
                </div>
                """, unsafe_allow_html=True)

    if st.button("🚀 Export to Google Sheets [cite: 129]"):
        with st.spinner("Writing columns..."):
            export_to_google_sheets(res)
            st.toast("Success: Sheet Updated! [cite: 43]")
            st.markdown(f"[Click here to view Sheet]({_get_secret('GOOGLE_SHEET_URL')}) [cite: 44]")

    with st.expander("Internal Traceability [cite: 47, 164]"):
        st.json(res)
