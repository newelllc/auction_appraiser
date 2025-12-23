import os
import uuid
from datetime import datetime

import boto3
import requests
import streamlit as st

# -----------------------------
# Page Config
# -----------------------------
st.set_page_config(page_title="Newel Appraiser MVP", layout="centered")
st.title("Newel Appraiser")
st.caption("Internal MVP • Image-based appraisal")

# -----------------------------
# Helpers: secrets, S3, SerpApi
# -----------------------------
def _get_secret(name: str, default: str | None = None) -> str:
    if name in st.secrets:
        return str(st.secrets[name])
    val = os.getenv(name, default)
    if val is None:
        raise RuntimeError(f"Missing required secret/env var: {name}")
    return str(val)


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
# Helpers: extract + CSV summary
# -----------------------------
def extract_top_lens_matches(lens_json: dict, limit: int = 5) -> list[dict]:
    matches: list[dict] = []

    # Common SerpApi Google Lens key
    for item in (lens_json or {}).get("visual_matches", [])[:limit]:
        price = item.get("price")
        if isinstance(price, dict):
            price_value = price.get("value", "")
            currency = price.get("currency", "")
        else:
            price_value = price or ""
            currency = ""

        matches.append(
            {
                "title": item.get("title") or "",
                "source": item.get("source") or item.get("domain") or "",
                "link": item.get("link") or "",
                "thumbnail": item.get("thumbnail") or "",
                "price": price_value,
                "currency": currency,
            }
        )

    # Fallback if response shape differs
    if not matches:
        for item in (lens_json or {}).get("shopping_results", [])[:limit]:
            matches.append(
                {
                    "title": item.get("title") or "",
                    "source": item.get("source") or item.get("seller") or "",
                    "link": item.get("link") or "",
                    "thumbnail": item.get("thumbnail") or "",
                    "price": item.get("price") or "",
                    "currency": item.get("currency") or "",
                }
            )

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


# -----------------------------
# Session State Init
# -----------------------------
if "image_uploaded" not in st.session_state:
    st.session_state.image_uploaded = False
if "uploaded_image_bytes" not in st.session_state:
    st.session_state.uploaded_image_bytes = None
if "uploaded_image_meta" not in st.session_state:
    st.session_state.uploaded_image_meta = None
if "results" not in st.session_state:
    st.session_state.results = None

# -----------------------------
# 1) Upload
# -----------------------------
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

# -----------------------------
# 2) Run Appraisal
# -----------------------------
st.header("2. Run Appraisal")

run_disabled = not st.session_state.image_uploaded

if st.button("Run Appraisal", disabled=run_disabled, key="run_appraisal_btn"):
    try:
        # Upload to S3
        s3_info = upload_bytes_to_s3_and_presign(
            file_bytes=st.session_state.uploaded_image_bytes,
            content_type=st.session_state.uploaded_image_meta.get("content_type", ""),
            original_filename=st.session_state.uploaded_image_meta.get("filename", "upload"),
        )

        # SerpApi Google Lens
        lens = serpapi_google_lens_search(s3_info["presigned_url"])
        top_matches = extract_top_lens_matches(lens, limit=5)
        csv_summary = summarize_matches_for_csv(top_matches)

        st.session_state.results = {
            "status": "lens_ok",
            "message": "Image uploaded, presigned URL generated, and Google Lens results fetched.",
            "timestamp": datetime.utcnow().isoformat(),
            "traceability": {
                "image": {
                    "filename": st.session_state.uploaded_image_meta.get("filename"),
                    "content_type": st.session_state.uploaded_image_meta.get("content_type"),
                    "size_bytes": st.session_state.uploaded_image_meta.get("size_bytes"),
                },
                "s3": s3_info,
                "search": {
                    "provider": "serpapi",
                    "engine": "google_lens",
                    "raw": lens,
                },
                "search_summary": {
                    "top_matches": top_matches,
                },
                "next": "Extract top matches → OpenAI → pricing_engine",
            },
            "csv_summary": csv_summary,
        }

    except Exception as e:
        st.session_state.results = {
            "status": "error",
            "message": f"Appraisal failed: {type(e).__name__}: {e}",
            "timestamp": datetime.utcnow().isoformat(),
        }

# -----------------------------
# 3) Results
# -----------------------------
st.header("3. Results")

if st.session_state.results:
    st.subheader("JSON Output")
    st.json(st.session_state.results, expanded=False)

    with st.expander("Show raw SerpApi response (for provenance)"):
        st.json(
            st.session_state.results.get("traceability", {}).get("search", {}).get("raw", {})
        )

    st.subheader("CSV Output (Single Row)")
    r = st.session_state.results

    presigned_url = r.get("traceability", {}).get("s3", {}).get("presigned_url", "")

    csv_summary = r.get("csv_summary", {})
    top_match_count = csv_summary.get("top_match_count", "")
    top_match_titles = csv_summary.get("top_match_titles", "")
    top_match_sources = csv_summary.get("top_match_sources", "")
    top_match_links = csv_summary.get("top_match_links", "")
    top_match_prices = csv_summary.get("top_match_prices", "")

    csv_data = (
        "status,message,timestamp,presigned_url,"
        "top_match_count,top_match_titles,top_match_sources,top_match_links,top_match_prices\n"
        f"\"{csv_safe(r.get('status',''))}\","
        f"\"{csv_safe(r.get('message',''))}\","
        f"\"{csv_safe(r.get('timestamp',''))}\","
        f"\"{csv_safe(presigned_url)}\","
        f"\"{csv_safe(top_match_count)}\","
        f"\"{csv_safe(top_match_titles)}\","
        f"\"{csv_safe(top_match_sources)}\","
        f"\"{csv_safe(top_match_links)}\","
        f"\"{csv_safe(top_match_prices)}\"\n"
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
st.caption("Traceability: image → S3 presigned URL → Google Lens → model → pricing (stubbed)")
