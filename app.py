import os
import uuid
import json
import time
import hashlib
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple
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
# 1) PAGE CONFIG
# ==========================================
st.set_page_config(page_title="Newel Appraiser MVP", layout="wide")


# ==========================================
# 2) BRAND / UI STYLES (Light mode, readable, Newel maroon buttons)
# ==========================================
NEWEL_MAROON = "#8B0000"
NEWEL_MAROON_HOVER = "#A30000"

def apply_newel_branding():
    components.html(
        f"""
<link href="https://fonts.googleapis.com/css2?family=EB+Garamond:wght@400;600;700&display=swap" rel="stylesheet">
<meta name="color-scheme" content="light">
<style>
:root{{
  --bg: #FBF5EB;
  --bg2:#F6EFE4;
  --card:#FFFFFF;
  --text:#1C1C1E;
  --muted:#4A4A4F;
  --border:#CFC7BC;
  --btn:{NEWEL_MAROON};
  --btnHover:{NEWEL_MAROON_HOVER};
  --burgundy:#5A0B1B;
  --gold:#EFDAAC;
}}

html, body {{
  background: var(--bg) !important;
  color: var(--text) !important;
  color-scheme: light !important;
}}

.stApp {{
  background: var(--bg) !important;
  color: var(--text) !important;
  font-family: 'EB Garamond', serif !important;
}}

[data-testid="stHeader"], [data-testid="stToolbar"], header {{
  background: var(--bg) !important;
}}

[data-testid="stAppViewContainer"], [data-testid="stMain"], section.main {{
  background: var(--bg) !important;
}}

.stApp, .stApp * {{
  color: var(--text) !important;
  font-family: 'EB Garamond', serif !important;
}}

section[data-testid="stSidebar"]{{
  background: var(--bg2) !important;
  border-right: 1px solid var(--border) !important;
}}
section[data-testid="stSidebar"] *{{
  color: var(--text) !important;
}}

h1, h2, h3 {{
  font-family: 'EB Garamond', serif !important;
  color: var(--burgundy) !important;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-weight: 700 !important;
}}
h1 {{ font-size: 2.4rem !important; margin-bottom: 0.25rem !important; }}
h2 {{ font-size: 1.6rem !important; margin-top: 1.25rem !important; }}

.newel-logo-text {{
  font-weight: 700 !important;
  font-size: 3.4rem !important;
  letter-spacing: 0.18em !important;
  line-height: 1.0 !important;
  color: var(--burgundy) !important;
  margin: 0.25rem 0 0.8rem 0 !important;
}}

[data-testid="stFileUploader"] section,
[data-testid="stFileUploaderDropzone"]{{
  background: var(--card) !important;
  border: 1px dashed var(--border) !important;
  border-radius: 12px !important;
}}

/* ALL buttons red/maroon with white text */
button, .stButton>button {{
  background-color: var(--btn) !important;
  color: #FFFFFF !important;
  border: none !important;
  border-radius: 0px !important;
  font-weight: 700 !important;
  letter-spacing: 0.12em !important;
  text-transform: uppercase !important;
  padding: 0.9rem 1.2rem !important;
}}
button:hover, .stButton>button:hover {{
  background-color: var(--btnHover) !important;
  color: #FFFFFF !important;
}}

hr, [data-testid="stDivider"]{{
  border-color: var(--border) !important;
}}

.result-card {{
  background: var(--card) !important;
  border: 1px solid var(--border) !important;
  border-radius: 16px !important;
  padding: 14px 14px !important;
  margin-bottom: 14px !important;
}}

.pill {{
  background: var(--gold) !important;
  color: var(--text) !important;
  padding: 6px 10px !important;
  border-radius: 999px !important;
  font-weight: 700 !important;
  display: inline-block !important;
  margin-top: 8px !important;
  margin-right: 8px !important;
}}

.meta {{
  color: var(--muted) !important;
  font-size: 0.95rem !important;
}}

[data-testid="stAlert"] {{
  border-radius: 12px !important;
}}

/* Make textareas readable */
textarea {{
  color: var(--text) !important;
  background: #FFFFFF !important;
}}
</style>
        """,
        height=0,
        scrolling=False,
    )

