"""Memory: what the coach remembers about you. These entries live in the cached prompt prefix."""

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from api_client import api_call, fail, require_user  # noqa: E402

st.set_page_config(page_title="Memory", layout="wide")
user_id = require_user()

st.title("Coach memory")
st.caption(
    "Durable facts and preferences injected into every conversation (and cached for 12h). "
    "Entries marked 'user' were stated by you and are never overwritten by the assistant."
)

KINDS = ["preference", "fact", "constraint"]

response = api_call("GET", f"/users/{user_id}/memories")
if response.is_error:
    fail(response, "Load memories")
    st.stop()
memories = response.json()

for memory in memories:
    key = f"{memory['kind']}/{memory['key']}"
    cols = st.columns([1, 4, 1, 1])
    cols[0].markdown(f"`{key}`  \n_{memory['source']}_")
    new_value = cols[1].text_input("Value", value=memory["value"], key=f"val_{memory['id']}", label_visibility="collapsed")
    pinned = cols[2].toggle("Pinned", value=memory["pinned"], key=f"pin_{memory['id']}")
    if cols[3].button("Delete", key=f"del_{memory['id']}"):
        r = api_call("DELETE", f"/users/{user_id}/memories/{memory['kind']}/{memory['key']}")
        if r.is_error:
            fail(r, "Delete")
        st.rerun()
    if new_value != memory["value"] or pinned != memory["pinned"]:
        r = api_call("PUT", f"/users/{user_id}/memories", json={
            "kind": memory["kind"], "key": memory["key"], "value": new_value, "pinned": pinned,
        })
        if r.is_error:
            fail(r, "Save")
        else:
            st.toast(f"Updated {key}")
            st.rerun()

if not memories:
    st.info("Nothing stored yet. Tell the coach 'remember that ...' in chat, or add an entry below.")

st.divider()
st.subheader("Add a memory")
with st.form("add_memory", clear_on_submit=True):
    cols = st.columns([1, 1, 3])
    kind = cols[0].selectbox("Kind", KINDS)
    key = cols[1].text_input("Key (snake_case)", placeholder="bad_knee", key="new_memory_key")
    value = cols[2].text_input("Value", placeholder="No heavy barbell squats — knee pain", key="new_memory_value")
    pinned = st.checkbox("Pinned (never auto-evicted)")
    if st.form_submit_button("Save memory"):
        if not key.strip() or not value.strip():
            st.error("Key and value are required.")
        else:
            r = api_call("PUT", f"/users/{user_id}/memories", json={
                "kind": kind, "key": key.strip().lower(), "value": value.strip(), "pinned": pinned,
            })
            if r.is_error:
                fail(r, "Save")
            else:
                st.rerun()
