import streamlit as st
import os

# 1. Load Custom Google Font (EB Garamond)
st.markdown(
    """
    <link href="https://fonts.googleapis.com/css2?family=EB+Garamond:wght@400;700&display=swap" rel="stylesheet">
    """,
    unsafe_allow_html=True
)

# 2. Inject Custom CSS for Brand Alignment
def inject_newel_styles():
    st.markdown(
        """
        <style>
        /* Base Styling: Font and App Background */
        html, body, [data-testid="stAppViewContainer"], [data-testid="stHeader"] {
            font-family: 'EB Garamond', serif !important;
            background-color: #FBF5EB !important; /* Neutral Light BG from Guide */
            color: #1C1C1E !important; /* Primary Text - Black */
        }

        /* Sidebar Styling */
        [data-testid="stSidebar"] {
            background-color: #F8F2E8 !important; /* Lighter Cream BG */
            border-right: 1px solid #C2C2C2;
        }

        /* Headings: Newel Red and Font Sizes */
        h1, h2, h3, .brand-header {
            font-family: 'EB Garamond', serif !important;
            color: #8B0000 !important; /* Newel Red */
            text-transform: uppercase;
            letter-spacing: 0.1em;
            margin-bottom: 0.5rem;
        }

        /* 1. UPLOAD ITEM IMAGE - Remove dark background */
        [data-testid="stFileUploader"] {
            background-color: transparent !important;
        }
        [data-testid="stFileUploader"] section {
            background-color: #FBF5EB !important; /* Match Page BG */
            border: 1px dashed #C2C2C2 !important; /* Grey border */
            border-radius: 4px;
            padding: 20px;
        }
        /* Readable Text in Uploader */
        [data-testid="stFileUploader"] p, [data-testid="stFileUploader"] span, [data-testid="stFileUploader"] label {
            color: #1C1C1E !important;
            font-size: 1rem !important;
        }

        /* 2. RUN APPRAISAL - Primary Button Styling (Inquire/Buy Now) */
        div.stButton > button {
            background-color: #1C1C1E !important; /* Black Default */
            color: #FBF5EB !important; /* Light Text */
            border-radius: 0px !important;
            border: none !important;
            padding: 0.75rem 2rem !important;
            font-family: 'EB Garamond', serif !important;
            font-weight: 700;
            text-transform: uppercase;
            width: 100%;
            transition: all 0.3s ease;
        }
        div.stButton > button:hover {
            background-color: #8B0000 !important; /* Hover to Newel Red */
            color: #FBF5EB !important;
        }

        /* 3. RESULTS - "Other Matches" and Tab Styling */
        .result-card {
            background-color: white !important;
            padding: 1.5rem;
            border: 1px solid #C2C2C2;
            margin-bottom: 1rem;
            border-radius: 2px;
            color: #1C1C1E !important;
        }

        /* General spacing and unreadable text fixes */
        .stMarkdown p, .stMarkdown span {
            color: #1C1C1E !important;
        }
        
        /* Specific Fix for the 'No results' info box */
        div[data-testid="stNotification"] {
            background-color: #EFDAAC !important; /* Mustard/Gold from Palette */
            color: #1C1C1E !important;
            border: 1px solid #BD9745;
        }
        </style>
        """,
        unsafe_allow_html=True
    )

inject_newel_styles()

# --- APP LAYOUT ---
with st.sidebar:
    st.markdown("<h2 class='brand-header'>NEWEL</h2>", unsafe_allow_html=True)
    if os.path.exists("logo.png"):
        st.image("logo.png", use_container_width=True)
    st.markdown("<p style='font-size: 0.8rem; opacity: 0.8;'>EST 1939</p>", unsafe_allow_html=True)
    st.divider()
    sku = st.session_state.get("uploaded_image_meta", {}).get("filename", "N/A")
    st.markdown(f"**SKU Label:** `{sku}`")

st.markdown("<h1 class='brand-header'>Newel Appraiser</h1>", unsafe_allow_html=True)

st.header("1. Upload Item Image")
uploaded_file = st.file_uploader("Upload item photo", type=["jpg", "png"])

if uploaded_file:
    st.session_state["uploaded_image_bytes"] = uploaded_file.getvalue()
    st.session_state["uploaded_image_meta"] = {"filename": uploaded_file.name, "content_type": uploaded_file.type}
    st.image(uploaded_file, width=400)

st.header("2. Run Appraisal")
# This button follows the 'INQUIRE/BUY NOW' styling from guide
if st.button("Run Appraisal", disabled=not uploaded_file):
    with st.spinner("AI searching..."):
        # logic placeholder
        pass

st.header("3. Results")
# Placeholder for matches rendering...
st.info("No results yet. Please upload and run appraisal.")
