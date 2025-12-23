import os
import uuid
from datetime import datetime

import boto3
import streamlit as st

st.set_page_config(page_title="Newel Appraiser MVP", layout="centered")

st.title("Newel Appraiser")
st.caption("Internal MVP • Image-based appraisal")


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


def upload_bytes_to_s3_and_presign(file_bytes: bytes, content_type: str, original_filename: str) -> dict:
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


if "image_uploaded" not in st.session_state:
    st.session_state.image_uploaded = False
if "uploaded_image_bytes" not in st.session_state:
    st.session_state.uploaded_image_bytes = None
if "uploaded_image_meta" not in st.session_state:
    st.session_state.uploaded_image_meta = None
if "results" not in st.session_state:
    st.session_state.results = None


st.header("1. Upload Item Image")

uploaded_file = st.file_uploader("Upload a clear photo of the item", type=["jpg", "jpeg", "png"])

if uploaded_file is not None:
    st.session_state.image_uploaded = True
    st.session_state.uploaded_image_bytes = uploaded_file.getvalue()
    st.session_state.uploaded_image_meta = {
        "filename": uploaded_file.name,
        "content_type": uploaded_file.type,
        "size_bytes": len(st.session_state.uploaded_image_bytes),
    }
    st.image(uploaded_file, caption="U_
