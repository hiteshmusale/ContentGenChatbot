import pytest

from tests.test_api import upload, upload_all

BARE = "<html><body><table><tr><td>Hello</td></tr></table></body></html>"


@pytest.fixture
def ws(client, sample):
    wid = client.post("/workspaces", json={"name": "NovaHeart"}).json()["id"]
    upload_all(client, wid, sample)
    client.post(f"/workspaces/{wid}/logos", files={"file": ("logo.svg", sample("logo.svg").encode(), "image/svg+xml")})
    return wid


@pytest.fixture
def good(client, ws):
    return client.post(f"/workspaces/{ws}/assets/sample").json()["id"]


def review(client, aid, decision, comments="", reviewer="Dana"):
    return client.post(f"/assets/{aid}/review", json={"decision": decision, "comments": comments, "reviewer": reviewer})


def test_sample_is_validated_on_creation_and_pending(client, good):
    a = client.get(f"/assets/{good}").json()
    assert a["status"] == "pending_review"
    assert a["validations"][0]["passed"] is True and a["validations"][0]["findings"] == []


def test_export_locked_until_approved(client, good):
    assert client.get(f"/assets/{good}/export").status_code == 409
    assert review(client, good, "approved", "Looks good").status_code == 200
    r = client.get(f"/assets/{good}/export")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    assert "IMPORTANT SAFETY INFORMATION" in r.text


def test_approval_is_recorded_for_audit(client, good):
    review(client, good, "approved", "Approved for send", reviewer="Dana")
    a = client.get(f"/assets/{good}").json()
    assert a["status"] == "approved"
    assert [(r["decision"], r["reviewer"], r["comments"]) for r in a["reviews"]] == [("approved", "Dana", "Approved for send")]
    assert len(a["validations"]) == 2  # on creation + re-validation at approval


def test_noncompliant_email_cannot_be_approved_but_can_be_rejected(client, ws):
    aid = client.post(f"/workspaces/{ws}/assets", files={"file": ("bare.html", BARE.encode(), "text/html")}).json()["id"]
    a = client.get(f"/assets/{aid}").json()
    assert a["validations"][0]["passed"] is False

    r = review(client, aid, "approved")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert any(f["code"] == "REQUIRED_BLOCK_MISSING" for f in detail["findings"])
    assert client.get(f"/assets/{aid}").json()["status"] == "pending_review"
    assert client.get(f"/assets/{aid}/export").status_code == 409

    assert review(client, aid, "rejected", "No ISI").status_code == 200
    assert client.get(f"/assets/{aid}").json()["status"] == "rejected"
    assert client.get(f"/assets/{aid}/export").status_code == 409


def test_approval_revalidates_against_current_rules(client, ws, sample, good):
    # ISI text changes after the email was validated: the old email must not be approvable
    new_rules = sample("compliance_rules.md").replace(
        "contraindicated in patients with known hypersensitivity", "contraindicated in patients with severe liver disease"
    )
    assert upload(client, ws, "rules", new_rules, "compliance_rules.md").status_code == 201
    r = review(client, good, "approved")
    assert r.status_code == 409
    assert any(f["code"] == "REQUIRED_BLOCK_MISSING" for f in r.json()["detail"]["findings"]) or any(
        f["code"] == "REQUIRED_BLOCK_ALTERED" for f in r.json()["detail"]["findings"]
    )


def test_manual_validate_endpoint_records_a_new_result(client, good):
    r = client.post(f"/assets/{good}/validate")
    assert r.status_code == 200 and r.json()["passed"] is True
    assert len(client.get(f"/assets/{good}").json()["validations"]) == 2


def test_unknown_asset(client):
    assert client.get("/assets/999").status_code == 404
    assert review(client, 999, "approved").status_code == 404
    assert client.get("/assets/999/export").status_code == 404
