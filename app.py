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
    st.markdown(
        """
<link href="https://fonts.googleapis.com/css2?family=EB+Garamond:wght@400;600;700&display=swap" rel="stylesheet">
<style>
/* ---------------------------
   Palette (readable + on-brand)
   --------------------------- */
:root{
  --newel-bg: #FBF5EB;        /* warm ivory */
  --newel-bg-2: #F8F2E8;      /* sidebar ivory */
  --newel-text: #1C1C1E;      /* near-black (readable) */
  --newel-muted: #4A4A4F;     /* muted text */
  --newel-border: #C2C2C2;    /* light border */
  --newel-burgundy: #5A0B1B;  /* deep wine */
  --newel-burgundy-2: #7A0F24;
  --newel-gold: #EFDAAC;      /* accent */
  --card-bg: #FFFFFF;
}

/* Base page */
.stApp {
  background-color: var(--newel-bg) !important;
  font-family: 'EB Garamond', serif !important;
}

/* Force all default Streamlit text to readable dark */
.stApp, .stApp p, .stApp span, .stApp label, .stApp div, .stMarkdown, .stText, .stAlert {
  color: var(--newel-text) !important;
  font-family: 'EB Garamond', serif !important;
}

/* Sidebar */
[data-testid="stSidebar"] {
  background-color: var(--newel-bg-2) !important;
  border-right: 1px solid var(--newel-border) !important;
}
[data-testid="stSidebar"] * {
  color: var(--newel-text) !important;
}

/* Headings */
h1, h2, h3 {
  font-family: 'EB Garamond', serif !important;
  color: var(--newel-burgundy) !important;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

/* Sidebar "NEWEL" logo text: bigger */
.newel-logo-text {
  font-family: 'EB Garamond', serif !important;
  font-weight: 700;
  font-size: 3.2rem;         /* larger per request */
  line-height: 1.0;
  letter-spacing: 0.14em;
  color: var(--newel-burgundy) !important;
  margin: 0.4rem 0 0.25rem 0;
}
.newel-logo-subtext {
  font-family: 'EB Garamond', serif !important;
  font-weight: 600;
  font-size: 0.95rem;
  color: var(--newel-muted) !important;
  margin: 0 0 0.75rem 0;
}

/* Dividers */
hr, [data-testid="stDivider"] {
  border-color: var(--newel-border) !important;
}

/* File uploader */
[data-testid="stFileUploader"] { background-color: transparent !important; }
[data-testid="stFileUploader"] section {
  background-color: var(--newel-bg-2) !important;
  border: 1px dashed var(--newel-border) !important;
  border-radius: 12px;
}
[data-testid="stFileUploader"] label {
  color: var(--newel-text) !important;
  font-weight: 600;
}

/* Buttons: burgundy fill + ivory text */
div.stButton > button {
  background-color: var(--newel-burgundy) !important;
  color: var(--newel-bg) !important;
  border-radius: 0px !important;
  border: none !important;
  padding: 0.85rem 1.25rem !important;
  font-family: 'EB Garamond', serif !important;
  font-weight: 700 !important;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  width: 100%;
}
div.stButton > button:hover {
  background-color: var(--newel-burgundy-2) !important;
  color: var(--newel-bg) !important;
}

/* Tabs */
.stTabs [data-baseweb="tab"] {
  font-family: 'EB Garamond', serif !important;
  font-weight: 700 !important;
  letter-spacing: 0.06em;
  color: var(--newel-text) !important;
}
.stTabs [data-baseweb="tab"][aria-selected="true"] {
  color: var(--newel-burgundy) !important;
}

/* Result card */
.result-card {
  background-color: var(--card-bg) !important;
  padding: 1.25rem 1.25rem;
  border: 1px solid var(--newel-border);
  border-radius: 16px;
  margin-bottom: 1rem;
  color: var(--newel-text) !important;
}
.result-title {
  font-size: 1.15rem;
  font-weight: 700;
  color: var(--newel-text) !important;
}
.result-meta {
  color: var(--newel-muted) !important;
  font-size: 0.95rem;
}

/* Pills */
.pill {
  background-color: var(--newel-gold) !important;
  padding: 6px 10px;
  border-radius: 999px;
  font-size: 0.85rem;
  font-weight: 700;
  color: var(--newel-text) !important;
  display: inline-block;
  margin-top: 8px;
}

/* Links: burgundy, readable */
a, a:link, a:visited {
  color: var(--newel-burgundy) !important;
  font-weight: 700;
  text-decoration: none;
}
a:hover {
  text-decoration: underline;
}

/* Alerts/info boxes: ensure readable */
[data-testid="stAlert"] {
  border-radius: 12px !important;
}
</style>
        """,
        unsafe_allow_html=True,
    )

apply_newel_branding()

# ==========================================
# 2. CORE UTILITIES
# ==========================================
def _get_secret(name: str) -> str:
    if name in st.secrets:
        return str(st.secrets[name])
    val = os.getenv(name)
    if not val:
        raise RuntimeError(f"Missing required secret: {name}")
    return val

