import pytest

from backend.ingestion import ParseError
from backend.ingestion.brand import parse_brand
from backend.ingestion.claims import parse_claims
from backend.ingestion.persona import parse_personas
from backend.ingestion.rules import parse_rules


def test_claims_sample(sample):
    claims = parse_claims(sample("claims.md"))
    assert [c["claim_id"] for c in claims] == ["CLM-001", "CLM-002", "CLM-003"]
    assert claims[0]["text"] == (
        "Cardiva lowered LDL-C by up to 52% in adults with primary hyperlipidemia."
    )
    assert claims[0]["reference"].startswith("Fictional Study A")


def test_claims_text_with_colon_is_kept_verbatim():
    md = "## CLM-1\nproduct: X\ntext: Note: this has a colon.\nreference: R\n"
    assert parse_claims(md)[0]["text"] == "Note: this has a colon."


def test_claims_duplicate_and_missing_reference():
    md = (
        "## CLM-001\nproduct: X\ntext: a\nreference: r\n\n"
        "## CLM-001\nproduct: X\ntext: b\n"
    )
    with pytest.raises(ParseError) as e:
        parse_claims(md)
    assert any("duplicate" in m for m in e.value.errors)
    assert any("missing 'reference'" in m for m in e.value.errors)


def test_claims_empty():
    with pytest.raises(ParseError):
        parse_claims("# Claims\n")


def test_brand_sample(sample):
    tokens = parse_brand(sample("brand.md"))
    got = {(t["category"], t["key"]): t["value"] for t in tokens}
    assert got[("brand", "name")] == "NovaHeart Pharma"
    assert got[("color", "primary")] == "#0B5CAD"
    assert got[("font", "heading")] == "Georgia, serif"
    assert got[("logo", "path")] == "logo.svg"
    assert sum(t["category"] == "tone" for t in tokens) == 3


def test_brand_errors():
    md = "# Brand: X\n## Colors\n- primary: blue\n## Fonts\n- heading: Arial\n"
    with pytest.raises(ParseError) as e:
        parse_brand(md)
    msgs = " | ".join(e.value.errors)
    assert "hex color" in msgs
    assert "'body' font" in msgs
    assert "Logo" in msgs


def test_personas_sample(sample):
    personas = parse_personas(sample("persona.md"))
    assert [p["name"] for p in personas] == ["HCP Cardiologist", "Primary Care Physician"]
    assert personas[0]["concerns"][0] == "Efficacy versus current standard of care"
    assert len(personas[1]["concerns"]) == 3
    assert personas[0]["language"] == "US English"


def test_persona_missing_fields():
    with pytest.raises(ParseError) as e:
        parse_personas("## Persona: A\ndescription: d\n")
    assert "A: missing 'tone'" in e.value.errors


def test_rules_sample(sample):
    rules = parse_rules(sample("compliance_rules.md"))
    by_type: dict[str, list] = {}
    for r in rules:
        by_type.setdefault(r["rule_type"], []).append(r)
    isi = next(r for r in by_type["required_block"] if r["name"] == "ISI")
    assert isi["value"].startswith("IMPORTANT SAFETY INFORMATION: Cardiva")
    assert [r["value"] for r in by_type["banned_phrase"]] == [
        "guaranteed", "miracle", "cure", "risk-free",
    ]
    assert len(by_type["mandatory_text"]) == 1


def test_rules_require_isi():
    with pytest.raises(ParseError) as e:
        parse_rules("## Banned Phrases\n- cure\n")
    assert any("ISI" in m for m in e.value.errors)
