import os
import uuid
from datetime import datetime
from urllib.parse import urlparse

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
# Match extraction helpers
# =========================
def extract_top_lens_matches(lens_json: dict, limit: int = 5) -> list[dict]:
    matches: list[dict] = []
    visual_matches = (lens_json or {}).get("visual_matches", [])
    
    for item in visual_matches[:limit]:
        price = item.get("price")
        if isinstance(price, dict):
            price_value = price.get("extracted_value", price.get("value", ""))
            currency = price.get("currency", "")
        else:
            price_value = price or ""
            currency = ""

        matches.append({
            "title": item.get("title") or "Unknown Title",
            "source": item.get("source") or item.get("domain") or "Unknown Source",
            "link": item.get("link") or "",
            "thumbnail": item.get("thumbnail") or "",
            "price": price_value,
            "currency": currency,
        })

    if not matches:
        for item in (lens_json or {}).get("shopping_results", [])[:limit]:
            matches.append({
                "title": item.get("title") or "Unknown Title",
                "source": item.get("source") or item.get("seller") or "Unknown Seller",
                "link": item.get("link") or "",
                "thumbnail": item.get("thumbnail") or "",
                "price": item.get("price") or "",
                "currency": item.get("currency") or "",
            })
    return matches

def csv_safe(text: str) -> str:
    return (str(text) or "").replace('"', '""').replace("\n", " ").replace("\r", " ").strip()

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
# Google Sheets export
# =========================
def _google_creds():
    if "google_service_account" not in st.secrets:
        raise RuntimeError("Missing [google_service_account] in Streamlit secrets.")

    sa_raw = st.secrets["google_service_account"]
    sa_info = {k: ("" if sa_raw[k] is None else str(sa_raw[k])) for k in sa_raw.keys()}

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

    auction = [m for m in matches if "auction" in (m.get("source", "").lower())]
    retail = [m for m in matches if "auction" not in (m.get("source", "").lower())]

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
# UI Rendering Helper
# =========================
def render_results_ui(results: dict):
    if not results:
        return
        
    if results.get("status") == "error":
        st.error(results.get("message"))
        return

    st.success("Appraisal Complete!")
    
    matches = results.get("traceability", {}).get("search_summary", {}).get("top_matches", [])
    
    if not matches:
        st.warning("No visual matches found by Google Lens.")
        return

    st.subheader("Visual Matches Found")
    for idx, item in enumerate(matches):
        col1, col2 = st.columns([1, 3])
        with col1:
            if item.get("thumbnail"):
                st.image(item["thumbnail"])
        with col2:
            st.markdown(f"**{item.get('title')}**")
            st.write(f"Source: {item.get('source')}")
            price_str = f"{item.get('currency')} {item.get('price')}".strip()
            if price_str:
                st.write(f"Price: {price_str}")
            st.markdown(f"[View Link]({item.get('link')})")
        st.divider()

    if st.button("🚀 Export Results to Google Sheets"):
        with st.spinner("Exporting..."):
            try:
                export_to_google_sheets(results)
                st.toast("Successfully exported to Sheets!")
            except Exception as e:
                st.error(f"Export failed: {e}")

# =========================
# Session State Init
# =========================
if "image_uploaded" not in st.session_state:
    st.session_state["image_uploaded"] = False
if "uploaded_image_bytes" not in st.session_state:
    st.session_state["uploaded_image_bytes"] = None
if "uploaded_image_meta" not in st.session_state:
    st.session_state["uploaded_image_meta"] = None
if "results" not in st.session_state:
    st.session_state["results"] = None

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
    st.session_state["image_uploaded"] = True
    st.session_state["uploaded_image_bytes"] = uploaded_file.getvalue()
    st.session_state["uploaded_image_meta"] = {
        "filename": uploaded_file.name,
        "content_type": uploaded_file.type,
        "size_bytes": len(st.session_state["uploaded_image_bytes"]),
    }
    st.image(uploaded_file, caption="Uploaded Image", use_container_width=True)

# =========================
# 2) Run Appraisal
# =========================
st.header("2. Run Appraisal")

is_uploaded = st.session_state.get("image_uploaded", False)
run_disabled = not is_uploaded

if st.button("Run Appraisal", disabled=run_disabled, key="run_appraisal_btn"):
    with st.spinner("Processing image and searching..."):
        try:
            img_bytes = st.session_state.get("uploaded_image_bytes")
            img_meta = st.session_state.get("uploaded_image_meta", {})
            
            s3_info = upload_bytes_to_s3_and_presign(
                file_bytes=img_bytes,
                content_type=img_meta.get("content_type", ""),
                original_filename=img_meta.get("filename", "upload"),
            )

            lens = serpapi_google_lens_search(s3_info["presigned_url"])
            top_matches = extract_top_lens_matches(lens, limit=5)
            csv_summary = summarize_matches_for_csv(top_matches)

            st.session_state["results"] = {
                "status": "lens_ok",
                "message": "Image processed successfully.",
                "timestamp": datetime.utcnow().isoformat(),
                "traceability": {
                    "image": img_meta,
                    "s3": s3_info,
                    "search": {"provider": "serpapi", "engine": "google_lens", "raw": lens},
                    "search_summary": {"top_matches": top_matches},
                },
                "csv_summary": csv_summary,
            }
        except Exception as e:
            st.session_state["results"] = {
                "status": "error",
                "message": f"Appraisal failed: {type(e).__name__}: {e}",
                "timestamp": datetime.utcnow().isoformat(),
            }

# =========================
# 3) Results
# =========================
st.header("3. Results")

current_results = st.session_state.get("results")

if current_results:
    render_results_ui(current_results)

    with st.expander("JSON Output"):
        st.json(current_results, expanded=False)

    with st.expander("CSV Output (Single Row)"):
        r = current_results
        # FIXED: Corrected parenthesis on the line below
        presigned_url = r.get("traceability", {}).get("s3", {}).get("presigned_url", "")
        cs = r.get("csv_summary", {})
        
        csv_header = "status,message,timestamp,presigned_url,top_match_count,top_match_titles,top_match_sources,top_match_links,top_match_prices"
        csv_values = [
            r.get('status', ''),
            r.get('message', ''),
            r.get('timestamp', ''),
            presigned_url,
            cs.get('top_match_count', ''),
            cs.get('top_match_titles', ''),
            cs.get('top_match_sources', ''),
            cs.get('top_match_links', ''),
            cs.get('top_match_prices', '')
        ]
        csv_row = ",".join([f'"{csv_safe(v)}"' for v in csv_values])
        csv_data = f"{csv_header}\n{csv_row}\n"
        
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
st.caption("Traceability: image → S3 presigned URL → Google Lens → export")
