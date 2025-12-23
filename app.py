import streamlit as st
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

if uploaded_file is not None:
    st.session_state.image_uploaded = True
    st.image(
        uploaded_file,
        caption="Uploaded Image",
        use_container_width=True
    )

# -----------------------------
# Action Button
# -----------------------------
st.header("2. Run Appraisal")

run_disabled = not st.session_state.image_uploaded

if st.button("Run Appraisal", disabled=run_disabled):
    # Placeholder pipeline:
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

    st.subheader("CSV Output (Single Row)")
    csv_data = (
        "status,message,timestamp\n"
        f"{st.session_state.results['status']},"
        f"{st.session_state.results['message']},"
        f"{st.session_state.results['timestamp']}\n"
    )

    st.download_button(
        label="Download CSV",
        data=csv_data,
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
