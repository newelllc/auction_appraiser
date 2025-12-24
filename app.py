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

html, body { background: var(--bg) !important; color: var(--text) !important; color-scheme: light !important; }
.stApp { background: var(--bg) !important; color: var(--text) !important; font-family: 'EB Garamond', serif !important; }

[data-testid="stHeader"], [data-testid="stToolbar"], header { background: var(--bg) !important; }
[data-testid="stAppViewContainer"], [data-testid="stMain"], section.main { background: var(--bg) !important; }

.stApp, .stApp * { color: var(--text) !important; font-family: 'EB Garamond', serif !important; }

section[data-testid="stSidebar"]{ background: var(--bg2) !important; border-right: 1px solid var(--border) !important; }
section[data-testid="stSidebar"] *{ color: var(--text) !important; }

h1, h2, h3 {
  font-family: 'EB Garamond', serif !important;
  color: var(--burgundy) !important;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-weight: 700 !important;
}
h1 { font-size: 2.4rem !important; margin-bottom: 0.25rem !important; }
h2 { font-size: 1.6rem !important; margin-top: 1.25rem !important; }

.newel-logo-text {
  font-weight: 700 !important;
  font-size: 3.4rem !important;
  letter-spacing: 0.18em !important;
  line-height: 1.0 !important;
  color: var(--burgundy) !important;
  margin: 0.25rem 0 0.8rem 0 !important;
}

[data-testid="stFileUploader"] section,
[data-testid="stFileUploaderDropzone"]{
  background: var(--card) !important;
  border: 1px dashed var(--border) !important;
  border-radius: 12px !important;
}

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

.stTabs [data-baseweb="tab"]{ font-weight: 700 !important; letter-spacing: 0.08em !important; text-transform: uppercase !important; }
.stTabs [data-baseweb="tab"][aria-selected="true"]{ color: var(--burgundy) !important; }

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


def _hostname(url: str) -> str:
    try:
        h = urlparse(url).netloc.lower()
        if h.startswith("www."):
            h = h[4:]
        return h
    except Exception:
        return ""


# ==========================================
# 4. DOMAIN-BASED KIND
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
# 5. GEMINI (optional) + fallback
# ==========================================
def _simple_kind_fallback(match: dict) -> dict:
    link = (match.get("link") or "").strip()
    match["kind"] = _kind_from_domain(link)
    match["confidence"] = 0.70 if match["kind"] in ("auction", "retail") else 0.35
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
            m.setdefault("kind", _kind_from_domain(m.get("link") or ""))
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
                    m.setdefault("kind", _kind_from_domain(m.get("link") or ""))
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
                "Continuing with domain-based classification + scraping."
            )
            st.session_state["gemini_error_banner_shown"] = True

        st.session_state["gemini_last_error"] = str(e)
        return [_simple_kind_fallback(m) for m in matches]


# ==========================================
# 6. SCRAPING: DOMAIN-TARGETED PARSERS
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
    r'(?:(?:USD|US\$)\s*)?\$\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})?|[0-9]+(?:\.[0-9]{2})?)',
    re.IGNORECASE,
)
RANGE_RE = re.compile(
    r'\$\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})?)\s*(?:-|–|to)\s*\$\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})?)',
    re.IGNORECASE,
)

# embedded JSON hints
USD_PRICE_NEAR_CURRENCY_RE = re.compile(
    r'"price"\s*:\s*"?([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})?|[0-9]+(?:\.[0-9]{2})?)"?'
    r'.{0,120}?"(?:priceCurrency|currency|currencyCode)"\s*:\s*"?USD"?',
    re.IGNORECASE | re.DOTALL,
)
CENTS_RE = re.compile(r'"price_cents"\s*:\s*([0-9]{3,})', re.IGNORECASE)


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
        raw = (m.group(1) or "").strip()
        if not raw:
            continue
        raw = raw.strip("<!--").strip("-->")
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


def _normalize_money(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, (int, float, Decimal)):
        d = Decimal(str(v))
    else:
        s = str(v).strip()
        mm = re.search(r"([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})?|[0-9]+(?:\.[0-9]{2})?)", s)
        if not mm:
            return None
        try:
            d = Decimal(mm.group(1).replace(",", ""))
        except InvalidOperation:
            return None

    if d == d.to_integral():
        return f"${int(d):,}"
    return f"${d:,.2f}"


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