# ==========================================
# 3. SERVICE: GEMINI CLASSIFICATION
# ==========================================
def upgrade_comps_with_gemini(matches: list[dict]) -> list[dict]:
    genai.configure(api_key=_get_secret("GEMINI_API_KEY"))
    model = genai.GenerativeModel("gemini-2.0-flash")

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
            if i < len(ai_data):
                match.update(ai_data[i])
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

    # Auth via Service Account Secrets
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
# 5. UI MAIN LOGIC
# ==========================================
with st.sidebar:
    # Bigger NEWEL logo text (EB Garamond) + remove "EST 1939"
    st.markdown("<div class='newel-logo-text'>NEWEL</div>", unsafe_allow_html=True)

    # If you have a logo file, keep it, but show it nicely. (Optional)
    if os.path.exists("logo.png"):
        st.image("logo.png", use_container_width=True)

    st.divider()
    sku = st.session_state.get("uploaded_image_meta", {}).get("filename", "N/A")
    st.markdown(f"**SKU Label:** `{sku}`")

# Main page header
st.markdown("<h1>Newel Appraiser</h1>", unsafe_allow_html=True)

# 1. Upload Section
st.header("1. Upload Item Image")
uploaded_file = st.file_uploader("Upload item photo", type=["jpg", "png"])

if uploaded_file:
    st.session_state["uploaded_image_bytes"] = uploaded_file.getvalue()
    st.session_state["uploaded_image_meta"] = {"filename": uploaded_file.name, "content_type": uploaded_file.type}
    st.image(uploaded_file, width=420)

# 2. Run Appraisal Section
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
        s3.put_object(Bucket=_get_secret("S3_BUCKET"), Key=key, Body=st.session_state["uploaded_image_bytes"])
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": _get_secret("S3_BUCKET"), "Key": key},
            ExpiresIn=3600,
        )

        lens = requests.get(
            "https://serpapi.com/search.json",
            params={"engine": "google_lens", "url": url, "api_key": _get_secret("SERPAPI_API_KEY")},
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

# 3. Results & Export Section
st.header("3. Results")
res = st.session_state.get("results")
if res:
    matches = res["traceability"]["search_summary"]["top_matches"]
    t_auc, t_ret, t_misc = st.tabs(["🔨 Auction Results", "🛋️ Retail Listings", "🔎 Other Matches"])

    for tab, kind in [(t_auc, "auction"), (t_ret, "retail")]:
        with tab:
            subset = [m for m in matches if m.get("kind") == kind]
            if not subset:
                st.info(f"No {kind} matches found.")
            for m in subset:
                auc_pill = ""
                if m.get("auction_low") is not None or m.get("auction_high") is not None:
                    auc_pill = (
                        "<div class='pill'>"
                        f"Low: {m.get('auction_low')} &nbsp; | &nbsp; High: {m.get('auction_high')}"
                        "</div>"
                    )

                ret_pill = ""
                if m.get("retail_price") is not None:
                    ret_pill = f"<div class='pill'>Retail Price: {m.get('retail_price')}</div>"

                thumb = m.get("thumbnail", "")
                title = m.get("title", "Untitled")
                source = m.get("source", "Unknown")
                link = m.get("link", "")

                st.markdown(
                    f"""
<div class="result-card">
  <div style="display:flex; gap:16px; align-items:flex-start;">
    <div style="width:110px; flex:0 0 110px;">
      <img src="{thumb}" width="110" style="object-fit:contain; border:1px solid #C2C2C2; border-radius:12px; background:#FFF;" />
    </div>
    <div style="flex:1;">
      <div class="result-title">{title}</div>
      <div class="result-meta">Source: {source}</div>
      {auc_pill}
      {ret_pill}
      <div style="margin-top:10px;">
        <a href="{link}" target="_blank">VIEW LISTING</a>
      </div>
    </div>
  </div>
</div>
                    """,
                    unsafe_allow_html=True,
                )

    with t_misc:
        # Keep existing behavior: show anything not labeled auction/retail (or unlabeled)
        other = [m for m in matches if m.get("kind") not in ("auction", "retail")]
        if not other:
            st.info("No other matches.")
        for m in other:
            thumb = m.get("thumbnail", "")
            title = m.get("title", "Untitled")
            source = m.get("source", "Unknown")
            link = m.get("link", "")
            conf = m.get("confidence")
            conf_pill = f"<div class='pill'>Confidence: {conf}</div>" if conf is not None else ""

            st.markdown(
                f"""
<div class="result-card">
  <div style="display:flex; gap:16px; align-items:flex-start;">
    <div style="width:110px; flex:0 0 110px;">
      <img src="{thumb}" width="110" style="object-fit:contain; border:1px solid #C2C2C2; border-radius:12px; background:#FFF;" />
    </div>
    <div style="flex:1;">
      <div class="result-title">{title}</div>
      <div class="result-meta">Source: {source}</div>
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

    if st.button("🚀 Export to Google Sheets"):
        with st.spinner("Exporting rows..."):
            try:
                export_to_google_sheets(res)
                st.toast("Successfully exported to Sheets!")
                st.markdown(f"[Click here to view your Master Sheet]({_get_secret('GOOGLE_SHEET_URL')})")
            except Exception as e:
                st.error(f"Export failed: {e}")
else:
    st.info("No appraisal run yet.")
