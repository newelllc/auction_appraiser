# Full app.py — fix: prevent NameError for auction extractor calls by using a single robust helper.
# Keeps Chairish canonical-product + thumbnail heuristics and in-app traceback capture (debug mode).
import os
import uuid
import json
import time
import re
import traceback
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, urljoin, quote_plus
from html import escape as html_escape

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
# 2) BRAND / UI STYLES
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

html, body {{ background: var(--bg) !important; color: var(--text) !important; color-scheme: light !important; }}
.stApp {{ background: var(--bg) !important; color: var(--text) !important; font-family: 'EB Garamond', serif !important; }}
[data-testid="stHeader"], [data-testid="stToolbar"], header {{ background: var(--bg) !important; }}
[data-testid="stAppViewContainer"], [data-testid="stMain"], section.main {{ background: var(--bg) !important; }}
.stApp, .stApp * {{ color: var(--text) !important; font-family: 'EB Garamond', serif !important; }}

section[data-testid="stSidebar"]{{ background: var(--bg2) !important; border-right: 1px solid var(--border) !important; }}
section[data-testid="stSidebar"] *{{ color: var(--text) !important; }}

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
.meta {{ color: var(--muted) !important; font-size: 0.95rem !important; }}

textarea {{ color: var(--text) !important; background: #FFFFFF !important; }}
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

def _get_optional_secret(name: str) -> Optional[str]:
    if name in st.secrets:
        v = str(st.secrets[name]).strip()
        return v if v else None
    v = os.getenv(name)
    return v.strip() if v else None

def _hostname(url: str) -> str:
    try:
        h = urlparse(url).netloc.lower()
        if h.startswith("www."):
            h = h[4:]
        return h
    except Exception:
        return ""

def _container_border():
    try:
        return st.container(border=True)
    except TypeError:
        return st.container()


# ==========================================
# 4) DOMAIN-BASED KIND
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
# 5) GEMINI CLIENT (Flash 2.0)
# ==========================================
def _gemini_model():
    genai.configure(api_key=_get_secret("GEMINI_API_KEY"))
    return genai.GenerativeModel("gemini-2.0-flash")

def _gemini_json(prompt: str, retries: int = 3) -> dict:
    model = _gemini_model()
    last_err = None
    for i in range(retries):
        try:
            resp = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
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
# 6) MONEY SANITIZATION (prevents CSS/HTML garbage)
# ==========================================
MONEY_CAPTURE_RE = re.compile(
    r'(?:(?:USD|US\$)\s*)?\$?\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?|[0-9]+(?:\.[0-9]{1,2})?)',
    re.IGNORECASE
)

def _to_decimal_money(v: Any) -> Optional[Decimal]:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return v
    if isinstance(v, (int, float)):
        try:
            return Decimal(str(v))
        except Exception:
            return None
    s = str(v)
    # strip any HTML-ish
    s = re.sub(r"<[^>]+>", "", s).strip()
    if not s:
        return None
    m = MONEY_CAPTURE_RE.search(s)
    if not m:
        return None
    try:
        return Decimal(m.group(1).replace(",", ""))
    except InvalidOperation:
        return None

def _format_money(d: Decimal) -> str:
    if d == d.to_integral():
        return f"${int(d):,}"
    return f"${d:,.2f}"

def _sanitize_money(v: Any) -> Optional[str]:
    d = _to_decimal_money(v)
    if d is None:
        return None
    if d < Decimal("1") or d > Decimal("200000000"):
        return None
    return _format_money(d)

def _sanitize_range(lo: Any, hi: Any) -> Tuple[Optional[str], Optional[str]]:
    dlo = _to_decimal_money(lo)
    dhi = _to_decimal_money(hi)
    if dlo is None or dhi is None:
        return None, None
    if dhi < dlo:
        dlo, dhi = dhi, dlo
    if dlo < Decimal("1") or dhi < Decimal("1"):
        return None, None
    return _format_money(dlo), _format_money(dhi)


# ==========================================
# 7) HTTP FETCH (optionally logged in for LiveAuctioneers)
# ==========================================
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; NewelAppraiser/1.0; +https://newel.com)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

def _get_session() -> requests.Session:
    if "http_session" not in st.session_state:
        s = requests.Session()
        s.headers.update(DEFAULT_HEADERS)
        st.session_state["http_session"] = s
        st.session_state["la_logged_in"] = False
    return st.session_state["http_session"]

def _fetch_html(url: str, session: requests.Session) -> Tuple[Optional[str], Optional[int]]:
    try:
        r = session.get(url, timeout=18, allow_redirects=True)
        if r.status_code >= 400:
            return None, r.status_code
        return (r.text or "")[:1200000], r.status_code
    except Exception:
        return None, None

def _clean_html_text(html: str) -> str:
    t = re.sub(r"<script.*?>.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    t = re.sub(r"<style.*?>.*?</style>", " ", t, flags=re.IGNORECASE | re.DOTALL)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t[:350000]


# ==========================================
# 8) LiveAuctioneers login (OPTIONAL)
# ==========================================
def _try_login_liveauctioneers(session: requests.Session) -> bool:
    username = _get_optional_secret("LIVEAUCTIONEERS_USERNAME")
    password = _get_optional_secret("LIVEAUCTIONEERS_PASSWORD")
    if not username or not password:
        return False
    if st.session_state.get("la_logged_in"):
        return True
    try:
        login_page_url = "https://www.liveauctioneers.com/login/"
        html, status = _fetch_html(login_page_url, session)
        if not html:
            return False
        csrf = None
        m = re.search(r'name="csrfmiddlewaretoken"\s+value="([^"]+)"', html, re.IGNORECASE)
        if m:
            csrf = m.group(1)
        post_url = "https://www.liveauctioneers.com/login/"
        data = {"username": username, "email": username, "password": password}
        if csrf:
            data["csrfmiddlewaretoken"] = csrf
        r = session.post(post_url, data=data, timeout=18, allow_redirects=True)
        if r.status_code >= 400:
            return False
        txt = (r.text or "").lower()
        if "sign in" in txt and "password" in txt:
            return False
        st.session_state["la_logged_in"] = True
        return True
    except Exception:
        return False


# ==========================================
# 9) Domain extractors (auction and retail helpers)
# ==========================================
DOLLAR_RANGE_RE = re.compile(
    r'\$\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?)\s*(?:-|–|to)\s*\$\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?)',
    re.IGNORECASE
)
USD_RANGE_RE = re.compile(
    r'([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?)\s*(?:-|–|to)\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?)\s*(?:USD|US\$)\b',
    re.IGNORECASE
)

CENTS_RE = re.compile(r'"price_cents"\s*:\s*([0-9]{3,})', re.IGNORECASE)

NEXT_DATA_RE = re.compile(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', re.IGNORECASE | re.DOTALL)

def _extract_text_estimate_range(text: str) -> Tuple[Optional[str], Optional[str]]:
    lower = text.lower()
    idx = lower.find("estimate")
    windows = []
    if idx != -1:
        windows.append(text[max(0, idx - 10000): idx + 10000])
    windows.append(text[:250000])

    for w in windows:
        m = DOLLAR_RANGE_RE.search(w)
        if m:
            return _sanitize_range(m.group(1), m.group(2))
        m2 = USD_RANGE_RE.search(w)
        if m2:
            return _sanitize_range(m2.group(1), m2.group(2))

        m3 = re.search(
            r'(?:estimate[^0-9]{0,60})\b([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?)\s*(?:-|–|to)\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?)',
            w,
            re.IGNORECASE,
        )
        if m3:
            return _sanitize_range(m3.group(1), m3.group(2))

    return None, None

def _extract_reserve_from_text(text: str) -> Optional[str]:
    lower = text.lower()
    if "reserve" not in lower:
        return None
    pos = lower.find("reserve")
    window = text[max(0, pos - 10000): pos + 10000]
    m = MONEY_CAPTURE_RE.search(window)
    if not m:
        return None
    return _sanitize_money(m.group(1))

def _parse_next_data_json(html: str) -> Optional[Any]:
    m = NEXT_DATA_RE.search(html)
    if not m:
        return None
    raw = (m.group(1) or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None

def _walk_find_numbers(obj: Any, keys: List[str]) -> List[Decimal]:
    found: List[Decimal] = []
    wanted = {k.lower() for k in keys}
    def rec(x: Any):
        if isinstance(x, dict):
            for k, v in x.items():
                lk = str(k).lower()
                if lk in wanted:
                    d = _to_decimal_money(v)
                    if d is not None:
                        found.append(d)
                rec(v)
        elif isinstance(x, list):
            for it in x:
                rec(it)
    rec(obj)
    return found

# Per-site auction extractor functions used before — keep them but add a generic fallback helper below.
def _extract_liveauctioneers_estimates(html: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    low = None
    high = None
    reserve = None

    nd = _parse_next_data_json(html)
    if nd:
        lows = _walk_find_numbers(nd, ["lowEstimate", "estimateLow", "estimate_low", "low_estimate"])
        highs = _walk_find_numbers(nd, ["highEstimate", "estimateHigh", "estimate_high", "high_estimate"])
        if lows and highs:
            low = _sanitize_money(min(lows))
            high = _sanitize_money(min(highs))

    if not low or not high:
        mlo = re.search(r'"lowEstimate"\s*:\s*\{[^}]*"amount"\s*:\s*([0-9]+(?:\.[0-9]{1,2})?)', html, re.IGNORECASE)
        mhi = re.search(r'"highEstimate"\s*:\s*\{[^}]*"amount"\s*:\s*([0-9]+(?:\.[0-9]{1,2})?)', html, re.IGNORECASE)
        if mlo:
            low = low or _sanitize_money(mlo.group(1))
        if mhi:
            high = high or _sanitize_money(mhi.group(1))

    text = _clean_html_text(html)
    if not low or not high:
        rlo, rhi = _extract_text_estimate_range(text)
        low = low or rlo
        high = high or rhi

    reserve = _extract_reserve_from_text(text)
    return low, high, reserve

def _extract_bidsquare_estimates(html: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    text = _clean_html_text(html)
    low, high = _extract_text_estimate_range(text)
    reserve = _extract_reserve_from_text(text)

    if not low or not high:
        # scan for numeric keys directly in HTML
        lows = []
        highs = []
        for m in re.finditer(r'"(?:estimate_low|lowEstimate|low_estimate|estimateLow)"\s*:\s*("?)([0-9,]+(?:\.[0-9]{1,2})?)\1', html, re.IGNORECASE):
            d = _to_decimal_money(m.group(2))
            if d is not None:
                lows.append(d)
        for m in re.finditer(r'"(?:estimate_high|highEstimate|high_estimate|estimateHigh)"\s*:\s*("?)([0-9,]+(?:\.[0-9]{1,2})?)\1', html, re.IGNORECASE):
            d = _to_decimal_money(m.group(2))
            if d is not None:
                highs.append(d)
        if lows and highs:
            low = low or _sanitize_money(min(lows))
            high = high or _sanitize_money(min(highs))

    return low, high, reserve

def _extract_sothebys_christies_estimates(html: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    text = _clean_html_text(html)
    low, high = _extract_text_estimate_range(text)
    reserve = _extract_reserve_from_text(text)
    return low, high, reserve

# Generic auction estimate helper (used to avoid NameError if a site-specific function is missing)
def _get_auction_estimates_by_host(host: str, html: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Centralized logic to get auction estimates. Tries site-specific extractors first (if host matches),
    otherwise falls back to text-based extraction. This prevents NameError issues if a specific extractor
    is not available.
    """
    try:
        if host.endswith("liveauctioneers.com"):
            return _extract_liveauctioneers_estimates(html)
        if host.endswith("bidsquare.com"):
            # Bidsquare may use text or JSON; try its extractor if present
            return _extract_bidsquare_estimates(html)
        # default fallback for Sotheby's/Christie's and others
        return _extract_sothebys_christies_estimates(html)
    except NameError:
        # In case a per-site extractor isn't available for some reason, fall back to text-based extraction
        text = _clean_html_text(html)
        low, high = _extract_text_estimate_range(text)
        reserve = _extract_reserve_from_text(text)
        return low, high, reserve
    except Exception:
        # Any unexpected parsing error => return Nones to avoid crashing
        return None, None, None


# ==========================================
# 10) Gemini fallback extraction from page text
# ==========================================
def _gemini_extract_auction_from_text(page_text: str, url: str) -> Dict[str, Optional[str]]:
    page_text = page_text[:14000]
    prompt = f"""
Extract auction estimate values from the text below (source URL included).
Return ONLY JSON: {{"low_estimate":"$...","high_estimate":"$...","reserve":"$..."}}.
If any value is not present, set it to null. Do not guess.

URL: {url}

TEXT:
{page_text}
"""
    data = _gemini_json(prompt)
    out = {
        "auction_low": _sanitize_money(data.get("low_estimate")),
        "auction_high": _sanitize_money(data.get("high_estimate")),
        "auction_reserve": _sanitize_money(data.get("reserve")),
    }
    return out

def _gemini_extract_retail_from_text(page_text: str, url: str) -> Dict[str, Optional[str]]:
    page_text = page_text[:14000]
    prompt = f"""
Extract the retail listing price from the text below (source URL included).
Return ONLY JSON: {{"retail_price":"$..."}}. If not present, retail_price must be null. Do not guess.

URL: {url}

TEXT:
{page_text}
"""
    data = _gemini_json(prompt)
    return {"retail_price": _sanitize_money(data.get("retail_price"))}


# ==========================================
# 11) Enrichment with scrape + Chairish improvements (canonical product requirement & thumbnail heuristics)
# ==========================================
def _is_likely_thumbnail_url(img_url: str) -> bool:
    if not img_url:
        return False
    low = img_url.lower()
    if "fit&width=265&height=265" in low or "width=265" in low or "height=265" in low:
        return True
    if re.search(r'width=\d+&height=\d+', low):
        m = re.search(r'width=(\d+)', low)
        if m and int(m.group(1)) < 500:
            return True
    if any(tok in low for tok in ("/thumbs/", "/thumbnail", "thumbnail=", "thumb=", "/small", "/w_")):
        return True
    return False

def _basename_from_url(u: str) -> str:
    if not u:
        return ""
    u = u.split("?")[0].split("#")[0]
    return os.path.basename(u)

def _score_candidate_by_image_and_title(candidate_snippet: str, candidate_img_src: str, match_thumb_basename: str, match_title_words: List[str]) -> int:
    score = 0
    if candidate_img_src and not _is_likely_thumbnail_url(candidate_img_src):
        cand_basename = _basename_from_url(candidate_img_src).lower()
        if match_thumb_basename and cand_basename == match_thumb_basename.lower():
            score += 100
        elif match_thumb_basename and match_thumb_basename.lower() in cand_basename:
            score += 50
        else:
            score += 5
    snippet_lower = (candidate_snippet or "").lower()
    for w in match_title_words:
        if w in snippet_lower:
            score += 10
    return score

def _find_chairish_product_link_by_image(html: str, match_thumbnail: Optional[str], match_title: str, base_url: str) -> Optional[str]:
    canonical_prefix = f"{base_url.rstrip('/')}/product/"
    match_thumb_basename = _basename_from_url(match_thumbnail) if match_thumbnail else ""
    title_words = re.findall(r'\w{4,}', (match_title or "").lower())

    candidates: List[Tuple[int, str]] = []

    for m in re.finditer(r'<a[^>]+href=["\']([^"\']*?/product/[^"\']+)["\'][^>]*>(.*?)</a>', html, flags=re.IGNORECASE | re.DOTALL):
        href = m.group(1).strip()
        inner = m.group(2) or ""
        full_href = href if href.startswith("http") else urljoin(base_url, href)
        if not full_href.startswith(canonical_prefix):
            continue
        img_m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', inner, flags=re.IGNORECASE)
        img_src = img_m.group(1).strip() if img_m else ""
        if img_src and img_src.startswith("/"):
            img_src = urljoin(base_url, img_src)
        start = max(0, m.start() - 200)
        end = min(len(html), m.end() + 200)
        snippet = html[start:end]
        score = _score_candidate_by_image_and_title(snippet, img_src, match_thumb_basename, title_words)
        candidates.append((score, full_href))

    if not candidates:
        for m in re.finditer(r'(https?://[^"\'>\s]*chairish\.com/product/[^"\'>\s]+)', html, re.IGNORECASE):
            url = m.group(1).strip()
            if url.startswith(canonical_prefix):
                candidates.append((1, url))

    if not candidates:
        return None

    candidates.sort(reverse=True, key=lambda x: x[0])
    best_score, best_url = candidates[0]
    if best_score >= 1 and best_url.startswith(canonical_prefix):
        return best_url
    return None

def enrich_matches_with_prices(matches: list[dict], max_to_scrape: int = 10) -> list[dict]:
    if "scrape_cache" not in st.session_state:
        st.session_state["scrape_cache"] = {}
    session = _get_session()

    if st.session_state.get("use_la_login", False):
        _try_login_liveauctioneers(session)

    debug_chair = st.session_state.get("debug_chairish", False)

    scraped = 0
    for m in matches:
        if scraped >= max_to_scrape:
            break

        original_url = (m.get("link") or "").strip()
        if not original_url:
            continue

        host = _hostname(original_url)
        is_target = any(host.endswith(d) for d in (AUCTION_DOMAINS | RETAIL_DOMAINS))
        if not is_target:
            continue

        m.setdefault("kind", _kind_from_domain(original_url))
        kind = m.get("kind")

        match_title = (m.get("title") or "").strip()
        match_thumbnail = (m.get("thumbnail") or "").strip()

        cache_key = original_url
        if cache_key in st.session_state["scrape_cache"]:
            cached_update = st.session_state["scrape_cache"][cache_key]
            m.update(cached_update)
            scraped += 1
            continue

        html, status = _fetch_html(original_url, session)
        update: Dict[str, Any] = {"_http_status": status}

        if not html:
            st.session_state["scrape_cache"][cache_key] = update
            m.update(update)
            scraped += 1
            continue

        # Chairish-specific canonical enforcement + heuristics
        if host.endswith("chairish.com"):
            parsed = urlparse(original_url)
            base_url = f"{parsed.scheme}://{parsed.netloc}"
            canonical_prefix = f"{base_url.rstrip('/')}/product/"

            product_url = None
            if not original_url.startswith(canonical_prefix):
                try:
                    product_url = _find_chairish_product_link_by_image(html, match_thumbnail, match_title, base_url)
                except Exception:
                    product_url = None

                if not product_url and match_title:
                    q = quote_plus(match_title)
                    search_url = f"{base_url}/search?query={q}"
                    s_html, s_status = _fetch_html(search_url, session)
                    if s_html:
                        try:
                            product_url = _find_chairish_product_link_by_image(s_html, match_thumbnail, match_title, base_url)
                        except Exception:
                            product_url = None

                if product_url and product_url.startswith(canonical_prefix):
                    update["product_url"] = product_url
                    update["link"] = product_url
                    if debug_chair:
                        st.write("Chairish: resolved product_url for match:", match_title)
                        st.write(" original:", original_url)
                        st.write(" chosen product_url:", product_url)
                else:
                    if debug_chair:
                        st.write("Chairish: did NOT resolve product detail for:", match_title)
                        st.write(" original:", original_url)
                        st.write(" candidate product_url:", product_url)
            else:
                update["product_url"] = original_url
                if debug_chair:
                    st.write("Chairish: original link is canonical product:", original_url)

        # scrape prices/estimates (use _get_auction_estimates_by_host to avoid NameError)
        try:
            if kind == "retail":
                if host.endswith("1stdibs.com"):
                    rp = _extract_1stdibs_price(html)
                elif host.endswith("chairish.com"):
                    rp = _extract_chairish_price(html)
                else:
                    rp = _extract_retail_price_generic(html)
                update["retail_price"] = rp

                if (not update.get("retail_price")) and st.session_state.get("use_gemini", True):
                    text = _clean_html_text(html)
                    ai = _gemini_extract_retail_from_text(text, original_url)
                    if ai.get("retail_price"):
                        update["retail_price"] = ai["retail_price"]

            elif kind == "auction":
                # use central helper that won't raise NameError
                low, high, reserve = _get_auction_estimates_by_host(host, html)
                update["auction_low"] = low
                update["auction_high"] = high
                update["auction_reserve"] = reserve

                if (not update.get("auction_low") or not update.get("auction_high")) and st.session_state.get("use_gemini", True):
                    text = _clean_html_text(html)
                    ai = _gemini_extract_auction_from_text(text, original_url)
                    update["auction_low"] = update.get("auction_low") or ai.get("auction_low")
                    update["auction_high"] = update.get("auction_high") or ai.get("auction_high")
                    update["auction_reserve"] = update.get("auction_reserve") or ai.get("auction_reserve")
        except Exception:
            # Do not let any parsing error crash the whole app
            pass

        st.session_state["scrape_cache"][cache_key] = update
        m.update(update)
        scraped += 1

    return matches


# ==========================================
# 12) GOOGLE SHEETS EXPORT (unchanged schema)
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
# 13) UI RENDERER (HTML-escaped values)
# ==========================================
def _strip_tags(v: Any) -> str:
    if v is None:
        return ""
    return re.sub(r"<[^>]+>", "", str(v))

def _display_money_value(v: Any) -> str:
    if v is None:
        return "—"
    sanitized = _sanitize_money(v)
    if sanitized:
        return sanitized
    s = str(v)
    m = MONEY_CAPTURE_RE.search(_strip_tags(s))
    if m:
        try:
            return _format_money(Decimal(m.group(1).replace(",", "")))
        except Exception:
            pass
    stripped = _strip_tags(s).strip()
    return html_escape(stripped) if stripped else "—"

def _pill_html(label: str, value_text: str) -> str:
    safe_label = html_escape(label)
    safe_value = html_escape(value_text)
    return f'<span class="pill">{safe_label}: {safe_value}</span>'

def render_match_card_native(m: dict, kind_for_view: str):
    thumb = m.get("thumbnail") or ""
    title = (m.get("title") or "Untitled")
    source = (m.get("source") or "Unknown")
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
            st.markdown(f"**{html_escape(title)}**")
            st.markdown(f"<span class=\"meta\">Source: {html_escape(source)}</span>", unsafe_allow_html=True)

            if kind_for_view == "auction":
                pills_html = (
                    _pill_html("Low Estimate", _display_money_value(m.get("auction_low")))
                    + " "
                    + _pill_html("High Estimate", _display_money_value(m.get("auction_high")))
                    + " "
                    + _pill_html("Auction Reserve", _display_money_value(m.get("auction_reserve")))
                )
                components.html(f"<div style='display:flex;gap:8px;align-items:center'>{pills_html}</div>", height=56, scrolling=False)

            elif kind_for_view == "retail":
                pill = _pill_html("Retail Price", _display_money_value(m.get("retail_price")))
                components.html(f"<div style='display:flex;gap:8px;align-items:center'>{pill}</div>", height=48, scrolling=False)

            else:
                conf = m.get("confidence")
                if conf is not None:
                    st.markdown(_pill_html("Confidence", str(conf)), unsafe_allow_html=True)

            if link:
                try:
                    st.link_button("View Listing", link, use_container_width=False)
                except TypeError:
                    st.markdown(f"[VIEW LISTING]({link})")

        st.markdown("</div>", unsafe_allow_html=True)


# ==========================================
# 14) CONTENT GENERATION (Gemini)
# ==========================================
def _content_context_for_mode(results: dict, mode: str) -> str:
    matches = results.get("traceability", {}).get("search_summary", {}).get("top_matches", [])
    relevant = [m for m in matches if m.get("kind") == mode][:6]

    lines = []
    for i, m in enumerate(relevant, start=1):
        title = (m.get("title") or "").strip()
        source = (m.get("source") or "").strip()
        link = (m.get("link") or "").strip()
        if mode == "auction":
            low = _display_money_value(m.get("auction_low")) or "—"
            high = _display_money_value(m.get("auction_high")) or "—"
            reserve = _display_money_value(m.get("auction_reserve")) or "—"
            lines.append(f"{i}. {title} | {source} | low={low}, high={high}, reserve={reserve} | {link}")
        else:
            rp = _display_money_value(m.get("retail_price")) or "—"
            lines.append(f"{i}. {title} | {source} | retail_price={rp} | {link}")

    sku = results.get("traceability", {}).get("sku_label", "")
    img_url = results.get("traceability", {}).get("s3", {}).get("presigned_url", "")

    return f"""SKU: {sku}
Image URL: {img_url}
Mode: {mode}
Reference Listings:
{chr(10).join(lines)}
""".strip()

def generate_auction_title(results: dict) -> str:
    ctx = _content_context_for_mode(results, "auction")
    prompt = f"""
You are an expert auction cataloger.
Create a concise, high-quality AUCTION TITLE (max 12 words).
Use the reference listings as guidance, but do NOT include source names.
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
# 15) SIDEBAR + MAIN UI (traceback capture in-app)
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
    st.toggle("AI Mode", value=True, key="use_gemini")
    st.toggle("Scrape prices/estimates from listing pages", value=True, key="use_scrape_prices")
    st.slider("Max listing links to process per run", 0, 20, 10, key="max_scrape_links")

    st.toggle("Use LiveAuctioneers Login (optional)", value=False, key="use_la_login")
    st.caption("Add LIVEAUCTIONEERS_USERNAME and LIVEAUCTIONEERS_PASSWORD in Streamlit secrets to enable.")

    st.divider()
    st.checkbox("Debug Chairish linking", value=False, key="debug_chairish")
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
run = st.button("Run Appraisal", disabled=not uploaded_file)

if run:
    # Wrap the entire processing in try/except and display full traceback in-app for debugging
    with st.spinner("Processing..."):
        try:
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

            for m in raw_matches:
                m["kind"] = _kind_from_domain(m.get("link") or "")
                m.setdefault("confidence", 0.75 if m["kind"] in ("auction", "retail") else 0.35)

            # Only attempt scraping/enrichment if enabled; guard against exceptions so app doesn't crash
            if st.session_state.get("use_scrape_prices", True):
                try:
                    raw_matches = enrich_matches_with_prices(
                        raw_matches,
                        max_to_scrape=int(st.session_state.get("max_scrape_links", 10)),
                    )
                except Exception as e:
                    # capture and show traceback in-app
                    tb = traceback.format_exc()
                    st.error("Error during enrich_matches_with_prices — full traceback follows below.")
                    st.code(tb)
                    # preserve raw_matches as-is so UI can still show SerpAPI results
            # save results
            st.session_state["results"] = {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "traceability": {
                    "sku_label": st.session_state.get("uploaded_image_meta", {}).get("filename", ""),
                    "s3": {"presigned_url": presigned_url},
                    "search_summary": {"top_matches": raw_matches},
                },
            }

            st.session_state["content_outputs"] = {
                "auction_title": "",
                "auction_description": "",
                "newel_title": "",
                "newel_description": "",
                "keywords": "",
            }

        except Exception as e:
            tb = traceback.format_exc()
            # Show clear UI feedback + full traceback for debugging
            st.error("An unexpected error occurred while running the appraisal. Full traceback:")
            st.code(tb)
            # Also store in session for later inspection
            st.session_state["last_run_traceback"] = tb

st.header("3. Results")

res = st.session_state.get("results")
if not res:
    st.info("No appraisal run yet. Upload a photo above to begin.")
else:
    view_mode = st.radio(
        "View",
        options=["Auction Results", "Retail Listings", "Other Matches"],
        horizontal=True,
        key="results_view_mode",
        label_visibility="collapsed",
    )

    left_col, right_col = st.columns([1.35, 1.0], gap="large")

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

    with right_col:
        st.header("4. Content Generation")
        if not st.session_state.get("use_gemini", True):
            st.info("Turn on **AI Mode** in the sidebar to generate content.")
        else:
            if view_mode == "Auction Results":
                st.subheader("Auction Content")
                if st.button("Generate Auction Title"):
                    with st.spinner("Generating Auction Title..."):
                        st.session_state["content_outputs"]["auction_title"] = generate_auction_title(res)
                st.text_area("Auction Title", value=st.session_state["content_outputs"].get("auction_title", ""), height=90)

                if st.button("Generate Auction Description"):
                    with st.spinner("Generating Auction Description..."):
                        st.session_state["content_outputs"]["auction_description"] = generate_auction_description(res)
                st.text_area("Auction Description", value=st.session_state["content_outputs"].get("auction_description", ""), height=240)

            elif view_mode == "Retail Listings":
                st.subheader("Newel Content")
                if st.button("Generate Newel Title"):
                    with st.spinner("Generating Newel Title..."):
                        st.session_state["content_outputs"]["newel_title"] = generate_newel_title(res)
                st.text_area("Newel Title", value=st.session_state["content_outputs"].get("newel_title", ""), height=90)

                if st.button("Generate Newel Description"):
                    with st.spinner("Generating Newel Description..."):
                        st.session_state["content_outputs"]["newel_description"] = generate_newel_description(res)
                st.text_area("Newel Description", value=st.session_state["content_outputs"].get("newel_description", ""), height=240)

                if st.button("Generate keywords"):
                    with st.spinner("Generating keywords..."):
                        st.session_state["content_outputs"]["keywords"] = generate_keywords(res)
                st.text_area("SEO Keywords", value=st.session_state["content_outputs"].get("keywords", ""), height=150)
            else:
                st.info("Select **Auction Results** or **Retail Listings** to generate content.")
