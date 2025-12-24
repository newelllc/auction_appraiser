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

# ==========================================
# 1. PAGE CONFIG
# ==========================================
st.set_page_config(page_title="Newel Appraiser MVP", layout="wide")


# ==========================================
# 2. BRAND STYLES (INJECT VIA components.html)
#    (Prevents CSS from appearing as text in Streamlit)
# ==========================================
import streamlit.components.v1 as components

def force_light_mode_shell():
    components.html(
        """
        <meta name="color-scheme" content="light">
        <style>
          :root { color-scheme: light !important; }
          html, body, .stApp { background: #FBF5EB !important; }

          /* Streamlit chrome/header/toolbars */
          [data-testid="stHeader"],
          [data-testid="stToolbar"],
          header {
            background: #FBF5EB !important;
          }

          /* Main containers */
          [data-testid="stAppViewContainer"],
          [data-testid="stMain"],
          section.main {
            background: #FBF5EB !important;
          }

          /* Sidebar */
          section[data-testid="stSidebar"]{
            background: #F6EFE4 !important;
          }

          /* Force readable text globally */
          .stApp, .stApp * { color: #1C1C1E !important; }
        </style>
        """,
        height=0,
        scrolling=False,
    )

force_light_mode_shell()



# ==========================================
# 3. CORE UTILITIES
# ==========================================
def _get_secret(name: str) -> str:
    if name in st.secrets:
        return str(st.secrets[name])
    val = os.getenv(name)
    if not val:
        raise RuntimeError(f"Missing required secret: {name}")
    return val


# ==========================================
# 4. SERVICE: GEMINI CLASSIFICATION
# ==========================================
def upgrade_comps_with_gemini(matches: list[dict]) -> list[dict]:
    genai.configure(api_key=_get_secret("GEMINI_API_KEY"))
    model = genai.GenerativeModel("gemini-2.0-flash")

    context = [{"title": m.get("title"), "source": m.get("source"), "link": m.get("link")} for m in matches]
    prompt = f"""
Appraisal Expert: Classify matches into "auction" or "retail".
Extract: kind (auction/retail), confidence (0.0-1.0), auction_low, auction_high, auction_reserve, retail_price.
Data: {json.dumps(context)}
Return ONLY a JSON object with a key "results" containing the ordered list of objects.
"""
    try:
        response = model.generate_content(
            prompt,
            generation_config={"response_mime_type": "application/json"},
        )
        ai_data = json.loads(response.text).get("results", [])
        for i, match in enumerate(matches):
            if i < len(ai_data):
                match.update(ai_data[i])
        return matches
    except Exception as e:
        st.error(f"Gemini AI Error: {e}")
        return matches


# ==========================================
# 5. SERVICE: GOOGLE SHEETS EXPORT (3 COMPS SCHEMA)
# ==========================================
def export_to_google_sheets(results: dict):
    sheet_id = _get_secret("GOOGLE_SHEET_ID")
    trace = results.get("traceability", {})
    matches = trace.get("search_summary", {}).get("top_matches", [])
    img_url = trace.get("s3", {}).get("presigned_url", "")
    ts = results.get("timestamp")

    auctions = [m for m in matches if m.get("kind") == "auction"][:3]
    retails = [m for m in matches if m.get("kind") == "retail"][:3]

    def build_row(items, is_auc):
        row = [ts, f'=IMAGE("{img_url}")', img_url]
        for i in range(3):
            if i < len(items):
                m = items[i]
                row.extend([m.get("title"), m.get("link")])
                if is_auc:
                    row.extend([m.get("auction_low"), m.get("auction_high"), m.get("auction_reserve")])
                else:
                    row.append(m.get("retail_price"))
            else:
                row.extend([""] * (5 if is_auc else 3))
        return row

    sa_info = st.secrets["google_service_account"]
    creds = service_account.Credentials.from_service_account_info(
        sa_info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    creds.refresh(Request())

    for tab, items, is_auc in [("Auction", auctions, True), ("Retail", retails, False)]:
        requests.post(
            f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{tab}!A:Z:append",
            params={"valueInputOption": "USER_ENTERED"},
            headers={"Authorization": f"Bearer {creds.token}"},
            json={"values": [build_row(items, is_auc)]},
            timeout=30,
        )


# ==========================================
# 6. UI
# ==========================================
with st.sidebar:
    # Large NEWEL logo (text), no "EST 1939"
    st.markdown("<div class='newel-logo-text'>NEWEL</div>", unsafe_allow_html=True)
    st.divider()
    sku = st.session_state.get("uploaded_image_meta", {}).get("filename", "N/A")
    st.markdown(f"**SKU Label:** `{sku}`")


st.markdown("<h1>Newel Appraiser</h1>", unsafe_allow_html=True)

# 1. Upload Section
st.header("1. Upload Item Image")
uploaded_file = st.file_uploader("Upload item photo", type=["jpg", "jpeg", "png"])

if uploaded_file:
    st.session_state["uploaded_image_bytes"] = uploaded_file.getvalue()
    st.session_state["uploaded_image_meta"] = {"filename": uploaded_file.name, "content_type": uploaded_file.type}
    st.image(uploaded_file, width=420)

# 2. Run Appraisal Section
st.header("2. Run Appraisal")
if st.button("Run Appraisal", disabled=not uploaded_file):
    with st.spinner("Processing..."):
        s3 = boto3.client(
            "s3",
            region_name=_get_secret("AWS_REGION"),
            aws_access_key_id=_get_secret("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=_get_secret("AWS_SECRET_ACCESS_KEY"),
        )
        key = f"uploads/{uuid.uuid4().hex}_{uploaded_file.name}"
        s3.put_object(
            Bucket=_get_secret("S3_BUCKET"),
            Key=key,
            Body=st.session_state["uploaded_image_bytes"],
            ContentType=st.session_state["uploaded_image_meta"]["content_type"],
        )
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": _get_secret("S3_BUCKET"), "Key": key},
            ExpiresIn=3600,
        )

        lens = requests.get(
            "https://serpapi.com/search.json",
            params={"engine": "google_lens", "url": url, "api_key": _get_secret("SERPAPI_API_KEY")},
            timeout=60,
        ).json()

        raw_matches = [
            {
                "title": i.get("title"),
                "source": i.get("source"),
                "link": i.get("link"),
                "thumbnail": i.get("thumbnail"),
            }
            for i in lens.get("visual_matches", [])[:15]
        ]

        st.session_state["results"] = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "traceability": {
                "s3": {"presigned_url": url},
                "search_summary": {"top_matches": upgrade_comps_with_gemini(raw_matches)},
            },
        }

