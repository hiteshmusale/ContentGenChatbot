import pytest

FILES = {
    "brand": "brand.md",
    "persona": "persona.md",
    "claims": "claims.md",
    "rules": "compliance_rules.md",
}


@pytest.fixture
def ws(client):
    r = client.post("/workspaces", json={"name": "NovaHeart"})
    assert r.status_code == 201
    return r.json()["id"]


def upload(client, ws, doc_type, content, filename=None):
    return client.post(
        f"/workspaces/{ws}/documents",
        data={"doc_type": doc_type},
        files={"file": (filename or FILES.get(doc_type, "x.md"), content.encode(), "text/markdown")},
    )


def upload_all(client, ws, sample):
    for t, f in FILES.items():
        assert upload(client, ws, t, sample(f)).status_code == 201


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_duplicate_workspace_name(client, ws):
    assert client.post("/workspaces", json={"name": "NovaHeart"}).status_code == 409


def test_upload_populates_structured_data(client, ws, sample):
    upload_all(client, ws, sample)
    assert len(client.get(f"/workspaces/{ws}/claims").json()) == 3
    assert len(client.get(f"/workspaces/{ws}/personas").json()) == 2
    brand = client.get(f"/workspaces/{ws}/brand").json()
    assert {"category": "color", "key": "primary", "value": "#0B5CAD"} in brand
    rules = client.get(f"/workspaces/{ws}/rules").json()
    assert {r["rule_type"] for r in rules} == {"required_block", "banned_phrase", "mandatory_text"}
    assert len(client.get(f"/workspaces/{ws}/documents").json()) == 4


def test_reupload_creates_version_2_and_keeps_history(client, ws, sample, tmp_path):
    upload_all(client, ws, sample)
    changed = sample("claims.md") + (
        "\n## CLM-004\nproduct: Cardiva\ntext: A new claim.\nreference: Ref\n"
    )
    r = upload(client, ws, "claims", changed)
    assert r.status_code == 201 and r.json()["version"] == 2

    claims = client.get(f"/workspaces/{ws}/claims").json()
    assert len(claims) == 4  # only the active version is served

    doc = next(d for d in client.get(f"/workspaces/{ws}/documents").json() if d["doc_type"] == "claims")
    assert [(v["version"], v["status"]) for v in doc["versions"]] == [(1, "superseded"), (2, "active")]
    assert (tmp_path / "workspaces" / str(ws) / "docs" / "claims" / "v1_claims.md").exists()
    assert (tmp_path / "workspaces" / str(ws) / "docs" / "claims" / "v2_claims.md").exists()


def test_malformed_upload_rejected_and_old_version_stays_active(client, ws, sample, tmp_path):
    upload_all(client, ws, sample)
    bad = "## CLM-001\nproduct: X\ntext: no reference here\n"
    r = upload(client, ws, "claims", bad)
    assert r.status_code == 422
    assert "CLM-001: missing 'reference'" in r.json()["detail"]["errors"]

    assert len(client.get(f"/workspaces/{ws}/claims").json()) == 3
    doc = next(d for d in client.get(f"/workspaces/{ws}/documents").json() if d["doc_type"] == "claims")
    assert [(v["version"], v["status"]) for v in doc["versions"]] == [(1, "active")]
    assert not (tmp_path / "workspaces" / str(ws) / "docs" / "claims" / "v2_claims.md").exists()


def test_first_upload_malformed_leaves_no_document(client, ws):
    assert upload(client, ws, "claims", "# Claims\n").status_code == 422
    assert client.get(f"/workspaces/{ws}/documents").json() == []


def test_unknown_workspace_and_doc_type(client, ws, sample):
    assert upload(client, 999, "claims", sample("claims.md")).status_code == 404
    assert upload(client, ws, "banner", "x").status_code == 422


def test_workspaces_are_isolated(client, ws, sample):
    other = client.post("/workspaces", json={"name": "Other"}).json()["id"]
    upload_all(client, ws, sample)
    assert client.get(f"/workspaces/{other}/claims").json() == []
