import requests
import streamlit as st

from backend.config import settings

API = settings.api_url


def workspace_sidebar() -> dict:
    """Shared sidebar: create/select a workspace. Returns the selected one (or stops the page)."""
    try:
        workspaces = requests.get(f"{API}/workspaces", timeout=5).json()
    except requests.RequestException:
        st.error(f"Cannot reach the API at {API}. Start it with: uv run uvicorn backend.main:app")
        st.stop()

    with st.sidebar:
        st.header("Workspace")
        with st.form("new_ws", clear_on_submit=True):
            name = st.text_input("New workspace (one per brand)")
            if st.form_submit_button("Create") and name.strip():
                r = requests.post(f"{API}/workspaces", json={"name": name})
                st.error(r.json()["detail"]) if r.status_code != 201 else st.rerun()
        if not workspaces:
            st.info("Create a workspace to begin.")
            st.stop()
        ids = [w["id"] for w in workspaces]
        current = st.session_state.get("ws_id", ids[0])
        ws = st.selectbox(
            "Select", workspaces, index=ids.index(current) if current in ids else 0,
            format_func=lambda w: w["name"],
        )
        st.session_state["ws_id"] = ws["id"]
        st.text_input("Reviewer name", key="reviewer", value=st.session_state.get("reviewer", "reviewer"))
    return ws


STATUS_LABEL = {"pending_review": "pending review", "approved": "approved", "rejected": "rejected"}


def review_panel(asset_id: int, key: str) -> None:
    """Validation findings plus the reviewer's approve/reject controls for one email."""
    r = requests.get(f"{API}/assets/{asset_id}", timeout=30)
    if r.status_code != 200:
        st.error("Could not load this email's review state.")
        return
    a = r.json()
    val = a["validations"][0] if a["validations"] else None
    st.markdown(f"**Status:** {STATUS_LABEL.get(a['status'], a['status'])}")
    if val:
        errors = [f for f in val["findings"] if f["severity"] == "error"]
        warnings = [f for f in val["findings"] if f["severity"] == "warning"]
        if val["passed"]:
            st.success(f"Compliance validation passed ({len(warnings)} warning(s))")
        else:
            st.error(f"Compliance validation failed ({len(errors)} error(s))")
        for f in errors:
            st.error(f["message"], icon="🚫")
        for f in warnings:
            st.warning(f["message"])

    if a["status"] == "approved":
        exported = requests.get(f"{API}/assets/{asset_id}/export", timeout=30)
        if exported.status_code == 200:
            st.download_button(
                "Export approved HTML", exported.content, file_name=f"email-{asset_id}.html",
                mime="text/html", key=f"export-{key}",
            )
    else:
        comments = st.text_input("Review comments", key=f"comments-{key}")
        c1, c2, _ = st.columns([1, 1, 5])
        for col, decision, label in ((c1, "approved", "Approve"), (c2, "rejected", "Reject")):
            if col.button(label, key=f"{decision}-{key}", type="primary" if decision == "approved" else "secondary"):
                resp = requests.post(
                    f"{API}/assets/{asset_id}/review",
                    json={"decision": decision, "comments": comments, "reviewer": st.session_state.get("reviewer", "reviewer")},
                    timeout=60,
                )
                if resp.status_code == 409:
                    st.error(resp.json()["detail"]["message"])
                    for f in resp.json()["detail"]["findings"]:
                        st.write(f"- {f['message']}")
                else:
                    st.rerun()
    if a["reviews"]:
        with st.expander("Review history"):
            for rv in a["reviews"]:
                st.write(f"{rv['created_at'][:19]} · **{rv['decision']}** by {rv['reviewer']}: {rv['comments'] or '(no comment)'}")
