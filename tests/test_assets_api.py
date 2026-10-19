import pytest
from bs4 import BeautifulSoup

from tests.test_api import upload_all

HTML = "<html><body><table><tr><td>Hello</td></tr></table></body></html>"


@pytest.fixture
def ws(client, sample):
    wid = client.post("/workspaces", json={"name": "NovaHeart"}).json()["id"]
    upload_all(client, wid, sample)
    return wid


def test_sample_email_is_built_from_knowledge_base(client, ws, sample):
    client.post(f"/workspaces/{ws}/logos", files={"file": ("logo.svg", sample("logo.svg").encode(), "image/svg+xml")})
    r = client.post(f"/workspaces/{ws}/assets/sample")
    assert r.status_code == 201 and r.json()["origin"] == "sample"
    html = client.get(f"/assets/{r.json()['id']}/html").text
    soup = BeautifulSoup(html, "html.parser")
    claims = [li.get_text(strip=True) for li in soup.select("[data-claim-id]")]
    assert claims[0] == "Cardiva lowered LDL-C by up to 52% in adults with primary hyperlipidemia."
    assert len(claims) == 3
    assert "IMPORTANT SAFETY INFORMATION" in soup.select_one('[data-slot="isi"]').get_text()
    assert soup.select_one('img[data-slot="logo"]')["src"].startswith("data:image/svg+xml")
    assert "#0B5CAD" in html  # brand color applied


def test_sample_needs_knowledge_base(client):
    wid = client.post("/workspaces", json={"name": "Empty"}).json()["id"]
    r = client.post(f"/workspaces/{wid}/assets/sample")
    assert r.status_code == 422


def test_upload_html_and_listing(client, ws):
    r = client.post(f"/workspaces/{ws}/assets", files={"file": ("launch.html", HTML.encode(), "text/html")})
    assert r.status_code == 201 and r.json()["name"] == "launch" and r.json()["origin"] == "upload"
    assert [a["name"] for a in client.get(f"/workspaces/{ws}/assets").json()] == ["launch"]
    assert r.json()["tag_status"] == "suggested"
    got = client.get(f"/assets/{r.json()['id']}/html")
    assert got.headers["content-type"].startswith("text/plain")  # never served as HTML
    assert 'data-slot="body"' in got.text and "Hello" in got.text  # auto-tagged on upload


def test_upload_rejects_non_html_and_binary(client, ws):
    assert client.post(f"/workspaces/{ws}/assets", files={"file": ("x.html", b"just words", "text/html")}).status_code == 422
    assert client.post(f"/workspaces/{ws}/assets", files={"file": ("x.html", b"\xff\xfe\x00", "text/html")}).status_code == 422


def test_logo_upload_validation(client, ws):
    ok = client.post(f"/workspaces/{ws}/logos", files={"file": ("new logo.svg", b"<svg/>", "image/svg+xml")})
    assert ok.status_code == 201 and ok.json()["name"] == "new_logo.svg"
    assert client.get(f"/workspaces/{ws}/logos").json() == ["new_logo.svg"]
    assert client.post(f"/workspaces/{ws}/logos", files={"file": ("evil.html", b"<script>", "text/html")}).status_code == 422
    assert client.post(f"/workspaces/{ws}/logos", files={"file": ("../../etc.png", b"x", "image/png")}).json()["name"] == "etc.png"


def test_preview_404_when_disabled(client, ws):
    a = client.post(f"/workspaces/{ws}/assets", files={"file": ("a.html", HTML.encode(), "text/html")}).json()
    assert a["has_preview"] is False
    assert client.get(f"/assets/{a['id']}/preview").status_code == 404
    assert client.get("/assets/9999/html").status_code == 404


# ---- section tagging ------------------------------------------------------------------

def upload_messy(client, ws):
    from tests.test_sections import MESSY

    return client.post(f"/workspaces/{ws}/assets", files={"file": ("messy.html", MESSY.encode(), "text/html")}).json()["id"]


