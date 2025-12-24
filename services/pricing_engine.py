import streamlit as st
from services.pricing_engine import calculate_estimates

# Apply Newel Brand Styling
st.set_page_config(page_title="Newel Appraiser MVP", layout="wide")
st.markdown(f"""
    <style>
    .main {{ background-color: #FBF5EB; }}
    h1 {{ color: #1C1C1E; font-family: 'Cormorant Garamond', serif; }}
    .stButton>button {{ background-color: #D6AD53; color: white; border-radius: 0px; }}
    </style>
    """, unsafe_content_code=True)

st.title("NEWEL APPRAISER")
st.subheader("Estate Intake & Appraisal Assistant")

# Sidebar for AI Toggles
with st.sidebar:
    st.image("https://raw.githubusercontent.com/your-username/your-repo/main/logo.png") # Placeholder
    gen_catalog = st.toggle("Generate Title/Description with AI", value=True)

# Main UI - Image Upload
uploaded_file = st.file_uploader("Upload Item Photo", type=['jpg', 'png', 'jpeg'])

if uploaded_file:
    st.image(uploaded_file, caption="Uploaded Preview", width=300)
    if st.button("Run Appraisal"):
        st.info("Searching for matches...")
        # We will connect the Search and OpenAI logic here next
