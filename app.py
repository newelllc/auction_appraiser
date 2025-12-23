import os
import uuid
from datetime import datetime

import boto3
import requests
import streamlit as st

from google.oauth2 import service_account
from google.auth.transport.requests import Request

# =========================
# Page Config
# =========================
st.set_page_config(page_title="Newel Appraiser MVP", layout="centered")
st.title("Newel Appraiser")
st.caption("Internal MVP • Image-based appraisal")

# =========================
# Secrets helper
# =========================
def _get_secret(name: str, default: str | None = None) -> str:
    # Streamlit Cloud secrets live in st.secrets; fallback to env for local dev.
    if name in st.secrets:
        return str(st.secrets[name])
    val = os.getenv(name, default)
    if val is None:
        raise RuntimeError(f"Missing required secret/env var: {name}")
    return str(val)

# =========================
# S3 helpers
# =========================
def _s3_client():
    return boto3.client(
        "s3",
        region_name=_get_secret("AWS_REGION"),
        aws_access_key_id=_get_secret("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=_get_secret("AWS_SECRET_ACCESS_KEY"),
    )

def upload_bytes_to_s3_and_presign(*, file_bytes: bytes, content_type: str, original_filename: str) -> dict:
    bucket = _get_secret("S3_BUCKET")
    prefix = _get_secret("S3_PREFIX", "").strip("/")
    ttl = int(_get_secret("PRESIGNED_URL_TTL_SECONDS", "3600"))

    ext = ""
    if "." in (original_filename or ""):
        ext = "." + original_filename.split(".")[-1].lower()

    key = f"{uuid.uuid4().hex}{ext}"
    if prefix:
        key = f"{prefix}/{key}"

    client = _s3_client()
    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=file_bytes,
        ContentType=content_type or "application/octet-stream",
        Metadata={
            "original_filename": (original_filename or "")[:200],
            "uploaded_utc": datetime.utcnow().isoformat(),
        },
    )

    url = client.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=ttl,
    )

    return {"bucket": bucket, "key": key, "presigned_url": url, "ttl_seconds": ttl}

