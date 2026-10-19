import pandas as pd
import requests
import streamlit as st

from ui.common import API, workspace_sidebar

DOC_SLOTS = [
    ("brand", "brand.md"),
    ("persona", "persona.md"),
    ("claims", "claims.md"),
    ("rules", "compliance_rules.md"),
]

st.set_page_config(page_title="Pharma Content Chatbot", layout="wide")
st.title("Knowledge base")

ws = workspace_sidebar()
base = f"{API}/workspaces/{ws['id']}"

st.subheader(f"Upload documents for {ws['name']}")
cols = st.columns(len(DOC_SLOTS))
for col, (doc_type, label) in zip(cols, DOC_SLOTS):
    with col:
        f = st.file_uploader(label, type=["md"], key=f"{ws['id']}-{doc_type}")
        if f and st.button(f"Upload {label}", key=f"btn-{ws['id']}-{doc_type}"):
            r = requests.post(
                f"{base}/documents",
                data={"doc_type": doc_type},
                files={"file": (f.name, f.getvalue(), "text/markdown")},
            )
            if r.status_code == 201:
                st.success(f"Saved as version {r.json()['version']}")
            else:
                detail = r.json()["detail"]
                st.error(detail["message"] if isinstance(detail, dict) else str(detail))
                for e in detail.get("errors", []) if isinstance(detail, dict) else []:
                    st.write(f"- {e}")

st.subheader("Documents and versions")
docs = requests.get(f"{base}/documents").json()
if docs:
    st.dataframe(
        pd.DataFrame(
            [
                {"type": d["doc_type"], "version": v["version"], "status": v["status"],
                 "file": v["filename"], "uploaded": v["created_at"]}
                for d in docs for v in d["versions"]
            ]
        ),
        hide_index=True, width="stretch",
    )
else:
    st.caption("Nothing uploaded yet.")

st.subheader("Parsed knowledge (active versions)")
tabs = st.tabs(["Brand", "Personas", "Claims", "Rules"])
for tab, path in zip(tabs, ["brand", "personas", "claims", "rules"]):
    with tab:
        rows = requests.get(f"{base}/{path}").json()
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        else:
            st.caption("No data yet.")
