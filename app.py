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
# Match extraction + CSV helpers
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
    st.session_state.image_uploaded = False
if "uploaded_image_bytes" not in st.session_state:
    st.session_state.uploaded_
