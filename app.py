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
