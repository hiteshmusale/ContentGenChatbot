import pandas as pd
import requests
import streamlit as st

from ui.common import API, STATUS_LABEL, review_panel, workspace_sidebar

st.set_page_config(page_title="Assets", layout="wide")
st.title("Emails and logos")

ws = workspace_sidebar()
base = f"{API}/workspaces/{ws['id']}"


def post(path: str, **kw):
    r = requests.post(f"{base}{path}", timeout=120, **kw)
    if r.status_code >= 400:
        st.error(r.json().get("detail", r.text))
        return None
    return r.json()


left, right = st.columns(2)

with left:
    st.subheader("Emails")
    f = st.file_uploader("Upload an HTML email", type=["html", "htm"], key=f"html-{ws['id']}")
    if f and st.button("Upload email"):
        if post("/assets", files={"file": (f.name, f.getvalue(), "text/html")}):
            st.success("Email uploaded")
            st.rerun()
    if st.button("Create sample email from knowledge base"):
        if post("/assets/sample"):
            st.success("Sample email created")
            st.rerun()

with right:
    st.subheader("Logos")
    lf = st.file_uploader("Upload a logo", type=["png", "jpg", "jpeg", "gif", "svg"], key=f"logo-{ws['id']}")
    if lf and st.button("Upload logo"):
        if post("/logos", files={"file": (lf.name, lf.getvalue(), "application/octet-stream")}):
            st.success("Logo uploaded")
            st.rerun()
    logos = requests.get(f"{base}/logos").json()
    st.caption("Available: " + (", ".join(logos) if logos else "none yet"))

st.subheader("Saved emails")
assets = requests.get(f"{base}/assets").json()
if not assets:
    st.caption("No emails yet.")
SECTION_TYPES = ["preheader", "utility", "logo", "header", "subheader", "body", "claim", "cta", "isi", "footer", "image"]
NONE = "(none)"
TAG_HELP = {
    "tagged": "built-in tags (generated from our template)",
    "suggested": "auto-detected, not yet checked by a person",
    "confirmed": "checked by a person",
    "untagged": "not tagged yet",
}


def sections_tab(a: dict) -> None:
    data = requests.get(f"{API}/assets/{a['id']}/sections", timeout=30).json()
    st.markdown(f"**Tags:** {TAG_HELP.get(data['tag_status'], data['tag_status'])}")
    if data["sections"]:
        st.caption(
            "  \n".join(
                f"{s['type']} #{s['ordinal']} of {s['count']}: {s['text'][:70]}" for s in data["sections"]
            )
        )
    st.caption(
        "Fix any wrong tag, then save. The agent uses these to know what you mean by "
        "\"the 2nd subheader\", and claims are checked against the approved wording."
    )
    df = pd.DataFrame(
        [
            {"id": c["eid"], "element": c["tag"], "text": c["text"][:90], "type": c["type"] or NONE,
             "auto-detected": c["suggested"] or NONE}
            for c in data["candidates"]
        ]
    )
    edited = st.data_editor(
        df, hide_index=True, width="stretch", key=f"tags-{a['id']}",
        disabled=["id", "element", "text", "auto-detected"],
        column_config={"type": st.column_config.SelectboxColumn("type", options=[NONE, *SECTION_TYPES], required=True)},
    )
    c1, c2, _ = st.columns([1, 1, 4])
    if c1.button("Save tags", key=f"save-tags-{a['id']}", type="primary"):
        tags = [{"eid": int(r["id"]), "type": None if r["type"] == NONE else r["type"]} for _, r in edited.iterrows()]
        resp = requests.put(f"{API}/assets/{a['id']}/tags", json={"tags": tags}, timeout=60)
        st.error(resp.text) if resp.status_code != 200 else st.rerun()
    if c2.button("Re-run auto-detect", key=f"suggest-{a['id']}"):
        resp = requests.post(f"{API}/assets/{a['id']}/tags/suggest", timeout=60)
        st.error(resp.text) if resp.status_code != 200 else st.rerun()


for a in assets:
    parent = f" (edited from #{a['parent_asset_id']})" if a["parent_asset_id"] else ""
    with st.expander(
        f"#{a['id']} {a['name']}: {a['origin']}{parent} [{STATUS_LABEL.get(a['status'], a['status'])}] "
        f"[tags: {a['tag_status']}]"
    ):
        preview_tab, sections_tab_ = st.tabs(["Preview and review", "Sections"])
        with preview_tab:
            left, right = st.columns([3, 2])
            with left:
                if a["has_preview"]:
                    st.image(requests.get(f"{API}/assets/{a['id']}/preview").content, width=640)
                else:
                    st.caption("No preview available.")
            with right:
                review_panel(a["id"], f"asset-{a['id']}")
        with sections_tab_:
            sections_tab(a)
