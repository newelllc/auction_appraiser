import os
import uuid
import json
import boto3
import requests
import streamlit as st
import google.generativeai as genai
from datetime import datetime
from google.oauth2 import service_account
from google.auth.transport.requests import Request
import streamlit.components.v1 as components

# ======================================================
# PAGE CONFIG (must be first Streamlit call)
# ======================================================
st.set_page_config(
    page_title="Newel Appraiser",
    layout="wide"
)

# ======================================================
# HARD BRAND STYLES (STABLE + READABLE)
# ======================================================
components.html(
    """
    <link href="https://fonts.googleapis.com/css2?family=EB+Garamond:wght@400;600;700&display=swap" rel="stylesheet">
    <style>
        html, body, .stApp {
            background-color: #FBF5EB !important;
            color: #1C1C1E !important;
            font-family: 'EB Garamond', serif !important;
        }

        /* Sidebar */
        section[data-testid="stSidebar"] {
            background-color: #F6EFE4 !important;
            border-right: 1px solid #CFC7BC !important;
        }

        /* Headings */
        h1, h2, h3 {
            color: #8B0000 !important;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            font-weight: 700 !important;
        }

        h1 { font-size: 2.4rem !important; }
        h2 { font-size: 1.6rem !important; }

        /* NEWEL logo */
        .newel-logo {
            font-size: 3.4rem;
            font-weight: 700;
            letter-spacing: 0.18em;
            color: #8B0000;
            margin-bottom: 1rem;
        }

        /* Buttons — ALL RED WITH WHITE TEXT */
        button, .stButton>button {
            background-color: #8B0000 !important;
            color: #FFFFFF !important;
            border: none !important;
            border-radius: 0px !important;
            font-weight: 700 !important;
            letter-spacing: 0.12em;
            text-transform: uppercase;
            padding: 0.9rem 1.2rem !important;
        }

        button:hover, .stButton>button:hover {
            background-color: #A30000 !important;
            color: #FFFFFF !important;
        }

        /* File uploader */
        [data-testid="stFileUploader"] section {
            background-color: #FFFFFF !important;
            border: 1px dashed #CFC7BC !important;
            border-radius: 12px;
        }

        /* Result cards */
        .result-card {
            background-color: #FFFFFF;
            border: 1px solid #CFC7BC;
            border-radius: 14px;
            padding: 1rem;
            margin-bottom: 1rem;
        }

        .pill {
            background-color: #EFDAAC;
            color: #1C1C1E;
            padding: 6px 10px;
            border-radius: 999px;
            font-weight: 700;
            display: inline-block;
            margin-top: 6px;
        }

        a {
            color: #8B0000 !important;
            font-weight: 700;
            text-decoration: none;
        }
        a:hover { text-decoration: underline; }
    </style>
    """,
    height=0
)

# ======================================================
# SECRETS
# ======================================================
def secret(name):
    if name in st.secrets:
        return st.secrets[name]
    if name in os.environ:
        return os.environ[name]
    raise RuntimeError(f"Missing secret: {name}")

# ======================================================
# SIDEBAR
# ======================================================
with st.sidebar:
    st.markdown("<div class='newel-logo'>NEWEL</div>", unsafe_allow_html=True)
    st.divider()
    sku = st.session_state.get("uploaded_image_meta", {}).get("filename", "N/A")
    st.markdown(f"**SKU Label:** `{sku}`")

# ======================================================
# MAIN UI
# ======================================================
st.markdown("<h1>Newel Appraiser</h1>", unsafe_allow_html=True)

# 1. Upload
st.header("1. Upload Item Image")
uploaded_file = st.file_uploader(
    "Upload item photo for appraisal",
    type=["jpg", "jpeg", "png"]
)

if uploaded_file:
    st.session_state["uploaded_image_bytes"] = uploaded_file.getvalue()
    st.session_state["uploaded_image_meta"] = {
        "filename": uploaded_file.name,
        "content_type": uploaded_file.type
    }
    st.image(uploaded_file, width=420)

# ======================================================
# 2. Run Appraisal
# ======================================================
st.header("2. Run Appraisal")

if st.button("Run Appraisal", disabled=not uploaded_file):
    with st.spinner("Running appraisal…"):
        s3 = boto3.client(
            "s3",
