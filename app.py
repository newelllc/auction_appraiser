import os
import uuid
from datetime import datetime
import boto3
import requests
import streamlit as st

from google.oauth2 import service_account
from googleapiclient.discovery import build

# -----------------------------
# Page Config
# -----------------------------
st.set_page_config(page_title="Newel Appraiser MVP", layout="centered")
st.title("Newel Appraiser")
st.caption("Internal MVP • Image-based appraisal")

# -----------------------------
# Helpers: secrets
# -----------------------------
def _get_secret(name: str, default: str | None = None) -> str:
    if name in st.secrets:
        return str(st.secrets[name])
    val = os.getenv(name, default)
    if val is None:
        raise RuntimeError(f"Missing required secret/env var: {name}")
    return str(val)

# -----------------------------
# S3 helpers
# -----------------------------
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
    if "." in original_filename:
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
            "original_filename": original_filename[:200],
            "uploaded_utc": datetime.utcnow().isoformat(),
        },
    )

    url = client.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=ttl,
    )

    return {"bucket": bucket, "key": key, "presigned_url": url, "ttl_seconds": ttl}

# -----------------------------
# SerpApi helper
# -----------------------------
def serpapi_google_lens_search(image_url: str) -> dict:
    api_key = _get_secret("SERPAPI_API_KEY")
    params = {
        "engine": "google_lens",
        "url": image_url,
        "api_key": api_key,
    }
    resp = requests.get("https://serpapi.com/search.json", params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()

# -----------------------------
# Extract + CSV helpers
# -----------------------------
def extract_top_lens_matches(lens_json: dict, limit: int = 5) -> list[dict]:
    matches = []
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
        prices.append(csv_safe(f"{currency} {price}".strip()))
    return {
        "top_match_titles": " | ".join(titles),
        "top_match_sources": " | ".join(sources),
        "top_match_links": " | ".join(links),
        "top_match_prices": " | ".join(prices),
        "top_match_count": str(len(top_matches or [])),
    }

# -----------------------------
# Google Sheets helpers
# -----------------------------
GOOGLE_SHEET_ID = "1E5Sq2M1vcC-A70aCUSfY8FFXUdooUw6LGRptmqUrwSM"

def sheets_client():
    # Pull from TOML table and normalize types to plain strings
    sa_raw = st.secrets["google_service_account"]
    sa_info = {k: ("" if sa_raw[k] is None else str(sa_raw[k])) for k in sa_raw.keys()}

    # Normalize private key newlines (handles either PEM multiline or \n-escaped)
    pk = sa_info.get("private_key", "")
    if "\\n" in pk:
        pk = pk.replace("\\n", "\n")
    sa_info["private_key"] = pk

    creds = service_account.Credentials.from_service_account_info(
        sa_info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )

    # cache_discovery=False avoids file/seek issues in hosted environments (Streamlit Cloud)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)

def append_row(tab_name: str, row_values: list):
    svc = sheets_client()

    # Ensure no None values get sent to Sheets
    safe_values = ["" if v is None else str(v) for v in row_values]

    svc.spreadsheets().values().append(
        spreadsheetId=GOOGLE_SHEET_ID,
        range=f"{tab_name}!A:Z",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": [safe_values]},
    ).execute()


def export_to_google_sheets(results: dict):
    trace = results.get("traceability", {})
    s3 = trace.get("s3", {})
    img_url = s3.get("presigned_url", "")
    thumb_formula = f'=IMAGE("{img_url}")' if img_url else ""

    matches = trace.get("search_summary", {}).get("top_matches", [])

    # MVP heuristic split
    auction = [m for m in matches if "auction" in (m.get("source","").lower())]
    retail = [m for m in matches if m not in auction]

    def summarize(items):
        titles = " | ".join([m.get("title","") for m in items])
        links = " | ".join([m.get("link","") for m in items])
        prices = " | ".join([f"{m.get('currency','')} {m.get('price','')}".strip() for m in items])
        return titles, links, prices

    ts = results.get("timestamp", datetime.utcnow().isoformat())

    a_titles, a_links, a_prices = summarize(auction)
    r_titles, r_links, r_prices = summarize(retail)

    append_row("Auction", [ts, thumb_formula, img_url, a_titles, a_links, a_prices])
    append_row("Retail", [ts, thumb_formula, img_url, r_titles, r_links, r_prices])

# -----------------------------
# Session state
# -----------------------------
for k in ["image_uploaded", "uploaded_image_bytes", "uploaded_image_meta", "results"]:
    st.session_state.setdefault(k, None)

# -----------------------------
# 1. Upload
# -----------------------------
st.header("1. Upload Item Image")

uploaded_file = st.file_uploader(
    "Upload a clear photo of the item",
    type=["jpg", "jpeg", "png"],
    key="item_image_uploader",
)

if uploaded_file:
    st.session_state.image_uploaded = True
    st.session_state.uploaded_image_bytes = uploaded_file.getvalue()
    st.session_state.uploaded_image_meta = {
        "filename": uploaded_file.name,
        "content_type": uploaded_file.type,
        "size_bytes": len(st.session_state.uploaded_image_bytes),
    }
    st.image(uploaded_file, caption="Uploaded Image", use_container_width=True)

# -----------------------------
# 2. Run Appraisal
# -----------------------------
st.header("2. Run Appraisal")

if st.button("Run Appraisal", disabled=not st.session_state.image_uploaded):
    try:
        s3_info = upload_bytes_to_s3_and_presign(
            file_bytes=st.session_state.uploaded_image_bytes,
            content_type=st.session_state.uploaded_image_meta["content_type"],
            original_filename=st.session_state.uploaded_image_meta["filename"],
        )
        lens = serpapi_google_lens_search(s3_info["presigned_url"])
        top_matches = extract_top_lens_matches(lens, 5)
        csv_summary = summarize_matches_for_csv(top_matches)

        st.session_state.results = {
            "status": "lens_ok",
            "message": "Lens search completed and results aggregated.",
            "timestamp": datetime.utcnow().isoformat(),
            "traceability": {
                "image": st.session_state.uploaded_image_meta,
                "s3": s3_info,
                "search_summary": {"top_matches": top_matches},
            },
            "csv_summary": csv_summary,
        }
    except Exception as e:
        st.session_state.results = {
            "status": "error",
            "message": str(e),
            "timestamp": datetime.utcnow().isoformat(),
        }

# -----------------------------
# 3. Results
# -----------------------------
st.header("3. Results")

if st.session_state.results:
    st.json(st.session_state.results, expanded=False)

    if st.button("Export to Google Sheet"):
        try:
            export_to_google_sheets(st.session_state.results)
            st.success("Exported to Google Sheet (Auction + Retail tabs).")
        except Exception as e:
            st.error(f"Export failed: {e}")
else:
    st.info("No appraisal run yet.")

st.divider()
st.caption("Traceability: image → S3 → Google Lens → aggregation → export")
