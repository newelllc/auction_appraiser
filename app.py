import os
import uuid
import json
import time
import hashlib
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
# 2. BRAND / UI STYLES
#    - Light background + readable text
#    - ALL buttons red w/ white text (includes Browse files + link_button)
#    - Larger NEWEL logo (no EST 1939)
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
   - "Browse files" uploader button
   - st.link_button button
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

/* Result "card" styling for native containers */
.result-card {
  background: var(--card) !important;
  border: 1px solid var(--border) !important;
  border-radius: 16px !important;
  padding: 14px 14px !important;
  margin-bottom: 14px !important;
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
  margin-right: 8px !important;
}

/* Small meta text */
.meta {
  color: var(--muted) !important;
  font-size: 0.95rem !important;
}

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

def _fmt_value(v) -> str:
    """Always show something for required fields."""
    if v is None:
        return "—"
    s = str(v).strip()
    return s if s else "—"

# ==========================================
# 4. GEMINI: FALLBACK + CACHING (NO QUOTA = KEEP RUNNING)
# ==========================================
def _simple_kind_fallback(match: dict) -> dict:
    title = (match.get("title") or "").lower()
    source = (match.get("source") or "").lower()
    link = (match.get("link") or "").lower()
    text = f"{title} {source} {link}"

    auction_signals = [
        "liveauctioneers", "invaluable", "sothebys", "christies", "bonhams",
        "heritage", "auction", "lot", "bids", "estimate", "hammer"
    ]
    retail_signals = [
        "chairish", "1stdibs", "ebay", "etsy", "amazon", "walmart", "wayfair",
        "buy now", "add to cart", "shop", "sale", "price"
    ]

    a = sum(1 for s in auction_signals if s in text)
    r = sum(1 for s in retail_signals if s in text)

    if a > r and a > 0:
        match["kind"] = "auction"
        match["confidence"] = 0.55
    elif r > a and r > 0:
        match["kind"] = "retail"
        match["confidence"] = 0.55
    else:
        match["kind"] = "other"
        match["confidence"] = 0.35

    match.setdefault("auction_low", None)
    match.setdefault("auction_high", None)
    match.setdefault("auction_reserve", None)
    match.setdefault("retail_price", None)
    return match

def upgrade_comps_with_gemini(matches: list[dict]) -> list[dict]:
    if "gemini_cache" not in st.session_state:
        st.session_state["gemini_cache"] = {}
    if "gemini_error_banner_shown" not in st.session_state:
        st.session_state["gemini_error_banner_shown"] = False

    use_gemini = st.session_state.get("use_gemini", True)
    if not use_gemini:
        return [_simple_kind_fallback(m) for m in matches]

    payload = [{"title": m.get("title"), "source": m.get("source"), "link": m.get("link")} for m in matches]
    cache_key = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    if cache_key in st.session_state["gemini_cache"]:
        cached = st.session_state["gemini_cache"][cache_key]
        for i, m in enumerate(matches):
            if i < len(cached):
                m.update(cached[i])
            m.setdefault("kind", "other")
            m.setdefault("confidence", None)
            m.setdefault("auction_low", None)
            m.setdefault("auction_high", None)
            m.setdefault("auction_reserve", None)
            m.setdefault("retail_price", None)
        return matches

    try:
        genai.configure(api_key=_get_secret("GEMINI_API_KEY"))
        model = genai.GenerativeModel("gemini-2.0-flash")

        prompt = f"""
Appraisal Expert: Classify matches into "auction" or "retail".
Extract: kind (auction/retail), confidence (0.0-1.0), auction_low, auction_high, auction_reserve, retail_price.
Data: {json.dumps(payload)}
Return ONLY a JSON object with a key "results" containing the ordered list of objects.
"""

        last_err = None
        for _attempt in range(2):
            try:
                response = model.generate_content(
                    prompt,
                    generation_config={"response_mime_type": "application/json"},
                )
                ai_data = json.loads(response.text).get("results", [])
                for i, m in enumerate(matches):
                    if i < len(ai_data):
                        m.update(ai_data[i])
                    m.setdefault("kind", "other")
                    m.setdefault("confidence", None)
                    m.setdefault("auction_low", None)
                    m.setdefault("auction_high", None)
                    m.setdefault("auction_reserve", None)
                    m.setdefault("retail_price", None)

                st.session_state["gemini_cache"][cache_key] = ai_data
                return matches
            except Exception as e:
                last_err = e
                time.sleep(1.2)

        raise last_err

    except Exception as e:
        if not st.session_state["gemini_error_banner_shown"]:
            st.warning(
                "Gemini classification is unavailable (quota/rate-limit). "
                "Continuing with fallback classification so the appraisal can run."
            )
            st.session_state["gemini_error_banner_shown"] = True

        st.session_state["gemini_last_error"] = str(e)
        return [_simple_kind_fallback(m) for m in matches]

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
# 6. UI HELPERS
# ==========================================
def _container_border():
    try:
        return st.container(border=True)
    except TypeError:
        return st.container()