apply_newel_branding()


# ==========================================
# 3) SECRETS + HELPERS
# ==========================================
def _get_secret(name: str) -> str:
    if name in st.secrets:
        return str(st.secrets[name])
    val = os.getenv(name)
    if not val:
        raise RuntimeError(f"Missing required secret: {name}")
    return val

def _hostname(url: str) -> str:
    try:
        h = urlparse(url).netloc.lower()
        if h.startswith("www."):
            h = h[4:]
        return h
    except Exception:
        return ""

def _strip_html(s: str) -> str:
    # Defensive: remove any markup that might have slipped into extracted values.
    if not s:
        return s
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("\u200b", "").strip()
    return s

def _fmt_value(v) -> str:
    if v is None:
        return "—"
    s = _strip_html(str(v)).strip()
    return s if s else "—"

def _container_border():
    try:
        return st.container(border=True)
    except TypeError:
        return st.container()


# ==========================================
# 4) DOMAIN-BASED KIND (for robust UI)
# ==========================================
AUCTION_DOMAINS = {"liveauctioneers.com", "bidsquare.com", "sothebys.com", "christies.com"}
RETAIL_DOMAINS = {"1stdibs.com", "chairish.com", "incollect.com", "rauantiques.com"}

def _kind_from_domain(url: str) -> str:
    host = _hostname(url)
    if any(host.endswith(d) for d in AUCTION_DOMAINS):
        return "auction"
    if any(host.endswith(d) for d in RETAIL_DOMAINS):
        return "retail"
    return "other"


# ==========================================
# 5) GEMINI CLIENT
# ==========================================
def _gemini_model():
    # Uses billed API key in secrets/env
    genai.configure(api_key=_get_secret("GEMINI_API_KEY"))
    return genai.GenerativeModel("gemini-2.0-flash")

def _gemini_json(prompt: str, retries: int = 3) -> dict:
    model = _gemini_model()
    last_err = None
    for i in range(retries):
        try:
            resp = model.generate_content(
                prompt,
                generation_config={"response_mime_type": "application/json"},
            )
            return json.loads(resp.text)
        except Exception as e:
            last_err = e
            time.sleep(1.0 * (2 ** i))
    raise RuntimeError(f"Gemini JSON call failed: {last_err}")

def _gemini_text(prompt: str, retries: int = 3) -> str:
    model = _gemini_model()
    last_err = None
    for i in range(retries):
        try:
            resp = model.generate_content(prompt)
            return (resp.text or "").strip()
        except Exception as e:
            last_err = e
            time.sleep(1.0 * (2 ** i))
    raise RuntimeError(f"Gemini text call failed: {last_err}")


# ==========================================
# 6) URL SCRAPING (best-effort)
# ==========================================
SCRIPT_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
META_CONTENT_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\']([^"\']+)["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
MONEY_RE = re.compile(
    r'(?:(?:USD|US\$)\s*)?\$\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?|[0-9]+(?:\.[0-9]{1,2})?)',
    re.IGNORECASE,
)
RANGE_RE = re.compile(
    r'\$\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?)\s*(?:-|–|to)\s*\$\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?)',
    re.IGNORECASE,
)

def _fetch_html(url: str) -> Optional[str]:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; NewelAppraiser/1.0; +https://newel.com)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        r = requests.get(url, headers=headers, timeout=16, allow_redirects=True)
        if r.status_code >= 400:
            return None
        return (r.text or "")[:900000]
    except Exception:
        return None

