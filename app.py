import os
import uuid
import json
import time
import hashlib
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

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

  --btn:#8B0000;
  --btnHover:#A30000;

  --burgundy:#5A0B1B;
  --gold:#EFDAAC;
}

/* Force light mode shell */
html, body { background: var(--bg) !important; color: var(--text) !important; color-scheme: light !important; }
.stApp { background: var(--bg) !important; color: var(--text) !important; font-family: 'EB Garamond', serif !important; }

/* Streamlit chrome */
[data-testid="stHeader"], [data-testid="stToolbar"], header { background: var(--bg) !important; }

/* Main containers */
[data-testid="stAppViewContainer"], [data-testid="stMain"], section.main { background: var(--bg) !important; }

/* Global readable text */
.stApp, .stApp * { color: var(--text) !important; font-family: 'EB Garamond', serif !important; }

/* Sidebar */
section[data-testid="stSidebar"]{ background: var(--bg2) !important; border-right: 1px solid var(--border) !important; }
section[data-testid="stSidebar"] *{ color: var(--text) !important; }

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

/* NEWEL logo */
.newel-logo-text {
  font-weight: 700 !important;
  font-size: 3.4rem !important;
  letter-spacing: 0.18em !important;
  line-height: 1.0 !important;
  color: var(--burgundy) !important;
  margin: 0.25rem 0 0.8rem 0 !important;
}

/* File uploader */
[data-testid="stFileUploader"] section,
[data-testid="stFileUploaderDropzone"]{
  background: var(--card) !important;
  border: 1px dashed var(--border) !important;
  border-radius: 12px !important;
}

