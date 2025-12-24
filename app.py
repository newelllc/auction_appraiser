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
import streamlit.components.v1 as components

# ==========================================
# 1. PAGE CONFIG
# ==========================================
st.set_page_config(page_title="Newel Appraiser MVP", layout="wide")

# ==========================================
# 2. BRAND / UI STYLES (SAFE INJECTION)
#    - Avoids CSS rendering as text
#    - Forces readable light UI
#    - Makes all buttons red w/ white text
# ==========================================
def apply_newel_branding():
    components.html(
        """
<link href="https://fonts.googleapis.com/css2?family=EB+Garamond:wght@400;600;700&display=swap" rel="stylesheet">
<meta name="color-scheme" content="light">
<style>
:root{
  --bg: #FBF5EB;
  --bg2:#F6EFE4;
  --card:#FFFFFF;
  --text:#1C1C1E;
  --muted:#4A4A4F;
  --border:#CFC7BC;

  /* Button red */
  --btn:#8B0000;
  --btnHover:#A30000;

  --burgundy:#5A0B1B;
  --gold:#EFDAAC;
}

/* Force light mode shell */
html, body {
  background: var(--bg) !important;
  color: var(--text) !important;
  color-scheme: light !important;
}

.stApp {
  background: var(--bg) !important;
  color: var(--text) !important;
  font-family: 'EB Garamond', serif !important;
}

/* Streamlit chrome */
[data-testid="stHeader"],
[data-testid="stToolbar"],
header {
  background: var(--bg) !important;
}

/* Main containers */
[data-testid="stAppViewContainer"],
[data-testid="stMain"],
section.main {
  background: var(--bg) !important;
}

/* Make all text readable */
.stApp, .stApp * {
  color: var(--text) !important;
  font-family: 'EB Garamond', serif !important;
}

/* Sidebar */
section[data-testid="stSidebar"]{
  background: var(--bg2) !important;
  border-right: 1px solid var(--border) !important;
}
section[data-testid="stSidebar"] *{
  color: var(--text) !important;
}

/* Headings */
h1, h2, h3 {
  font-family: 'EB Garamond', serif !important;
  color: var(--burgundy) !important;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-weight: 700 !important;
}
h1 { font-size: 2.4rem !important; margin-bottom: 0.25rem !important; }
h2 { font-size: 1.6rem !important; margin-top: 1.25rem !important; }
h3 { font-size: 1.25rem !important; }

/* NEWEL logo text bigger */
.newel-logo-text {
  font-family: 'EB Garamond', serif !important;
  font-weight: 700 !important;
  font-size: 3.4rem !important;
  letter-spacing: 0.18em !important;
  line-height: 1.0 !important;
  color: var(--burgundy) !important;
  margin: 0.25rem 0 0.8rem 0 !important;
}

/* Dividers */
hr, [data-testid="stDivider"]{
  border-color: var(--border) !important;
}

/* File uploader container */
[data-testid="stFileUploader"] section {
  background: var(--card) !important;
  border: 1px dashed var(--border) !important;
  border-radius: 12px !important;
}
[data-testid="stFileUploaderDropzone"]{
  background: var(--card) !important;
  border: 1px dashed var(--border) !important;
  border-radius: 12px !important;
}

/* ====== ALL BUTTONS: RED with WHITE text ======
   - Streamlit buttons
   - "Browse files" button in uploader is also a <button>
*/
button, .stButton>button {
  background-color: var(--btn) !important;
  color: #FFFFFF !important;
  border: none !important;
  border-radius: 0px !important;
  font-weight: 700 !important;
  letter-spacing: 0.12em !important;
  text-transform: uppercase !important;
  padding: 0.9rem 1.2rem !important;
}
button:hover, .stButton>button:hover {
  background-color: var(--btnHover) !important;
  color: #FFFFFF !important;
}

/* Tabs */
.stTabs [data-baseweb="tab"]{
  font-weight: 700 !important;
  letter-spacing: 0.08em !important;
  text-transform: uppercase !important;
}
.stTabs [data-baseweb="tab"][aria-selected="true"]{
  color: var(--burgundy) !important;
}

/* Result cards */
.result-card {
  background: var(--card) !important;
  border: 1px solid var(--border) !important;
  border-radius: 16px !important;
  padding: 1.1rem 1.1rem !important;
  margin-bottom: 1rem !important;
}
.result-title {
  font-size: 1.15rem !important;
  font-weight: 700 !important;
}
.result-meta {
  color: var(--muted) !important;
  font-size: 0.95rem !important;
}

/* Pills */
.pill {
  background: var(--gold) !important;
  color: var(--text) !important;
  padding: 6px 10px !important;
  border-radius: 999px !important;
  font-weight: 700 !important;
  display: inline-block !important;
  margin-top: 8px !important;
}

/* Links */
a, a:link, a:visited {
  color: var(--burgundy) !important;
  font-weight: 700 !important;
  text-decoration: none !important;
}
a:hover { text-decoration: underline !important; }

/* Alerts/info boxes */
[data-testid="stAlert"] {
  border-radius: 12px !important;
}
</style>
        """,
        height=0,
        scrolling=False,
    )

