import os
import uuid
from datetime import datetime
import boto3
import streamlit as st
import requests

def serpapi_google_lens_search(image_url: str) -> dict:
    api_key = _get_secret("SERPAPI_API_KEY")
    params = {
        "engine": "google_lens",
        "url": image_url,
        "api_key": api_key,
    }
    resp = requests.get(
        "https://serpapi.com/search.json",
        params=params,
        timeout=60
    )
    resp.raise_for_status()
    return resp.json()


# -----------------------------
# Page Config
# -----------------------------
st.set_page_config(page_title="Newel Appraiser MVP", layout="centered")

st.title("Newel Appraiser")
st.caption("Internal MVP • Image-based appraisal")

# -----------------------------
# Helpers
# -----------------------------
def _get_secret(name: str, default: str | None = None) -> str:
    # Streamlit Cloud secrets live in st.secrets; fallback to env for local dev.
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


def upload_bytes_to_s3_and_presign(
    *,
    file_bytes: bytes,
    content_type: str,
    original_filename: str,
) -> dict:
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
# 1. Upload Item Image
# -----------------------------
st.header("1. Upload Item Image")

uploaded_file = st.file_uploader(
    "Upload a clear photo of the item",
    type=["jpg", "jpeg", "png"],
)

if uploaded_file is not None:
    st.session_state.image_uploaded = True
    st.session_state.uploaded_image_bytes = uploaded_file.getvalue()
    st.session_state.uploaded_image_meta = {
        "filename": uploaded_file.name,
        "content_type": uploaded_file.type,
        "size_bytes": len(st.session_state.uploaded_image_bytes),
    }

    st.image(
        uploaded_file,
        caption="Uploaded Image",
        use_container_width=True,
    )


# -----------------------------
# 2. Run Appraisal
# -----------------------------
def serpapi_google_lens_search(image_url: str) -> dict:
    api_key = _get_secret("SERPAPI_API_KEY")
    params = {"engine": "google_lens", "url": image_url, "api_key": api_key}
    resp = requests.get("https://serpapi.com/search.json", params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()

st.header("2. Run Appraisal")

run_disabled = not st.session_state.image_uploaded

import streamlit as st
import json
from datetime import datetime

# -----------------------------
# Page Config
# -----------------------------
st.set_page_config(
    page_title="Newel Appraiser MVP",
    layout="centered"
)

st.title("Newel Appraiser")
st.caption("Internal MVP • Image-based appraisal")

# -----------------------------
# Session State Init
# -----------------------------
if "image_uploaded" not in st.session_state:
    st.session_state.image_uploaded = False

if "results" not in st.session_state:
    st.session_state.results = None

# -----------------------------
# Image Upload Section
# -----------------------------
st.header("1. Upload Item Image")

uploaded_file = st.file_uploader(
    "Upload a clear photo of the item",
    type=["jpg", "jpeg", "png"]
)

if uploaded_file:
    st.session_state.image_uploaded = True
    st.image(uploaded_file, caption="Uploaded Image", use_container_width=True)

# -----------------------------
# Action Button
# -----------------------------
st.header("2. Run Appraisal")

run_disabled = not st.session_state.image_uploaded

if st.button("Run Appraisal", disabled=run_disabled):
    # Placeholder for pipeline
    # S3 upload → SerpApi Lens → OpenAI → pricing_engine
    st.session_state.results = {
        "status": "stub",
        "message": "Pipeline not yet connected",
        "timestamp": datetime.utcnow().isoformat()
    }

# -----------------------------
# Results Section
# -----------------------------
st.header("3. Results")

if st.session_state.results:
    st.subheader("JSON Output")
    st.json(st.session_state.results)

    # Single-row CSV placeholder
    st.subheader("CSV Output (Single Row)")
    st.download_button(
        label="Download CSV",
        data="status,message,timestamp\n"
             f"{st.session_state.results['status']},"
             f"{st.session_state.results['message']},"
             f"{st.session_state.results['timestamp']}\n",
        file_name="appraisal_result.csv",
        mime="text/csv"
    )
else:
    st.info("No appraisal run yet.")

# -----------------------------
# Footer / Traceability
# -----------------------------
st.divider()
st.caption("Traceability: image → search → model → pricing (stubbed)")


if st.button("Run Appraisal", disabled=run_disabled):
    try:
        # 1) Upload to S3 + presign
        s3_info = upload_bytes_to_s3_and_presign(
            file_bytes=st.session_state.uploaded_image_bytes,
            content_type=st.session_state.uploaded_image_meta.get("content_type", ""),
            original_filename=st.session_state.uploaded_image_meta.get("filename", "upload"),
        )

        # 2) SerpApi Google Lens (uses the presigned URL)
        lens = serpapi_google_lens_search(s3_info["presigned_url"])
        top_matches = extract_top_lens_matches(lens, limit=5)


        # 3) Store results
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

                },
                "s3": {
                    "bucket": s3_info["bucket"],
                    "key": s3_info["key"],
                    "presigned_url": s3_info["presigned_url"],
                    "ttl_seconds": s3_info["ttl_seconds"],
                },
                "search": {
                    "provider": "serpapi",
                    "engine": "google_lens",
                    "raw": lens
                },
                "next": "Extract top matches → OpenAI → pricing_engine",
            },
        "search_summary": {
                        "top_matches": top_matches
        }

    except Exception as e:
        st.session_state.results = {
            "status": "error",
            "message": f"Appraisal failed: {type(e).__name__}: {e}",
            "timestamp": datetime.utcnow().isoformat(),
        }



# -----------------------------
# 3. Results
# -----------------------------
st.header("3. Results")

if st.session_state.results:
    st.subheader("JSON Output")
    st.json(st.session_state.results, expanded=False)

    # Raw SerpApi response (provenance only)
    with st.expander("Show raw SerpApi response (for provenance)"):
        st.json(
            st.session_state.results
            .get("traceability", {})
            .get("search", {})
            .get("raw", {})
        )

    st.subheader("CSV Output (Single Row)")

    r = st.session_state.results

    presigned_url = (
        r.get("traceability", {})
         .get("s3", {})
         .get("presigned_url", "")
    )

    csv_summary = r.get("csv_summary", {})
    top_match_count = csv_summary.get("top_match_count", "")
    top_match_titles = csv_summary.get("top_match_titles", "")
    top_match_sources = csv_summary.get("top_match_sources", "")
    top_match_links = csv_summary.get("top_match_links", "")
    top_match_prices = csv_summary.get("top_match_prices", "")

    csv_data = (
        "status,message,timestamp,presigned_url,"
        "top_match_count,top_match_titles,top_match_sources,"
        "top_match_links,top_match_prices\n"
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
    )
else:
    st.info("No appraisal run yet.")



# -----------------------------
# Footer
# -----------------------------
st.divider()
st.caption("Traceability: image → S3 presigned URL → search → model → pricing (stubbed)")