# 3. Results & Export Section
st.header("3. Results")
res = st.session_state.get("results")
if res:
    matches = res["traceability"]["search_summary"]["top_matches"]
    t_auc, t_ret, t_misc = st.tabs(["Auction Results", "Retail Listings", "Other Matches"])

    def render_cards(subset: list[dict], show_prices: bool):
        if not subset:
            st.info("No matches found.")
            return

        for m in subset:
            thumb = m.get("thumbnail", "")
            title = m.get("title", "Untitled")
            source = m.get("source", "Unknown")
            link = m.get("link", "")

            auc_pill = ""
            if show_prices and (m.get("auction_low") is not None or m.get("auction_high") is not None):
                auc_pill = (
                    "<div class='pill'>"
                    f"Low: {m.get('auction_low')} &nbsp; | &nbsp; High: {m.get('auction_high')}"
                    "</div>"
                )

            ret_pill = ""
            if show_prices and (m.get("retail_price") is not None):
                ret_pill = f"<div class='pill'>Retail Price: {m.get('retail_price')}</div>"

            conf = m.get("confidence")
            conf_pill = f"<div class='pill'>Confidence: {conf}</div>" if conf is not None and not show_prices else ""

            st.markdown(
                f"""
<div class="result-card">
  <div style="display:flex; gap:16px; align-items:flex-start;">
    <div style="width:110px; flex:0 0 110px;">
      <img src="{thumb}" width="110" style="object-fit:contain; border:1px solid #CFC7BC; border-radius:12px; background:#FFF;" />
    </div>
    <div style="flex:1;">
      <div class="result-title">{title}</div>
      <div class="result-meta">Source: {source}</div>
      {auc_pill}
      {ret_pill}
      {conf_pill}
      <div style="margin-top:10px;">
        <a href="{link}" target="_blank">VIEW LISTING</a>
      </div>
    </div>
  </div>
</div>
                """,
                unsafe_allow_html=True,
            )

    with t_auc:
        render_cards([m for m in matches if m.get("kind") == "auction"], show_prices=True)

    with t_ret:
        render_cards([m for m in matches if m.get("kind") == "retail"], show_prices=True)

    with t_misc:
        render_cards([m for m in matches if m.get("kind") not in ("auction", "retail")], show_prices=False)

    if st.button("Export to Google Sheets"):
        with st.spinner("Exporting rows..."):
            try:
                export_to_google_sheets(res)
                st.toast("Successfully exported to Sheets!")
                st.markdown(f"[View Master Sheet]({_get_secret('GOOGLE_SHEET_URL')})")
            except Exception as e:
                st.error(f"Export failed: {e}")
else:
    st.info("No appraisal run yet.")