def _clean_html_text(html: str) -> str:
    t = re.sub(r"<script.*?>.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    t = re.sub(r"<style.*?>.*?</style>", " ", t, flags=re.IGNORECASE | re.DOTALL)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t[:250000]

def _parse_jsonld_blocks(html: str) -> List[Any]:
    blocks: List[Any] = []
    for m in SCRIPT_JSONLD_RE.finditer(html):
        raw = (m.group(1) or "").strip().strip("<!--").strip("-->")
        if not raw:
            continue
        try:
            blocks.append(json.loads(raw))
        except Exception:
            continue
    return blocks

def _extract_meta_map(html: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for m in META_CONTENT_RE.finditer(html):
        k = (m.group(1) or "").strip().lower()
        v = (m.group(2) or "").strip()
        if k and v:
            out[k] = v
    return out

def _as_decimal_money(v: Any) -> Optional[Decimal]:
    if v is None:
        return None
    try:
        if isinstance(v, Decimal):
            return v
        if isinstance(v, (int, float)):
            return Decimal(str(v))
        s = str(v).strip().replace(",", "")
        mm = re.search(r"([0-9]+(?:\.[0-9]{1,2})?)", s)
        if not mm:
            return None
        return Decimal(mm.group(1))
    except Exception:
        return None

def _normalize_money(v: Any) -> Optional[str]:
    d = _as_decimal_money(v)
    if d is None:
        return None
    if d == d.to_integral():
        return f"${int(d):,}"
    return f"${d:,.2f}"

def _jsonld_offer_prices_usd(blocks: List[Any]) -> List[Decimal]:
    out: List[Decimal] = []

    def rec(x: Any):
        if isinstance(x, dict):
            t = str(x.get("@type", "")).lower()
            if "offer" in t:
                cur = (x.get("priceCurrency") or x.get("currency") or "").strip()
                price = x.get("price") or x.get("lowPrice") or x.get("highPrice")
                if cur.upper() == "USD" and price is not None:
                    d = _as_decimal_money(price)
                    if d is not None:
                        out.append(d)
            if "offers" in x:
                rec(x["offers"])
            for v in x.values():
                rec(v)
        elif isinstance(x, list):
            for it in x:
                rec(it)

    for b in blocks:
        rec(b)

    return out


# --------- Specific Retail: 1stDibs (pick lowest plausible USD offer price) ----------
USD_PRICE_NEAR_CURRENCY_RE = re.compile(
    r'"price"\s*:\s*"?([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{{1,2}})?|[0-9]+(?:\.[0-9]{{1,2}})?)"?'
    r'.{0,180}?"(?:priceCurrency|currency|currencyCode)"\s*:\s*"?USD"?',
    re.IGNORECASE | re.DOTALL,
)

def _extract_1stdibs_price(html: str) -> Optional[str]:
    candidates: List[Decimal] = []
    blocks = _parse_jsonld_blocks(html)
    candidates.extend(_jsonld_offer_prices_usd(blocks))

    for m in USD_PRICE_NEAR_CURRENCY_RE.finditer(html):
        d = _as_decimal_money(m.group(1))
        if d is not None:
            candidates.append(d)

    meta = _extract_meta_map(html)
    for k in ("product:price:amount", "og:price:amount", "twitter:data1"):
        if k in meta:
            d = _as_decimal_money(meta[k])
            if d is not None:
                candidates.append(d)

    plausible = [c for c in candidates if c > Decimal("10") and c < Decimal("2000000")]
    if not plausible:
        return None
    best = min(plausible)  # avoids inflated numbers
    return _normalize_money(best)


# --------- Specific Retail: Chairish (JSON-LD offers + meta + targeted “price” windows) ----------
CENTS_RE = re.compile(r'"price_cents"\s*:\s*([0-9]{3,})', re.IGNORECASE)

def _extract_chairish_price(html: str) -> Optional[str]:
    blocks = _parse_jsonld_blocks(html)
    prices = _jsonld_offer_prices_usd(blocks)
    if prices:
        return _normalize_money(min(prices))

    # price_cents (common)
    cents = []
    for m in CENTS_RE.finditer(html):
        try:
            cents.append(int(m.group(1)))
        except Exception:
            continue
        if len(cents) >= 10:
            break
    cents_plaus = [c for c in cents if c >= 1000]  # >= $10.00
    if cents_plaus:
        return _normalize_money(Decimal(min(cents_plaus)) / Decimal(100))

    # meta
    meta = _extract_meta_map(html)
    for k in ("product:price:amount", "og:price:amount"):
        if k in meta:
            d = _as_decimal_money(meta[k])
            if d is not None:
                return _normalize_money(d)

    # fallback: search near "Price" token
    text = _clean_html_text(html)
    idx = text.lower().find("price")
    window = text[max(0, idx - 2000): idx + 5000] if idx != -1 else text[:150000]
    vals: List[Decimal] = []
    for m in MONEY_RE.finditer(window):
        d = _as_decimal_money(m.group(1))
        if d is not None:
            vals.append(d)
        if len(vals) >= 12:
            break
    vals = [v for v in vals if v > Decimal("10") and v < Decimal("2000000")]
    if not vals:
        return None
    return _normalize_money(min(vals))


# --------- Generic Retail: Incollect / RauAntiques ----------
def _extract_retail_price_generic(html: str) -> Optional[str]:
    blocks = _parse_jsonld_blocks(html)
    prices = _jsonld_offer_prices_usd(blocks)
    if prices:
        return _normalize_money(min(prices))

    meta = _extract_meta_map(html)
    for k in ("product:price:amount", "og:price:amount"):
        if k in meta:
            d = _as_decimal_money(meta[k])
            if d is not None:
                return _normalize_money(d)

    text = _clean_html_text(html)
    idx = text.lower().find("price")
    window = text[max(0, idx - 2000): idx + 5000] if idx != -1 else text[:150000]
    vals: List[Decimal] = []
    for m in MONEY_RE.finditer(window):
        d = _as_decimal_money(m.group(1))
        if d is not None:
            vals.append(d)
        if len(vals) >= 12:
            break
    vals = [v for v in vals if v > Decimal("10") and v < Decimal("2000000")]
    if not vals:
        return None
    return _normalize_money(min(vals))


# --------- Auctions (best-effort estimates; reserve often not public) ----------
def _extract_estimate_range_from_text(text: str) -> Tuple[Optional[str], Optional[str]]:
    lowered = text.lower()
    idx = lowered.find("estimate")
    windows = []
    if idx != -1:
        windows.append(text[max(0, idx - 5000): idx + 5000])
    windows.append(text[:200000])
    for w in windows:
        rm = RANGE_RE.search(w)
        if rm:
            return f"${rm.group(1)}", f"${rm.group(2)}"
    return None, None

def _extract_reserve_from_text(text: str) -> Optional[str]:
    lowered = text.lower()
    if "reserve" not in lowered:
        return None
    pos = lowered.find("reserve")
    window = text[max(0, pos - 4000): pos + 4000]
    mm = MONEY_RE.search(window)
    if mm:
        return f"${mm.group(1)}"
    return None

def _extract_liveauctioneers_estimates(html: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    low = None
    high = None
    reserve = None

    # common object form
    mlo = re.search(r'"lowEstimate"\s*:\s*\{{[^}}]*"amount"\s*:\s*([0-9]+(?:\.[0-9]{{1,2}})?)', html, re.IGNORECASE)
    mhi = re.search(r'"highEstimate"\s*:\s*\{{[^}}]*"amount"\s*:\s*([0-9]+(?:\.[0-9]{{1,2}})?)', html, re.IGNORECASE)
    if mlo:
        low = _normalize_money(mlo.group(1))
    if mhi:
        high = _normalize_money(mhi.group(1))

    # flat forms
    if not low:
        m = re.search(r'"(?:estimate_low|low_estimate|lowEstimate)"\s*:\s*"?([0-9,]+(?:\.[0-9]{{1,2}})?)"?', html, re.IGNORECASE)
        if m:
            low = _normalize_money(m.group(1))
    if not high:
        m = re.search(r'"(?:estimate_high|high_estimate|highEstimate)"\s*:\s*"?([0-9,]+(?:\.[0-9]{{1,2}})?)"?', html, re.IGNORECASE)
        if m:
            high = _normalize_money(m.group(1))

    text = _clean_html_text(html)
    reserve = _extract_reserve_from_text(text)

    if not low or not high:
        rlo, rhi = _extract_estimate_range_from_text(text)
        low = low or rlo
        high = high or rhi

    return low, high, reserve

def _extract_bidsquare_estimates(html: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    # Try “estimate” range in text first (often present, robust)
    text = _clean_html_text(html)
    low, high = _extract_estimate_range_from_text(text)
    reserve = _extract_reserve_from_text(text)
    if low and high:
        return low, high, reserve

    # JSON-LD fallback
    blocks = _parse_jsonld_blocks(html)
    lows: List[Decimal] = []
    highs: List[Decimal] = []

    def rec(x: Any):
        if isinstance(x, dict):
            for k, v in x.items():
                lk = str(k).lower()
                if lk in ("lowprice", "lowestimate", "estimatelow", "estimate_low", "low_estimate"):
                    d = _as_decimal_money(v)
                    if d is not None:
                        lows.append(d)
                if lk in ("highprice", "highestimate", "estimatehigh", "estimate_high", "high_estimate"):
                    d = _as_decimal_money(v)
                    if d is not None:
                        highs.append(d)
                rec(v)
        elif isinstance(x, list):
            for it in x:
                rec(it)

    for b in blocks:
        rec(b)

    low2 = _normalize_money(min(lows)) if lows else None
    high2 = _normalize_money(min(highs)) if highs else None
    return low2, high2, reserve

def _extract_sothebys_christies_estimates(html: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    text = _clean_html_text(html)
    low, high = _extract_estimate_range_from_text(text)
    reserve = _extract_reserve_from_text(text)
    return low, high, reserve


def enrich_matches_with_scraped_prices(matches: list[dict], max_to_scrape: int = 10) -> list[dict]:
    if "scrape_cache" not in st.session_state:
        st.session_state["scrape_cache"] = {}

    scraped = 0
    for m in matches:
        if scraped >= max_to_scrape:
            break

        url = (m.get("link") or "").strip()
        if not url:
            continue

        host = _hostname(url)
        is_target = any(host.endswith(d) for d in (AUCTION_DOMAINS | RETAIL_DOMAINS))
        if not is_target:
            continue

        m.setdefault("kind", _kind_from_domain(url))
        kind = m.get("kind")

        if url in st.session_state["scrape_cache"]:
            m.update(st.session_state["scrape_cache"][url])
            scraped += 1
            continue

        html = _fetch_html(url)
        update: Dict[str, Any] = {}

        if not html:
            st.session_state["scrape_cache"][url] = update
            scraped += 1
            continue

        if kind == "retail":
            if host.endswith("1stdibs.com"):
                rp = _extract_1stdibs_price(html)
            elif host.endswith("chairish.com"):
                rp = _extract_chairish_price(html)
            else:
                rp = _extract_retail_price_generic(html)
            if rp:
                update["retail_price"] = _strip_html(rp)

        elif kind == "auction":
            if host.endswith("liveauctioneers.com"):
                low, high, reserve = _extract_liveauctioneers_estimates(html)
            elif host.endswith("bidsquare.com"):
                low, high, reserve = _extract_bidsquare_estimates(html)
            else:
                low, high, reserve = _extract_sothebys_christies_estimates(html)

            if low:
                update["auction_low"] = _strip_html(low)
            if high:
                update["auction_high"] = _strip_html(high)
            if reserve:
                update["auction_reserve"] = _strip_html(reserve)

        st.session_state["scrape_cache"][url] = update
        m.update(update)
        scraped += 1

    return matches


# ==========================================
# 7) GOOGLE SHEETS EXPORT (unchanged schema)
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
# 8) UI RENDERER (native, clickable)
# ==========================================
def render_match_card_native(m: dict, kind_for_view: str):
    thumb = m.get("thumbnail") or ""
    title = m.get("title") or "Untitled"
    source = m.get("source") or "Unknown"
    link = (m.get("link") or "").strip()

    with _container_border():
        st.markdown('<div class="result-card">', unsafe_allow_html=True)

        c1, c2 = st.columns([1, 6], gap="medium")
        with c1:
            if thumb:
                st.image(thumb, width=110)
            else:
                st.write("")

        with c2:
            st.markdown(f"**{_strip_html(title)}**")
            st.markdown(f"<span class='meta'>Source: {_strip_html(source)}</span>", unsafe_allow_html=True)

            if kind_for_view == "auction":
                st.markdown(
                    f"<span class='pill'>Low Estimate: {_fmt_value(m.get('auction_low'))}</span>"
                    f"<span class='pill'>High Estimate: {_fmt_value(m.get('auction_high'))}</span>"
                    f"<span class='pill'>Auction Reserve: {_fmt_value(m.get('auction_reserve'))}</span>",
                    unsafe_allow_html=True,
                )
            elif kind_for_view == "retail":
                st.markdown(
                    f"<span class='pill'>Retail Price: {_fmt_value(m.get('retail_price'))}</span>",
                    unsafe_allow_html=True,
                )
            else:
                conf = m.get("confidence")
                if conf is not None:
                    st.markdown(
                        f"<span class='pill'>Confidence: {_fmt_value(conf)}</span>",
                        unsafe_allow_html=True,
                    )

            if link:
                try:
                    st.link_button("View Listing", link, use_container_width=False)
                except TypeError:
                    st.markdown(f"[VIEW LISTING]({link})")

        st.markdown("</div>", unsafe_allow_html=True)


# ==========================================
# 9) CONTENT GENERATION (Gemini)
# ==========================================
def _content_context_for_mode(results: dict, mode: str) -> str:
    """
    Build a compact context for Gemini content generation based on current view.
    mode: 'auction' or 'retail'
    """
    matches = results.get("traceability", {}).get("search_summary", {}).get("top_matches", [])
    relevant = [m for m in matches if m.get("kind") == mode][:6]

    lines = []
    for i, m in enumerate(relevant, start=1):
        title = _strip_html(m.get("title") or "")
        source = _strip_html(m.get("source") or "")
        link = (m.get("link") or "").strip()
        if mode == "auction":
            low = _fmt_value(m.get("auction_low"))
            high = _fmt_value(m.get("auction_high"))
            reserve = _fmt_value(m.get("auction_reserve"))
            lines.append(f"{i}. {title} | {source} | low={low}, high={high}, reserve={reserve} | {link}")
        else:
            rp = _fmt_value(m.get("retail_price"))
            lines.append(f"{i}. {title} | {source} | retail_price={rp} | {link}")

    sku = results.get("traceability", {}).get("sku_label", "")
    img_url = results.get("traceability", {}).get("s3", {}).get("presigned_url", "")

    ctx = f"""SKU: {sku}
Image URL: {img_url}
Mode: {mode}
Reference Listings:
{chr(10).join(lines)}
"""
    return ctx.strip()

def generate_auction_title(results: dict) -> str:
    ctx = _content_context_for_mode(results, "auction")
    prompt = f"""
You are an expert auction cataloger.
Create a concise, high-quality AUCTION TITLE (max 12 words).
Use the reference listings as guidance, but do NOT include source names (e.g., do not say "Chairish").
Return ONLY the title text.

{ctx}
"""
    return _gemini_text(prompt)

def generate_auction_description(results: dict) -> str:
    ctx = _content_context_for_mode(results, "auction")
    prompt = f"""
You are an expert auction cataloger.
Write an AUCTION DESCRIPTION (120-200 words) using the reference listings as guidance.
Tone: professional, factual, sales-appropriate. Avoid overclaiming.
Do NOT include source names.
If maker/designer is uncertain, use cautious language (e.g., "attributed to", "in the manner of").
Return ONLY the description text.

{ctx}
"""
    return _gemini_text(prompt)

def generate_newel_title(results: dict) -> str:
    ctx = _content_context_for_mode(results, "retail")
    prompt = f"""
You are writing listing content for Newel (high-end vintage & antique furniture).
Create a NEWEL TITLE (max 12 words). Elegant, accurate, SEO-friendly.
Do NOT include source names. Avoid hype.
Return ONLY the title text.

{ctx}
"""
    return _gemini_text(prompt)

def generate_newel_description(results: dict) -> str:
    ctx = _content_context_for_mode(results, "retail")
    prompt = f"""
You are writing listing content for Newel (high-end vintage & antique furniture).
Write a NEWEL DESCRIPTION (140-220 words). Include:
- likely maker/designer attribution (cautious if uncertain)
- materials/finish clues (if inferable)
- style/period keywords
- condition note phrased safely (e.g., "consistent with age and use" if unknown)
Do NOT include source names.
Return ONLY the description text.

{ctx}
"""
    return _gemini_text(prompt)

def generate_keywords(results: dict) -> str:
    ctx = _content_context_for_mode(results, "retail")
    prompt = f"""
Generate 15-25 SEO KEYWORDS/PHRASES (comma-separated) for a Newel listing.
Use the reference listings as guidance. Avoid source names. Include style, period, materials, category.
Return ONLY a comma-separated list.

{ctx}
"""
    return _gemini_text(prompt)


# ==========================================
# 10) SIDEBAR + MAIN UI
# ==========================================
if "content_outputs" not in st.session_state:
    st.session_state["content_outputs"] = {
        "auction_title": "",
        "auction_description": "",
        "newel_title": "",
        "newel_description": "",
        "keywords": "",
    }

with st.sidebar:
    st.markdown("<div class='newel-logo-text'>NEWEL</div>", unsafe_allow_html=True)

    # Rename toggle label as requested
    st.toggle("AI Mode", value=True, key="use_gemini")

    # Keep scraping toggle available (still helpful even with AI Mode)
    st.toggle("Scrape prices/estimates from listing pages", value=True, key="use_scrape_prices")
    st.slider("Max listing links to scrape per run", 0, 20, 10, key="max_scrape_links")

    st.divider()
    sku = st.session_state.get("uploaded_image_meta", {}).get("filename", "N/A")
    st.markdown(f"**SKU Label:** `{sku}`")

st.markdown("<h1>Newel Appraiser</h1>", unsafe_allow_html=True)

# Step 1
st.header("1. Upload Item Image")
uploaded_file = st.file_uploader("Upload item photo for appraisal", type=["jpg", "jpeg", "png"])

if uploaded_file:
    st.session_state["uploaded_image_bytes"] = uploaded_file.getvalue()
    st.session_state["uploaded_image_meta"] = {"filename": uploaded_file.name, "content_type": uploaded_file.type}
    st.image(uploaded_file, width=420)

# Step 2
st.header("2. Run Appraisal")
run = st.button("Run Appraisal", disabled=not uploaded_file)

if run:
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

        presigned_url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": _get_secret("S3_BUCKET"), "Key": key},
            ExpiresIn=3600,
        )

        lens = requests.get(
            "https://serpapi.com/search.json",
            params={"engine": "google_lens", "url": presigned_url, "api_key": _get_secret("SERPAPI_API_KEY")},
            timeout=60,
        ).json()

        raw_matches = [
            {
                "title": i.get("title"),
                "source": i.get("source"),
                "link": i.get("link"),
                "thumbnail": i.get("thumbnail"),
            }
            for i in lens.get("visual_matches", [])[:18]
        ]

        # Assign kind via domain right away for predictable UX
        for m in raw_matches:
            m["kind"] = _kind_from_domain(m.get("link") or "")

        # OPTIONAL: Gemini classification enrichment (kept minimal, non-breaking)
        # (Your AI Mode toggle currently controls whether Gemini is available for content generation too.)
        # We avoid relying on it for prices, since you want scraped correctness where possible.
        # But leaving "confidence" field as a placeholder.
        if st.session_state.get("use_gemini", True):
            for m in raw_matches:
                m.setdefault("confidence", 0.75 if m.get("kind") in ("auction", "retail") else 0.35)
        else:
            for m in raw_matches:
                m.setdefault("confidence", 0.65 if m.get("kind") in ("auction", "retail") else 0.35)

        # Scrape numeric fields best-effort
        if st.session_state.get("use_scrape_prices", True):
            raw_matches = enrich_matches_with_scraped_prices(
                raw_matches, max_to_scrape=int(st.session_state.get("max_scrape_links", 10))
            )

        st.session_state["results"] = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "traceability": {
                "sku_label": st.session_state.get("uploaded_image_meta", {}).get("filename", ""),
                "s3": {"presigned_url": presigned_url},
                "search_summary": {"top_matches": raw_matches},
            },
        }

        # Clear old content outputs when a new appraisal is run
        st.session_state["content_outputs"] = {
            "auction_title": "",
            "auction_description": "",
            "newel_title": "",
            "newel_description": "",
            "keywords": "",
        }

# Step 3 + Step 4 in two columns
st.header("3. Results")

res = st.session_state.get("results")
if not res:
    st.info("No appraisal run yet. Upload a photo above to begin.")
else:
    # Let user choose which results they are viewing (drives Step 4 fields)
    view_mode = st.radio(
        "View",
        options=["Auction Results", "Retail Listings", "Other Matches"],
        horizontal=True,
        key="results_view_mode",
        label_visibility="collapsed",
    )

    left_col, right_col = st.columns([1.35, 1.0], gap="large")

    # LEFT: Results
    with left_col:
        matches = res.get("traceability", {}).get("search_summary", {}).get("top_matches", [])
        if view_mode == "Auction Results":
            subset = [m for m in matches if m.get("kind") == "auction"]
            if not subset:
                st.info("No auction matches found.")
            for m in subset:
                render_match_card_native(m, kind_for_view="auction")

        elif view_mode == "Retail Listings":
            subset = [m for m in matches if m.get("kind") == "retail"]
            if not subset:
                st.info("No retail matches found.")
            for m in subset:
                render_match_card_native(m, kind_for_view="retail")

        else:
            subset = [m for m in matches if m.get("kind") not in ("auction", "retail")]
            if not subset:
                st.info("No other matches.")
            for m in subset:
                render_match_card_native(m, kind_for_view="other")

        st.divider()
        if st.button("Export to Google Sheets"):
            with st.spinner("Exporting rows..."):
                try:
                    export_to_google_sheets(res)
                    st.toast("Successfully exported to Sheets!")
                    st.markdown(f"[View Master Sheet]({_get_secret('GOOGLE_SHEET_URL')})")
                except Exception as e:
                    st.error(f"Export failed: {e}")

    # RIGHT: Step 4 Content Generation (conditional by view_mode)
    with right_col:
        st.header("4. Content Generation")

        # AI Mode requirement
        if not st.session_state.get("use_gemini", True):
            st.info("Turn on **AI Mode** in the sidebar to generate content.")
        else:
            if view_mode == "Auction Results":
                st.subheader("Auction Content")

                if st.button("Generate Auction Title"):
                    with st.spinner("Generating Auction Title..."):
                        try:
                            st.session_state["content_outputs"]["auction_title"] = generate_auction_title(res)
                        except Exception as e:
                            st.error(f"AI generation failed: {e}")

                st.text_area(
                    "Auction Title",
                    value=st.session_state["content_outputs"].get("auction_title", ""),
                    height=90,
                )

                if st.button("Generate Auction Description"):
                    with st.spinner("Generating Auction Description..."):
                        try:
                            st.session_state["content_outputs"]["auction_description"] = generate_auction_description(res)
                        except Exception as e:
                            st.error(f"AI generation failed: {e}")

                st.text_area(
                    "Auction Description",
                    value=st.session_state["content_outputs"].get("auction_description", ""),
                    height=240,
                )

            elif view_mode == "Retail Listings":
                st.subheader("Newel Content")

                if st.button("Generate Newel Title"):
                    with st.spinner("Generating Newel Title..."):
                        try:
                            st.session_state["content_outputs"]["newel_title"] = generate_newel_title(res)
                        except Exception as e:
                            st.error(f"AI generation failed: {e}")

                st.text_area(
                    "Newel Title",
                    value=st.session_state["content_outputs"].get("newel_title", ""),
                    height=90,
                )

                if st.button("Generate Newel Description"):
                    with st.spinner("Generating Newel Description..."):
                        try:
                            st.session_state["content_outputs"]["newel_description"] = generate_newel_description(res)
                        except Exception as e:
                            st.error(f"AI generation failed: {e}")

                st.text_area(
                    "Newel Description",
                    value=st.session_state["content_outputs"].get("newel_description", ""),
                    height=240,
                )

                if st.button("Generate keywords"):
                    with st.spinner("Generating keywords..."):
                        try:
                            st.session_state["content_outputs"]["keywords"] = generate_keywords(res)
                        except Exception as e:
                            st.error(f"AI generation failed: {e}")

                st.text_area(
                    "SEO Keywords",
                    value=st.session_state["content_outputs"].get("keywords", ""),
                    height=150,
                )

            else:
                st.info("Select **Auction Results** or **Retail Listings** to generate content.")
