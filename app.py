"""
Streamlit entrypoint -- this is the hosted prototype.

Run locally:   streamlit run app.py
Deploy:        push to GitHub, deploy on Streamlit Community Cloud, set the
               secrets below in the app's Settings > Secrets panel.

Required secrets / env vars:
  ANTHROPIC_API_KEY
  MONDAY_API_TOKEN
  MONDAY_DEALS_BOARD_ID
  MONDAY_WORK_ORDERS_BOARD_ID
"""

import os

import streamlit as st

from monday_agent.agent import SkylarkAgent
from monday_agent.monday_client import MondayAPIError

st.set_page_config(page_title="Skylark BI Agent", page_icon="📊", layout="centered")

# Streamlit Cloud secrets -> env vars (so the rest of the code just reads os.environ)
# Streamlit Cloud secrets -> env vars (so the rest of the code just reads os.environ).
# Locally there's no secrets.toml at all (we use .env instead), and st.secrets
# raises rather than returning empty in that case -- so just skip it.
try:
    for key in (
        "GROQ_API_KEY",
        "MONDAY_API_TOKEN",
        "MONDAY_DEALS_BOARD_ID",
        "MONDAY_WORK_ORDERS_BOARD_ID",
    ):
        if key in st.secrets and key not in os.environ:
            os.environ[key] = st.secrets[key]
except Exception:
    pass

st.title("📊 Skylark Drones — BI Agent")
st.caption("Ask about pipeline, deals, work orders, or request a leadership update.")

missing = [
    k for k in ("GROQ_API_KEY", "MONDAY_API_TOKEN", "MONDAY_DEALS_BOARD_ID", "MONDAY_WORK_ORDERS_BOARD_ID")
    if not os.environ.get(k)
]
if missing:
    st.error(f"Missing required configuration: {', '.join(missing)}. See README.md.")
    st.stop()

if "agent" not in st.session_state:
    st.session_state.agent = SkylarkAgent()
if "history" not in st.session_state:
    st.session_state.history = []

for turn in st.session_state.history:
    with st.chat_message(turn["role"]):
        st.markdown(turn["content"])

with st.sidebar:
    st.subheader("Try asking")
    st.markdown(
        "- How's our pipeline looking for the energy sector this quarter?\n"
        "- Which work orders are overdue?\n"
        "- Did our won deals this year actually get delivered?\n"
        "- Prepare a leadership update on pipeline and delivery."
    )
    if st.button("Reset conversation"):
        st.session_state.agent.reset()
        st.session_state.history = []
        st.rerun()

prompt = st.chat_input("Ask a business question...")
if prompt:
    st.session_state.history.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Pulling live data from monday.com..."):
            try:
                answer = st.session_state.agent.ask(prompt)
            except MondayAPIError as e:
                answer = f"⚠️ Couldn't reach monday.com: {e}"
            except Exception as e:  # noqa: BLE001
                answer = f"⚠️ Something went wrong: {e}"
        st.markdown(answer)

    st.session_state.history.append({"role": "assistant", "content": answer})