def _jsonld_find_offers_prices(blocks: List[Any]) -> List[Decimal]:
    """
    Walk JSON-LD to find Offer.price (USD) candidates.
    """
    out: List[Decimal] = []

    def rec(x: Any):
        if isinstance(x, dict):
            t = str(x.get("@type", "")).lower()
            if t == "offer" or "offer" in t:
                cur = (x.get("priceCurrency") or x.get("currency") or "").strip()
                price = x.get("price") or x.get("lowPrice") or x.get("highPrice")
                if cur.upper() == "USD" and price is not None:
                    d = _as_decimal_money(price)
                    if d is not None:
                        out.append(d)
            # sometimes offers nested under "offers"
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


# ---------- Retail: 1stDibs ----------
def _extract_1stdibs_price(html: str) -> Optional[str]:
    """
    1stDibs pages often contain multiple monetary values.
    Strategy:
      1) Prefer JSON-LD Offer.price with USD
      2) Then embedded JSON `"price": X` near `"currency":"USD"`
      3) Then meta tags like product:price:amount
      4) Choose lowest plausible candidate (fixes inflated picks like 96,516.63 vs 9,622.18)
    """
    candidates: List[Decimal] = []

    blocks = _parse_jsonld_blocks(html)
    candidates.extend(_jsonld_find_offers_prices(blocks))

    # embedded JSON: price near currency USD
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

    # filter plausible: > 10 and < 2,000,000
    plausible = [c for c in candidates if c > Decimal("10") and c < Decimal("2000000")]

    if not plausible:
        return None

    # pick the lowest plausible retail price (most reliable for 1stDibs pages with multiple amounts)
    best = min(plausible)
    return _normalize_money(best)


# ---------- Retail: Chairish ----------
def _extract_chairish_price(html: str) -> Optional[str]:
    """
    Chairish often includes price in:
      - JSON-LD offers
      - embedded price_cents
      - embedded price + USD currency
    """
    blocks = _parse_jsonld_blocks(html)
    prices = _jsonld_find_offers_prices(blocks)
    if prices:
        return _normalize_money(min(prices))

    # price_cents is common
    cents = []
    for m in CENTS_RE.finditer(html):
        try:
            cents.append(int(m.group(1)))
        except Exception:
            continue
        if len(cents) >= 8:
            break
    if cents:
        # choose smallest plausible cents > $10
        cents_plaus = [c for c in cents if c >= 1000]
        if cents_plaus:
            best = min(cents_plaus)
            return _normalize_money(Decimal(best) / Decimal(100))

    # embedded JSON price near USD
    for m in USD_PRICE_NEAR_CURRENCY_RE.finditer(html):
        d = _as_decimal_money(m.group(1))
        if d is not None and d > Decimal("10"):
            return _normalize_money(d)

    # meta fallback
    meta = _extract_meta_map(html)
    for k in ("product:price:amount", "og:price:amount"):
        if k in meta:
            d = _as_decimal_money(meta[k])
            if d is not None:
                return _normalize_money(d)

    return None


# ---------- Retail: Incollect / RauAntiques (generic retail with stronger selection) ----------
def _extract_retail_price_generic_strict(html: str) -> Optional[str]:
    blocks = _parse_jsonld_blocks(html)
    prices = _jsonld_find_offers_prices(blocks)
    if prices:
        return _normalize_money(min(prices))

    # fallback: look for first "$x" near "price"
    text = _clean_html_text(html)
    idx = text.lower().find("price")
    window = text[max(0, idx - 1500): idx + 3000] if idx != -1 else text[:120000]

    vals = []
    for m in MONEY_RE.finditer(window):
        d = _as_decimal_money(m.group(1))
        if d is not None:
            vals.append(d)
        if len(vals) >= 8:
            break
    vals = [v for v in vals if v > Decimal("10") and v < Decimal("2000000")]
    if not vals:
        return None
    return _normalize_money(min(vals))