/* ALL buttons: red w/ white text */
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
button:hover, .stButton>button:hover { background-color: var(--btnHover) !important; color: #FFFFFF !important; }

/* Tabs */
.stTabs [data-baseweb="tab"]{ font-weight: 700 !important; letter-spacing: 0.08em !important; text-transform: uppercase !important; }
.stTabs [data-baseweb="tab"][aria-selected="true"]{ color: var(--burgundy) !important; }

/* Card + pills */
.result-card {
  background: var(--card) !important;
  border: 1px solid var(--border) !important;
  border-radius: 16px !important;
  padding: 14px 14px !important;
  margin-bottom: 14px !important;
}
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
.meta { color: var(--muted) !important; font-size: 0.95rem !important; }

/* Alerts */
[data-testid="stAlert"] { border-radius: 12px !important; }
</style>
        """,
        height=0,
        scrolling=False,
    )

apply_newel_branding()


# ==========================================
# 3. SECRETS + HELPERS
# ==========================================
def _get_secret(name: str) -> str:
    if name in st.secrets:
        return str(st.secrets[name])
    val = os.getenv(name)
    if not val:
        raise RuntimeError(f"Missing required secret: {name}")
    return val


def _fmt_value(v) -> str:
    if v is None:
        return "—"
    s = str(v).strip()
    return s if s else "—"


def _container_border():
    try:
        return st.container(border=True)
    except TypeError:
        return st.container()


# ==========================================
# 4. GEMINI FALLBACK + CACHING
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
# 5. SCRAPING: PRICE / ESTIMATE EXTRACTION
#    - Best-effort; some sites render price via JS or block bots.
# ==========================================
_MONEY_RE = re.compile(
    r'(?:(?:USD|US\$)\s*)?\$\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})?|[0-9]+(?:\.[0-9]{2})?)',
    re.IGNORECASE
)
_RANGE_RE = re.compile(
    r'\$\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})?)\s*(?:-|–|to)\s*\$\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})?)',
    re.IGNORECASE
)

def _to_number_string(x: str) -> str:
    # Keep as string for Sheets/JSON; normalize commas
    return x.replace(",", "").strip()

def _extract_best_retail_price(text: str) -> str | None:
    """
    Picks the first plausible retail price from HTML text.
    Conservative: choose the smallest of the first few amounts (often list pages include many prices).
    """
    vals = []
    for m in _MONEY_RE.finditer(text):
        amt = _to_number_string(m.group(1))
        try:
            vals.append(Decimal(amt))
        except InvalidOperation:
            continue
        if len(vals) >= 12:
            break
    if not vals:
        return None
    # heuristic: choose median-ish of first values to avoid huge banner numbers
    vals_sorted = sorted(vals)
    mid = vals_sorted[len(vals_sorted)//2]
    return f"${mid:,}".replace(",", ",")  # formatting keeps commas

def _extract_estimate_range(text: str) -> tuple[str | None, str | None]:
    """
    Look for explicit $X - $Y ranges near 'estimate' keywords.
    """
    # focus around "estimate" occurrences
    lowered = text.lower()
    idx = lowered.find("estimate")
    windows = []
    if idx != -1:
        windows.append(text[max(0, idx-2000): idx+2000])
    # fallback: entire doc window (smaller)
    windows.append(text[:120000])

    for w in windows:
        rm = _RANGE_RE.search(w)
        if rm:
            lo = "$" + rm.group(1)
            hi = "$" + rm.group(2)
            return lo, hi
    return None, None

def _extract_reserve(text: str) -> str | None:
    """
    Reserve is rare; try to find 'reserve' near a $ amount.
    """
    lowered = text.lower()
    if "reserve" not in lowered:
        return None
    # find first reserve occurrence and search nearby for money
    pos = lowered.find("reserve")
    window = text[max(0, pos-1500): pos+1500]
    m = _MONEY_RE.search(window)
    if m:
        return "$" + m.group(1)
    return None

def _fetch_html(url: str) -> str | None:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; NewelAppraiser/1.0; +https://newel.com)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        r = requests.get(url, headers=headers, timeout=12, allow_redirects=True)
        if r.status_code >= 400:
            return None
        # Some sites return huge HTML; cap to keep performance stable
        return (r.text or "")[:250000]
    except Exception:
        return None

def enrich_matches_with_scraped_prices(matches: list[dict], max_to_scrape: int = 6) -> list[dict]:
    """
    Best-effort scrape to populate numeric fields when missing.
    - Only scrapes the first N matches per run (performance guardrail)
    - Caches per session by URL
    """
    if "scrape_cache" not in st.session_state:
        st.session_state["scrape_cache"] = {}

    scraped = 0
    for m in matches:
        if scraped >= max_to_scrape:
            break

        url = (m.get("link") or "").strip()
        if not url:
            continue

        # If already has required numbers, skip
        if m.get("kind") == "auction":
            if m.get("auction_low") is not None and m.get("auction_high") is not None and m.get("auction_reserve") is not None:
                continue
        if m.get("kind") == "retail":
            if m.get("retail_price") is not None:
                continue

        if url in st.session_state["scrape_cache"]:
            cached = st.session_state["scrape_cache"][url]
            m.update(cached)
            continue

        html = _fetch_html(url)
        if not html:
            st.session_state["scrape_cache"][url] = {}
            scraped += 1
            continue

        # Strip tags crudely for regex (cheap + effective)
        text = re.sub(r"<script.*?>.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<style.*?>.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text)

        update = {}
        if m.get("kind") == "retail":
            rp = _extract_best_retail_price(text)
            if rp:
                update["retail_price"] = rp

        elif m.get("kind") == "auction":
            lo, hi = _extract_estimate_range(text)
            if lo or hi:
                update["auction_low"] = lo
                update["auction_high"] = hi
            resv = _extract_reserve(text)
            if resv:
                update["auction_reserve"] = resv

        st.session_state["scrape_cache"][url] = update
        m.update(update)
        scraped += 1

    return matches


# ==========================================
# 6. GOOGLE SHEETS EXPORT (unchanged schema)
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
# 7. UI RENDERER (native, clickable)
# ==========================================
def render_match_card_native(m: dict, kind_for_tab: str):
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

            if kind_for_tab == "auction":
                st.markdown(
                    f"<span class='pill'>Low Estimate: {_fmt_value(m.get('auction_low'))}</span>"
                    f"<span class='pill'>High Estimate: {_fmt_value(m.get('auction_high'))}</span>"
                    f"<span class='pill'>Auction Reserve: {_fmt_value(m.get('auction_reserve'))}</span>",
                    unsafe_allow_html=True,
                )
            elif kind_for_tab == "retail":
                st.markdown(
                    f"<span class='pill'>Retail Price: {_fmt_value(m.get('retail_price'))}</span>",
                    unsafe_allow_html=True,
                )
            else:
                if m.get("confidence") is not None:
                    st.markdown(
                        f"<span class='pill'>Confidence: {_fmt_value(m.get('confidence'))}</span>",
                        unsafe_allow_html=True,
                    )

            if link:
                try:
                    st.link_button("View Listing", link, use_container_width=False)
                except TypeError:
                    st.markdown(f"[VIEW LISTING]({link})")

        st.markdown("</div>", unsafe_allow_html=True)


# ==========================================
# 8. SIDEBAR + MAIN UI
# ==========================================
with st.sidebar:
    st.markdown("<div class='newel-logo-text'>NEWEL</div>", unsafe_allow_html=True)

    st.toggle("Use Gemini classification", value=True, key="use_gemini")
    # Scrape toggle: on by default so numbers show up when possible
    st.toggle("Scrape prices from listing pages", value=True, key="use_scrape_prices")
    st.slider("Max links to scrape per run", 0, 15, 6, key="max_scrape_links")

    st.divider()
    sku = st.session_state.get("uploaded_image_meta", {}).get("filename", "N/A")
    st.markdown(f"**SKU Label:** `{sku}`")

st.markdown("<h1>Newel Appraiser</h1>", unsafe_allow_html=True)

st.header("1. Upload Item Image")
uploaded_file = st.file_uploader("Upload item photo for appraisal", type=["jpg", "jpeg", "png"])

if uploaded_file:
    st.session_state["uploaded_image_bytes"] = uploaded_file.getvalue()
    st.session_state["uploaded_image_meta"] = {"filename": uploaded_file.name, "content_type": uploaded_file.type}
    st.image(uploaded_file, width=420)

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

        # Classify (Gemini if available; else fallback)
        top_matches = upgrade_comps_with_gemini(raw_matches)

        # Scrape numbers (best-effort) so pricing fields populate without Gemini
        if st.session_state.get("use_scrape_prices", True):
            top_matches = enrich_matches_with_scraped_prices(
                top_matches,
                max_to_scrape=int(st.session_state.get("max_scrape_links", 6)),
            )

        st.session_state["results"] = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "traceability": {
                "s3": {"presigned_url": url},
                "search_summary": {"top_matches": top_matches},
            },
        }

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