apply_newel_branding()

# ==========================================
# 3. CORE UTILITIES
# ==========================================
def _get_secret(name: str) -> str:
    if name in st.secrets:
        return str(st.secrets[name])
    val = os.getenv(name)
    if not val:
        raise RuntimeError(f"Missing required secret: {name}")
    return val

# ==========================================
# 4. SERVICE: GEMINI CLASSIFICATION
# ==========================================
def upgrade_comps_with_gemini(matches: list[dict]) -> list[dict]:
    genai.configure(api_key=_get_secret("GEMINI_API_KEY"))
    model = genai.GenerativeModel("gemini-2.0-flash")

    context = [{"title": m.get("title"), "source": m.get("source"), "link": m.get("link")} for m in matches]
    prompt = f"""
Appraisal Expert: Classify matches into "auction" or "retail".
Extract: kind (auction/retail), confidence (0.0-1.0), auction_low, auction_high, auction_reserve, retail_price.
Data: {json.dumps(context)}
Return ONLY a JSON object with a key "results" containing the ordered list of objects.
"""
    try:
        response = model.generate_content(
            prompt,
            generation_config={"response_mime_type": "application/json"},
        )
        ai_data = json.loads(response.text).get("results", [])
        for i, match in enumerate(matches):
            if i < len(ai_data):
                match.update(ai_data[i])
        return matches
    except Exception as e:
        st.error(f"Gemini AI Error: {e}")
        return matches

# ==========================================
# 5. SERVICE: GOOGLE SHEETS EXPORT (3 COMPS SCHEMA)
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
                if is_auc:
                    row.extend([m.get("auction_low"), m.get("auction_high"), m.get("auction_reserve")])
                else:
                    row.append(m.get("retail_price"))
            else:
                row.extend([""] * (5 if is_auc else 3))
        return row

    # Service Account in st.secrets["google_service_account"]
    sa_info = st.secrets["google_service_account"]
    creds = service_account.Credentials.from_service_account_info(
        sa_info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    creds.refresh(Request())

    for tab, items, is_auc in [("Auction", auctions, True), ("Retail", retails, False)]:
        requests.post(
            f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{tab}!A:Z:append",
            params={"valueInputOption": "USER_ENTERED"},
            headers={"Authorization": f"Bearer {creds.token}"},
            json={"values": [build_row(items, is_auc)]},
            timeout=30,
        )

# ==========================================
# 6. UI MAIN
# ==========================================
with st.sidebar:
    # Large NEWEL logo (no EST 1939)
    st.markdown("<div class='newel-logo-text'>NEWEL</div>", unsafe_allow_html=True)

    st.divider()
    sku = st.session_state.get("uploaded_image_meta", {}).get("filename", "N/A")
    st.markdown(f"**SKU Label:** `{sku}`")

st.markdown("<h1>Newel Appraiser</h1>", unsafe_allow_html=True)

# 1. Upload
st.header("1. Upload Item Image")
uploaded_file = st.file_uploader("Upload item photo for appraisal", type=["jpg", "jpeg", "png"])

if uploaded_file:
    st.session_state["uploaded_image_bytes"] = uploaded_file.getvalue()
    st.session_state["uploaded_image_meta"] = {"filename": uploaded_file.name, "content_type": uploaded_file.type}
    st.image(uploaded_file, width=420)

# 2. Run Appraisal
st.header("2. Run Appraisal")
if st.button("Run Appraisal", disabled=not uploaded_file):
    with st.spinner("Processing..."):
        s3 = boto3.client(
            "s3",
            region_name=_get_secret("AWS_REGION"),
            aws_access_key_id=_get_secret("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=_get_secret("AWS_SECRET_ACCESS_KEY"),
        )

        key = f"uploads/{uuid.uuid4().hex}_{uploaded_file.name}"
        s3.put_object(
            Bucket=_get_secret("S3_BUCKET"),
            Key=key,
            Body=st.session_state["uploaded_image_bytes"],
            ContentType=st.session_state["uploaded_image_meta"]["content_type"],
        )

        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": _get_secret("S3_BUCKET"), "Key": key},
            ExpiresIn=3600,
        )

        lens = requests.get(
            "https://serpapi.com/search.json",
            params={"engine": "google_lens", "url": url, "api_key": _get_secret("SERPAPI_API_KEY")},
            timeout=60,
        ).json()

        raw_matches = [
            {
                "title": i.get("title"),
                "source": i.get("source"),
                "link": i.get("link"),
                "thumbnail": i.get("thumbnail"),
            }
            for i in lens.get("visual_matches", [])[:15]
        ]

        st.session_state["results"] = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "traceability": {
                "s3": {"presigned_url": url},
                "search_summary": {"top_matches": upgrade_comps_with_gemini(raw_matches)},
            },
        }