# =========================
# SerpApi (Google Lens)
# =========================
def serpapi_google_lens_search(image_url: str) -> dict:
    api_key = _get_secret("SERPAPI_API_KEY")
    params = {"engine": "google_lens", "url": image_url, "api_key": api_key}
    resp = requests.get("https://serpapi.com/search.json", params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()

# =========================
# Match extraction + CSV helpers
# =========================
def extract_top_lens_matches(lens_json: dict, limit: int = 5) -> list[dict]:
    matches: list[dict] = []
    for item in (lens_json or {}).get("visual_matches", [])[:limit]:
        price = item.get("price")
        if isinstance(price, dict):
            price_value = price.get("value", "")
            currency = price.get("currency", "")
        else:
            price_value = price or ""
            currency = ""

        matches.append({
            "title": item.get("title") or "",
            "source": item.get("source") or item.get("domain") or "",
            "link": item.get("link") or "",
            "thumbnail": item.get("thumbnail") or "",
            "price": price_value,
            "currency": currency,
        })

    # Fallback if response shape differs
    if not matches:
        for item in (lens_json or {}).get("shopping_results", [])[:limit]:
            matches.append({
                "title": item.get("title") or "",
                "source": item.get("source") or item.get("seller") or "",
                "link": item.get("link") or "",
                "thumbnail": item.get("thumbnail") or "",
                "price": item.get("price") or "",
                "currency": item.get("currency") or "",
            })
    return matches

def csv_safe(text: str) -> str:
    return (text or "").replace('"', '""').replace("\n", " ").replace("\r", " ").strip()

def summarize_matches_for_csv(top_matches: list[dict]) -> dict:
    titles, sources, links, prices = [], [], [], []
    for m in top_matches or []:
        titles.append(csv_safe(m.get("title", "")))
        sources.append(csv_safe(m.get("source", "")))
        links.append(csv_safe(m.get("link", "")))
        price = m.get("price", "")
        currency = m.get("currency", "")
        price_str = f"{currency} {price}".strip() if (currency or price) else ""
        prices.append(csv_safe(price_str))
    return {
        "top_match_titles": " | ".join(titles),
        "top_match_sources": " | ".join(sources),
        "top_match_links": " | ".join(links),
        "top_match_prices": " | ".join(prices),
        "top_match_count": str(len(top_matches or [])),
    }

# =========================
# Google Sheets export (REST: avoids googleapiclient discovery issues)
# =========================
def _google_creds():
    # Read TOML table and normalize to plain strings
    if "google_service_account" not in st.secrets:
        raise RuntimeError("Missing [google_service_account] in Streamlit secrets.")

    sa_raw = st.secrets["google_service_account"]
    sa_info = {k: ("" if sa_raw[k] is None else str(sa_raw[k])) for k in sa_raw.keys()}

    # Normalize private key newlines (handles both PEM multiline and \\n-escaped)
    pk = sa_info.get("private_key", "")
    if "\\n" in pk:
        pk = pk.replace("\\n", "\n")
    sa_info["private_key"] = pk

    creds = service_account.Credentials.from_service_account_info(
        sa_info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    return creds

def append_row_to_sheet(*, sheet_id: str, tab_name: str, row_values: list):
    creds = _google_creds()
    creds.refresh(Request())
    token = creds.token

    safe_values = ["" if v is None else str(v) for v in row_values]

    url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{tab_name}!A:Z:append"
    params = {"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"}
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {"values": [safe_values]}

    resp = requests.post(url, params=params, headers=headers, json=body, timeout=60)
    resp.raise_for_status()
    return resp.json()

def export_to_google_sheets(results: dict):
    sheet_id = _get_secret("GOOGLE_SHEET_ID")

    trace = results.get("traceability", {})
    s3 = trace.get("s3", {})
    img_url = s3.get("presigned_url", "")
    thumb_formula = f'=IMAGE("{img_url}")' if img_url else ""

    matches = trace.get("search_summary", {}).get("top_matches", [])

    # MVP heuristic split: "auction" in source => Auction tab, else Retail tab.
    auction = [m for m in matches if "auction" in (m.get("source", "").lower())]
    retail = [m for m in matches if m not in auction]

    def summarize(items):
        titles = " | ".join([m.get("title", "") for m in items])
        links = " | ".join([m.get("link", "") for m in items])
        prices = " | ".join([f"{m.get('currency','')} {m.get('price','')}".strip() for m in items])
        return titles, links, prices

    ts = results.get("timestamp", datetime.utcnow().isoformat())
    a_titles, a_links, a_prices = summarize(auction)
    r_titles, r_links, r_prices = summarize(retail)

    append_row_to_sheet(sheet_id=sheet_id, tab_name="Auction",
                        row_values=[ts, thumb_formula, img_url, a_titles, a_links, a_prices])
    append_row_to_sheet(sheet_id=sheet_id, tab_name="Retail",
                        row_values=[ts, thumb_formula, img_url, r_titles, r_links, r_prices])

# =========================
# Session State Init
# =========================
if "image_uploaded" not in st.session_state:
    st.session_state.image_uploaded = False
if "uploaded_image_bytes" not in st.session_state:
    st.session_state.uploaded_image_bytes = None
if "uploaded_image_meta" not in st.session_state:
    st.session_state.uploaded_image_meta = None
if "results" not in st.session_state:
    st.session_state.results = None

# =========================
# 1) Upload
# =========================
st.header("1. Upload Item Image")

uploaded_file = st.file_uploader(
    "Upload a clear photo of the item",
    type=["jpg", "jpeg", "png"],
    key="item_image_uploader",
)

if uploaded_file is not None:
    st.session_state.image_uploaded = True
    st.session_state.uploaded_image_bytes = uploaded_file.getvalue()
    st.session_state.uploaded_image_meta = {
        "filename": uploaded_file.name,
        "content_type": uploaded_file.type,
        "size_bytes": len(st.session_state.uploaded_image_bytes),
    }
    st.image(uploaded_file, caption="Uploaded Image", use_container_width=True)

# =========================
# 2) Run Appraisal
# =========================
st.header("2. Run Appraisal")

run_disabled = not st.session_state.image_uploaded

if st.button("Run Appraisal", disabled=run_disabled, key="run_appraisal_btn"):
    try:
        s3_info = upload_bytes_to_s3_and_presign(
            file_bytes=st.session_state.uploaded_image_bytes,
            content_type=st.session_state.uploaded_image_meta.get("content_type", ""),
            original_filename=st.session_state.uploaded_image_meta.get("filename", "upload"),
        )

        lens = serpapi_google_lens_search(s3_info["presigned_url"])
        top_matches = extract_top_lens_matches(lens, limit=5)
        csv_summary = summarize_matches_for_csv(top_matches)

        st.session_state.results = {
            "status": "lens_ok",
            "message": "Image uploaded, presigned URL generated, and Google Lens results fetched.",
            "timestamp": datetime.utcnow().isoformat(),
            "traceability": {
                "image": st.session_state.uploaded_image_meta,
                "s3": s3_info,
                "search": {"provider": "serpapi", "engine": "google_lens", "raw": lens},
                "search_summary": {"top_matches": top_matches},
                "next": "Export to Google Sheets or proceed to OpenAI → pricing_engine",
            },
            "csv_summary": csv_summary,
        }
    except Exception as e:
        st.session_state.results = {
            "status": "error",
            "message": f"Appraisal failed: {type(e).__name__}: {e}",
            "timestamp": datetime.utcnow().isoformat(),
        }

from urllib.parse import urlparse

def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""

def _pill(label: str, value: str):
    st.markdown(
        f"""
        <div style="display:inline-block;padding:6px 10px;border:1px solid #2b2b2b;border-radius:10px;margin-right:8px;">
          <div style="font-size:12px;opacity:0.7">{label}</div>
          <div style="font-size:16px;font-weight:700">{value}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

def render_results_ui(results: dict):
    trace = results.get("traceability", {})
    img = trace.get("image", {})
    s3 = trace.get("s3", {})
    presigned_url = s3.get("presigned_url", "")

    matches = trace.get("search_summary", {}).get("top_matches", [])
    # Heuristic split (keep it simple for MVP)
    auction = [m for m in matches if "auction" in (m.get("source","").lower() + " " + _domain(m.get("link","")).lower())]
    retail = [m for m in matches if m not in auction]

    # Header row
    left, right = st.columns([3, 1])
    with left:
        st.subheader("Top Matches")
        st.caption(f"{img.get('filename','')} • Auction: {len(auction)} • Retail: {len(retail)} • Total: {len(matches)}")
    with right:
        # Export button (only if you already wired export_to_google_sheets)
        if "export_to_google_sheets" in globals():
            if st.button("Export to Google Sheet", key="export_btn_top"):
                try:
                    export_to_google_sheets(results)
                    st.success("Exported to Google Sheet (Auction + Retail).")
                except Exception as e:
                    st.error(f"Export failed: {type(e).__name__}: {e}")
        else:
            st.button("Export to Google Sheet", disabled=True, key="export_btn_top_disabled")

    st.divider()

    # Layout: thumbnail + results
    rail, main = st.columns([1, 3], vertical_alignment="top")

    with rail:
        if presigned_url:
            st.image(presigned_url, use_container_width=True)
        st.caption("Newel SKU / File")
        st.markdown(f"**{img.get('filename','—')}**")

        show_thumbs = st.toggle("Show result thumbnails", value=False, key="show_result_thumbs")

    with main:
        tab_a, tab_r = st.tabs([f"Auction ({len(auction)})", f"Retail ({len(retail)})"])

        def render_card(i: int, m: dict, kind: str):
            title = (m.get("title") or "").strip() or "(untitled)"
            link = m.get("link") or ""
            src = (m.get("source") or "").strip()
            dom = _domain(link)

            with st.container(border=True):
                cols = st.columns([1, 6]) if show_thumbs else [None, None]
                if show_thumbs:
                    with cols[0]:
                        thumb = m.get("thumbnail") or presigned_url
                        if thumb:
                            st.image(thumb, use_container_width=True)
                    body = cols[1]
                else:
                    body = st

                with body:
                    st.markdown(f"**{i}. {title}**")
                    if link:
                        st.markdown(link)
                    if src or dom:
                        st.caption(src or dom)

                    # Financial pills (MVP placeholders using available fields)
                    # SerpApi may give price sometimes; auction low/high/reserve comes later via extraction.
                    c1, c2, c3 = st.columns(3)
                    with c1:
                        if kind == "auction":
                            _pill("Auction Low", m.get("auction_low", "—"))
                        else:
                            _pill("Retail Price", (str(m.get("price")) if m.get("price") else "—"))
                    with c2:
                        if kind == "auction":
                            _pill("Auction High", m.get("auction_high", "—"))
                        else:
                            _pill("Source", src or dom or "—")
                    with c3:
                        if kind == "auction":
                            _pill("Reserve", m.get("auction_reserve", "—"))
                        else:
                            _pill("Link", "Open ↗" if link else "—")

        with tab_a:
            if not auction:
                st.info("No auction-style matches detected yet.")
            for idx, m in enumerate(auction, start=1):
                render_card(idx, m, "auction")

        with tab_r:
            if not retail:
                st.info("No retail-style matches detected yet.")
            for idx, m in enumerate(retail, start=1):
                render_card(idx, m, "retail")

# =========================
# 3) Results
# =========================
st.header("3. Results")

if st.session_state.results:
    st.subheader("JSON Output")
    st.json(st.session_state.results, expanded=False)

    with st.expander("Show raw SerpApi response (for provenance)"):
        st.json(st.session_state.results.get("traceability", {}).get("search", {}).get("raw", {}))

    # Export button (Google Sheets)
    st.subheader("Export")
    if st.button("Export to Google Sheet", key="export_google_sheet_btn"):
        try:
            export_to_google_sheets(st.session_state.results)
            st.success("Exported to Google Sheet (Auction + Retail tabs).")
        except Exception as e:
            st.error(f"Export failed: {type(e).__name__}: {e}")

    st.subheader("CSV Output (Single Row)")
    r = st.session_state.results
    presigned_url = r.get("traceability", {}).get("s3", {}).get("presigned_url", "")

    cs = r.get("csv_summary", {})
    csv_data = (
        "status,message,timestamp,presigned_url,top_match_count,top_match_titles,top_match_sources,top_match_links,top_match_prices\n"
        f"\"{csv_safe(r.get('status',''))}\","
        f"\"{csv_safe(r.get('message',''))}\","
        f"\"{csv_safe(r.get('timestamp',''))}\","
        f"\"{csv_safe(presigned_url)}\","
        f"\"{csv_safe(cs.get('top_match_count',''))}\","
        f"\"{csv_safe(cs.get('top_match_titles',''))}\","
        f"\"{csv_safe(cs.get('top_match_sources',''))}\","
        f"\"{csv_safe(cs.get('top_match_links',''))}\","
        f"\"{csv_safe(cs.get('top_match_prices',''))}\"\n"
    )

    st.download_button(
        label="Download CSV",
        data=csv_data,
        file_name="appraisal_result.csv",
        mime="text/csv",
        key="download_csv_btn",
    )
else:
    st.info("No appraisal run yet.")

st.divider()
st.caption("Traceability: image → S3 presigned URL → Google Lens → export (pricing stubbed)")
