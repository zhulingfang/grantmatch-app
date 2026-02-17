import os
import streamlit as st

st.set_page_config(page_title="GrantMatch", layout="wide")
st.title("GrantMatch Draft Assistant")

st.write("✅ If you can see this, Streamlit Cloud deployment works.")

# Check for key
key_present = bool(os.environ.get("OPENAI_API_KEY"))
st.info(f"OPENAI_API_KEY configured: {key_present}")

st.text_area("Test input", "Paste anything here…", height=120)