def test_upload_auto_tags_sections_and_keeps_the_original(client, ws, tmp_path):
    from tests.test_sections import EXPECTED, MESSY

    aid = upload_messy(client, ws)
    data = client.get(f"/assets/{aid}/sections").json()
    assert data["tag_status"] == "suggested"
    assert [c["type"] for c in data["candidates"]] == EXPECTED
    subs = [s for s in data["sections"] if s["type"] == "subheader"]
    assert [(s["ordinal"], s["count"], s["text"]) for s in subs][1] == (2, 3, "Dosing made simple")
    original = tmp_path / "workspaces" / str(ws) / "assets" / str(aid) / "email.original.html"
    assert original.read_text() == MESSY  # exactly what was uploaded


def test_correcting_tags_marks_confirmed_and_applies_everything_listed(client, ws):
    aid = upload_messy(client, ws)
    cands = client.get(f"/assets/{aid}/sections").json()["candidates"]
    # a person decides "Safety and tolerability" is a body paragraph, not a subheader; hero image untagged
    tags = [
        {"eid": c["eid"], "type": None if c["text"] == "image: Hero" else ("body" if c["text"] == "Safety and tolerability" else c["type"])}
        for c in cands
    ]
    r = client.put(f"/assets/{aid}/tags", json={"tags": tags})
    assert r.status_code == 200 and r.json()["tag_status"] == "confirmed"
    secs = r.json()["sections"]
    assert [s["text"] for s in secs if s["type"] == "subheader"] == ["Why Cardiva", "Dosing made simple"]
    assert not any(s["type"] == "image" for s in secs)
    assert client.get(f"/assets/{aid}").json()["tag_status"] == "confirmed"


def test_tagging_changes_how_claims_are_validated(client, ws):
    aid = upload_messy(client, ws)
    cands = client.get(f"/assets/{aid}/sections").json()["candidates"]
    # call an arbitrary sentence a claim: it matches no approved claim, so it must fail validation
    tags = [{"eid": c["eid"], "type": "claim" if c["text"] == "Body paragraph one." else c["type"]} for c in cands]
    client.put(f"/assets/{aid}/tags", json={"tags": tags})
    v = client.get(f"/assets/{aid}").json()["validations"][0]
    assert any(f["code"] == "CLAIM_UNKNOWN" and "Body paragraph one." in f["message"] for f in v["findings"])


def test_bad_tag_type_is_rejected_and_nothing_changes(client, ws):
    aid = upload_messy(client, ws)
    first = client.get(f"/assets/{aid}/sections").json()["candidates"][0]["eid"]
    assert client.put(f"/assets/{aid}/tags", json={"tags": [{"eid": first, "type": "banner"}]}).status_code == 422
    assert client.get(f"/assets/{aid}/sections").json()["tag_status"] == "suggested"


def test_suggest_retags_an_untagged_legacy_email(client, ws):
    aid = client.post(f"/workspaces/{ws}/assets", files={"file": ("x.html", b"<html><body><h1>Hi</h1><p>Some body text.</p></body></html>", "text/html")}).json()["id"]
    client.put(f"/assets/{aid}/tags", json={"tags": []})  # a person clears everything
    assert client.get(f"/assets/{aid}/sections").json()["sections"] == []
    r = client.post(f"/assets/{aid}/tags/suggest").json()
    assert r["tag_status"] == "suggested" and [s["type"] for s in r["sections"]] == ["header", "body"]


def test_export_removes_internal_tags(client, ws):
    aid = upload_messy(client, ws)
    assert 'data-slot="header"' in client.get(f"/assets/{aid}/html").text
    client.post(f"/assets/{aid}/review", json={"decision": "rejected"})
    sample_id = client.post(f"/workspaces/{ws}/assets/sample").json()["id"]
    assert client.post(f"/assets/{sample_id}/review", json={"decision": "approved", "reviewer": "Dana"}).status_code == 200
    out = client.get(f"/assets/{sample_id}/export").text
    assert "data-slot" not in out and "data-claim-id" not in out and "data-eid" not in out
    assert "Introducing Cardiva" in out and "IMPORTANT SAFETY INFORMATION" in out
    assert 'data-slot="claim"' in client.get(f"/assets/{sample_id}/html").text  # stored copy keeps its tags