def render_match_card_native(m: dict, kind_for_tab: str):
    """
    Bullet-proof renderer using ONLY Streamlit components.
    Requirements:
      - Auction tab: always show low/high/reserve for every listing
      - Retail tab: always show retail_price for every listing
      - Other tab: show confidence when present
    """
    thumb = m.get("thumbnail") or ""
    title = m.get("title") or "Untitled"
    source = m.get("source") or "Unknown"
    link = m.get("link") or ""

    with _container_border():
        st.markdown('<div class="result-card">', unsafe_allow_html=True)

        c1, c2 = st.columns([1, 6], gap="medium")
        with c1:
            if thumb:
                st.image(thumb, width=110)
            else:
                st.write("")

        with c2:
            st.markdown(f"**{title}**")
            st.markdown(f"<span class='meta'>Source: {source}</span>", unsafe_allow_html=True)

            # Required fields per tab
            if kind_for_tab == "auction":
                low = _fmt_value(m.get("auction_low"))
                high = _fmt_value(m.get("auction_high"))
                reserve = _fmt_value(m.get("auction_reserve"))
                st.markdown(
                    f"<span class='pill'>Low Estimate: {low}</span>"
                    f"<span class='pill'>High Estimate: {high}</span>"
                    f"<span class='pill'>Auction Reserve: {reserve}</span>",
                    unsafe_allow_html=True,
                )

            elif kind_for_tab == "retail":
                rp = _fmt_value(m.get("retail_price"))
                st.markdown(
                    f"<span class='pill'>Retail Price: {rp}</span>",
                    unsafe_allow_html=True,
                )

            else:
                conf = m.get("confidence")
                if conf is not None:
                    st.markdown(f"<span class='pill'>Confidence: {_fmt_value(conf)}</span>", unsafe_allow_html=True)

            # Bullet-proof clickable link
            if link:
                try:
                    st.link_button("View Listing", link, use_container_width=False)
                except TypeError:
                    st.markdown(f"[VIEW LISTING]({link})")

        st.markdown("</div>", unsafe_allow_html=True)

# ==========================================
# 7. UI MAIN
# ==========================================
with st.sidebar:
    st.markdown("<div class='newel-logo-text'>NEWEL</div>", unsafe_allow_html=True)
    st.toggle("Use Gemini classification", value=True, key="use_gemini")
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

    with t_auc:
        subset = [m for m in matches if m.get("kind") == "auction"]
        if not subset:
            st.info("No auction matches found.")
        for m in subset:
            render_match_card_native(m, kind_for_tab="auction")

    with t_ret:
        subset = [m for m in matches if m.get("kind") == "retail"]
        if not subset:
            st.info("No retail matches found.")
        for m in subset:
            render_match_card_native(m, kind_for_tab="retail")

    with t_misc:
        subset = [m for m in matches if m.get("kind") not in ("auction", "retail")]
        if not subset:
            st.info("No other matches.")
        for m in subset:
            render_match_card_native(m, kind_for_tab="other")

    if st.button("Export to Google Sheets"):
        with st.spinner("Exporting rows..."):
            try:
                export_to_google_sheets(res)
                st.toast("Successfully exported to Sheets!")
                st.markdown(f"[View Master Sheet]({_get_secret('GOOGLE_SHEET_URL')})")
            except Exception as e:
                st.error(f"Export failed: {e}")