# ---------- Auctions: LiveAuctioneers ----------
def _extract_liveauctioneers_estimates(html: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Pull low/high estimates from embedded JSON patterns on LiveAuctioneers.
    Reserve is rarely public; best-effort.
    """
    low = None
    high = None
    reserve = None

    # patterns like "lowEstimate":{"amount":1234,"currency":"USD"}
    mlo = re.search(r'"lowEstimate"\s*:\s*\{[^}]*"amount"\s*:\s*([0-9]+(?:\.[0-9]{1,2})?)', html, re.IGNORECASE)
    mhi = re.search(r'"highEstimate"\s*:\s*\{[^}]*"amount"\s*:\s*([0-9]+(?:\.[0-9]{1,2})?)', html, re.IGNORECASE)
    if mlo:
        low = _normalize_money(mlo.group(1))
    if mhi:
        high = _normalize_money(mhi.group(1))

    # alternative flat keys
    mlo2 = re.search(r'"(?:estimate_low|low_estimate|lowEstimate)"\s*:\s*"?([0-9,]+(?:\.[0-9]{1,2})?)"?', html, re.IGNORECASE)
    mhi2 = re.search(r'"(?:estimate_high|high_estimate|highEstimate)"\s*:\s*"?([0-9,]+(?:\.[0-9]{1,2})?)"?', html, re.IGNORECASE)
    if not low and mlo2:
        low = _normalize_money(mlo2.group(1))
    if not high and mhi2:
        high = _normalize_money(mhi2.group(1))

    # reserve (rare) near the word reserve
    text = _clean_html_text(html)
    reserve = _extract_reserve_from_text(text)

    # final fallback: range near estimate
    if not low or not high:
        rlo, rhi = _extract_estimate_range_from_text(text)
        low = low or rlo
        high = high or rhi

    return low, high, reserve


# ---------- Auctions: Bidsquare ----------
def _extract_bidsquare_estimates(html: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    low = None
    high = None
    reserve = None

    # JSON-LD can contain lowPrice/highPrice
    blocks = _parse_jsonld_blocks(html)
    # attempt to find lowPrice/highPrice anywhere
    def find_low_high(obj: Any) -> Tuple[Optional[Decimal], Optional[Decimal]]:
        lo = None
        hi = None
        def rec(x: Any):
            nonlocal lo, hi
            if isinstance(x, dict):
                for k, v in x.items():
                    lk = str(k).lower()
                    if lk in ("lowprice", "lowestimate", "estimatelow", "estimate_low", "low_estimate"):
                        dv = _as_decimal_money(v)
                        if dv is not None:
                            lo = lo or dv
                    if lk in ("highprice", "highestimate", "estimatehigh", "estimate_high", "high_estimate"):
                        dv = _as_decimal_money(v)
                        if dv is not None:
                            hi = hi or dv
                    rec(v)
            elif isinstance(x, list):
                for it in x:
                    rec(it)
        rec(obj)
        return lo, hi

    for b in blocks:
        dlo, dhi = find_low_high(b)
        if dlo and not low:
            low = _normalize_money(dlo)
        if dhi and not high:
            high = _normalize_money(dhi)
        if low and high:
            break

    # embedded JSON keys
    if not low:
        mlo = re.search(r'"(?:estimate_low|lowEstimate|low_estimate|estimateLow)"\s*:\s*("?)([0-9,]+(?:\.[0-9]{1,2})?)\1', html, re.IGNORECASE)
        if mlo:
            low = _normalize_money(mlo.group(2))
    if not high:
        mhi = re.search(r'"(?:estimate_high|highEstimate|high_estimate|estimateHigh)"\s*:\s*("?)([0-9,]+(?:\.[0-9]{1,2})?)\1', html, re.IGNORECASE)
        if mhi:
            high = _normalize_money(mhi.group(2))

    text = _clean_html_text(html)
    reserve = _extract_reserve_from_text(text)

    if not low or not high:
        rlo, rhi = _extract_estimate_range_from_text(text)
        low = low or rlo
        high = high or rhi

    return low, high, reserve


# ---------- Auctions: Sothebys / Christies (json-ld + estimate range fallback) ----------
def _extract_sothebys_christies_estimates(html: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    low = None
    high = None
    reserve = None

    blocks = _parse_jsonld_blocks(html)
    # try lowPrice/highPrice
    prices = []
    for b in blocks:
        # use the same walker as retail offers but allow low/high
        # collect any low/high as candidates
        def rec(x: Any):
            if isinstance(x, dict):
                for k, v in x.items():
                    lk = str(k).lower()
                    if lk in ("lowprice", "lowestimate", "estimatelow", "estimate_low", "low_estimate"):
                        d = _as_decimal_money(v)
                        if d is not None:
                            prices.append(("low", d))
                    if lk in ("highprice", "highestimate", "estimatehigh", "estimate_high", "high_estimate"):
                        d = _as_decimal_money(v)
                        if d is not None:
                            prices.append(("high", d))
                    rec(v)
            elif isinstance(x, list):
                for it in x:
                    rec(it)
        rec(b)

    lows = [d for t, d in prices if t == "low"]
    highs = [d for t, d in prices if t == "high"]
    if lows:
        low = _normalize_money(min(lows))
    if highs:
        high = _normalize_money(min(highs))

    text = _clean_html_text(html)
    reserve = _extract_reserve_from_text(text)
    if not low or not high:
        rlo, rhi = _extract_estimate_range_from_text(text)
        low = low or rlo
        high = high or rhi

    return low, high, reserve


def _extract_estimate_range_from_text(text: str) -> Tuple[Optional[str], Optional[str]]:
    lowered = text.lower()
    idx = lowered.find("estimate")
    windows = []
    if idx != -1:
        windows.append(text[max(0, idx - 4000): idx + 4000])
    windows.append(text[:160000])
    for w in windows:
        rm = RANGE_RE.search(w)
        if rm:
            return "$" + rm.group(1), "$" + rm.group(2)
    return None, None


def _extract_reserve_from_text(text: str) -> Optional[str]:
    lowered = text.lower()
    if "reserve" not in lowered:
        return None
    pos = lowered.find("reserve")
    window = text[max(0, pos - 2500): pos + 2500]
    mm = MONEY_RE.search(window)
    if mm:
        return "$" + mm.group(1)
    return None


def enrich_matches_with_scraped_prices(matches: list[dict], max_to_scrape: int = 10) -> list[dict]:
    """
    Scrape listing pages for ONLY the requested domains and populate:
      - auction_low, auction_high, auction_reserve
      - retail_price
    Uses domain-specific parsing (fixes Chairish, 1stDibs, LiveAuctioneers, Bidsquare).
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

        host = _hostname(url)
        is_target = any(host.endswith(d) for d in (AUCTION_DOMAINS | RETAIL_DOMAINS))
        if not is_target:
            continue

        m.setdefault("kind", _kind_from_domain(url))
        kind = m.get("kind")

        # skip if already present
        if kind == "retail" and m.get("retail_price"):
            continue
        if kind == "auction" and m.get("auction_low") and m.get("auction_high") and m.get("auction_reserve"):
            continue

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

        # Retail domain handling
        if kind == "retail":
            if host.endswith("1stdibs.com"):
                rp = _extract_1stdibs_price(html)
            elif host.endswith("chairish.com"):
                rp = _extract_chairish_price(html)
            else:
                rp = _extract_retail_price_generic_strict(html)

            if rp:
                update["retail_price"] = rp

        # Auction domain handling
        elif kind == "auction":
            if host.endswith("liveauctioneers.com"):
                low, high, reserve = _extract_liveauctioneers_estimates(html)
            elif host.endswith("bidsquare.com"):
                low, high, reserve = _extract_bidsquare_estimates(html)
            else:
                low, high, reserve = _extract_sothebys_christies_estimates(html)

            if low:
                update["auction_low"] = low
            if high:
                update["auction_high"] = high
            if reserve:
                update["auction_reserve"] = reserve

        # Ensure keys exist
        if kind == "auction":
            update.setdefault("auction_low", m.get("auction_low"))
            update.setdefault("auction_high", m.get("auction_high"))
            update.setdefault("auction_reserve", m.get("auction_reserve"))
        if kind == "retail":
            update.setdefault("retail_price", m.get("retail_price"))

        st.session_state["scrape_cache"][url] = update
        m.update(update)
        scraped += 1

    return matches


# ==========================================
# 7. GOOGLE SHEETS EXPORT (3 comps schema)
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
# 8. UI RENDERER (native, clickable)
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
# 9. SIDEBAR + MAIN UI
# ==========================================
with st.sidebar:
    st.markdown("<div class='newel-logo-text'>NEWEL</div>", unsafe_allow_html=True)

    st.toggle("Use Gemini classification", value=True, key="use_gemini")
    st.toggle("Scrape prices/estimates from listing pages", value=True, key="use_scrape_prices")
    st.slider("Max listing links to scrape per run", 0, 20, 10, key="max_scrape_links")

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

        top_matches = upgrade_comps_with_gemini(raw_matches)

        if st.session_state.get("use_scrape_prices", True):
            top_matches = enrich_matches_with_scraped_prices(
                top_matches, max_to_scrape=int(st.session_state.get("max_scrape_links", 10))
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
