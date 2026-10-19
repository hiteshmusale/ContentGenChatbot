import requests
import streamlit as st

from ui.common import API, review_panel, workspace_sidebar

st.set_page_config(page_title="Chat", layout="wide")
st.title("Chat")

ws = workspace_sidebar()


def call(method: str, path: str, **kw):
    r = requests.request(method, f"{API}{path}", timeout=180, **kw)
    if r.status_code >= 400:
        detail = r.json().get("detail", r.text)
        st.error(detail if isinstance(detail, str) else str(detail))
        return None
    return r.json()


sessions = call("GET", f"/workspaces/{ws['id']}/sessions") or []
with st.sidebar:
    st.header("Chats")
    if st.button("New chat", width="stretch"):
        s = call("POST", f"/workspaces/{ws['id']}/sessions")
        if s:
            st.session_state["sid"] = s["id"]
            st.rerun()
    ids = [s["id"] for s in sessions]
    if ids:
        current = st.session_state.get("sid")
        sid = st.radio(
            "History", ids, index=ids.index(current) if current in ids else 0,
            format_func=lambda i: next(s["title"] for s in sessions if s["id"] == i),
            label_visibility="collapsed",
        )
        st.session_state["sid"] = sid

sid = st.session_state.get("sid") if st.session_state.get("sid") in ids else None
if sid is None:
    st.info("Start a new chat from the sidebar.")
    st.stop()


def refresh(state):
    st.session_state["state"] = state
    st.session_state["state_sid"] = sid


if st.session_state.get("state_sid") != sid:
    refresh(call("GET", f"/sessions/{sid}"))
state = st.session_state["state"]
if state is None:
    st.stop()

for m in state["messages"]:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])

if state["awaiting_approval"]:
    st.info("A plan is waiting for your decision. Approve it, or type a change below.")
    a, b, _ = st.columns([1, 1, 6])
    if a.button("Approve", type="primary"):
        new = call("POST", f"/sessions/{sid}/plan", json={"decision": "approve"})
        if new:
            refresh(new)
            st.rerun()
    if b.button("Reject"):
        new = call("POST", f"/sessions/{sid}/plan", json={"decision": "reject"})
        if new:
            refresh(new)
            st.rerun()

if state["result"]:
    res = state["result"]
    with st.expander("Result", expanded=True):
        asset = res.get("asset")
        if res.get("error") or asset is None:
            st.error(res.get("error", "No result"))
        else:
            for line in asset.get("report", []):
                st.write(f"- {line}")
            if asset.get("has_preview"):
                st.image(requests.get(f"{API}/assets/{asset['id']}/preview").content, width=640)
            else:
                st.html(asset["html"])  # sanitized by Streamlit
            if asset.get("id"):
                st.divider()
                review_panel(asset["id"], f"chat-{asset['id']}")

if prompt := st.chat_input("Describe the email you need, or request a change to the plan"):
    with st.spinner("Thinking..."):
        new = call("POST", f"/sessions/{sid}/messages", json={"content": prompt})
    if new:
        refresh(new)
        st.rerun()