# 3. Results
st.header("3. Results")
res = st.session_state.get("results")
if not res:
    st.info("No appraisal run yet. Upload a photo above to begin.")
else:
    matches = res["traceability"]["search_summary"]["top_matches"]
    t_auc, t_ret, t_misc = st.tabs(["Auction Results", "Retail Listings", "Other Matches"])

    def render_match_card(m: dict, show_prices: bool):
        thumb = m.get("thumbnail", "")
        title = m.get("title") or "Untitled"
        source = m.get("source") or "Unknown"
        link = m.get("link") or ""

        auc_pill = ""
        if show_prices and (m.get("auction_low") is not None or m.get("auction_high") is not None):
            auc_pill = (
                "<div class='pill'>"
                f"Low: {m.get('auction_low')} &nbsp; | &nbsp; High: {m.get('auction_high')}"
                "</div>"
            )

        ret_pill = ""
        if show_prices and (m.get("retail_price") is not None):
            ret_pill = f"<div class='pill'>Retail Price: {m.get('retail_price')}</div>"

        conf = m.get("confidence")
        conf_pill = ""
        if (not show_prices) and conf is not None:
            conf_pill = f"<div class='pill'>Confidence: {conf}</div>"

        st.markdown(
            f"""
<div class="result-card">
  <div style="display:flex; gap:16px; align-items:flex-start;">
    <div style="width:110px; flex:0 0 110px;">
      <img src="{thumb}" width="110"
           style="object-fit:contain; border:1px solid #CFC7BC; border-radius:12px; background:#FFF;" />
    </div>
    <div style="flex:1;">
      <div class="result-title">{title}</div>
      <div class="result-meta">Source: {source}</div>
      {auc_pill}
      {ret_pill}
      {conf_pill}
      <div style="margin-top:10px;">
        <a href="{link}" target="_blank">VIEW LISTING</a>
      </div>
    </div>
  </div>
</div>
            """,
            unsafe_allow_html=True,
        )

    with t_auc:
        subset = [m for m in matches if m.get("kind") == "auction"]
        if not subset:
            st.info("No auction matches found.")
        for m in subset:
            render_match_card(m, show_prices=True)

    with t_ret:
        subset = [m for m in matches if m.get("kind") == "retail"]
        if not subset:
            st.info("No retail matches found.")
        for m in subset:
            render_match_card(m, show_prices=True)

    with t_misc:
        subset = [m for m in matches if m.get("kind") not in ("auction", "retail")]
        if not subset:
            st.info("No other matches.")
        for m in subset:
            render_match_card(m, show_prices=False)

    if st.button("Export to Google Sheets"):
        with st.spinner("Exporting rows..."):
            try:
                export_to_google_sheets(res)
                st.toast("Successfully exported to Sheets!")
                st.markdown(f"[View Master Sheet]({_get_secret('GOOGLE_SHEET_URL')})")
            except Exception as e:
                st.error(f"Export failed: {e}")
