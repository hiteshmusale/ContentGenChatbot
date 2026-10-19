from bs4 import BeautifulSoup

from workflows.personalize import ops_from_rewrites, reorder_claims, summary
from workflows.render import fill_email_template
from workflows.sections import list_sections

C1 = "Cardiva lowered LDL-C by up to 52% in adults with primary hyperlipidemia."
C2 = "Cardiva is taken once daily, with or without food."
C3 = "Most common adverse reactions were headache and nausea."


def email(claims=(("CLM-001", C1), ("CLM-002", C2), ("CLM-003", C3))):
    return fill_email_template(
        brand_name="N", colors={}, fonts={}, logo_src=None, subject="s", preheader="p", headline="Hi",
        body=["One.", "Two."], claims=[{"claim_id": i, "text": t} for i, t in claims], cta_text="Go", cta_url="#",
        isi="IMPORTANT SAFETY INFORMATION: x.", footer_lines=["f"],
    )


def claim_texts(html):
    return [(e["data-claim-id"], e.get_text(strip=True)) for e in BeautifulSoup(html, "html.parser").select("[data-claim-id]")]


def test_only_headings_body_cta_and_preheader_are_rewritable():
    assert summary(email()) == {"preheader": 1, "header": 1, "body": 2, "cta": 1}  # no claim, isi, footer, logo


def test_ops_from_rewrites_become_section_text_replacements():
    ops = ops_from_rewrites([{"type": "body", "ordinal": 2, "text": "New."}])
    assert ops == [{"type": "replace_section_text", "target": {"type": "body", "ordinal": 2}, "text": "New."}]


def test_reorder_moves_chosen_claims_first_and_never_touches_their_text():
    html, order = reorder_claims(email(), ["CLM-003", "CLM-001"])
    assert order == ["CLM-003", "CLM-001", "CLM-002"]
    assert claim_texts(html) == [("CLM-003", C3), ("CLM-001", C1), ("CLM-002", C2)]  # same text, new order
    assert [s.text for s in list_sections(html) if s.type == "claim"] == [C3, C1, C2]


def test_reorder_is_a_noop_when_already_leading_or_when_nothing_matches():
    original = email()
    assert reorder_claims(original, ["CLM-001", "CLM-002"]) == (original, ["CLM-001", "CLM-002", "CLM-003"])
    assert reorder_claims(original, ["CLM-999"])[0] == original
    assert reorder_claims(original, [])[0] == original


def test_reorder_skips_claims_spread_across_different_containers():
    split = (
        '<html><body><ul><li data-slot="claim" data-claim-id="CLM-001">a</li></ul>'
        '<ul><li data-slot="claim" data-claim-id="CLM-002">b</li></ul></body></html>'
    )
    assert reorder_claims(split, ["CLM-002"]) == (split, ["CLM-001", "CLM-002"])


def test_reorder_ignores_a_single_claim():
    one = email(claims=(("CLM-001", C1),))
    assert reorder_claims(one, ["CLM-001"])[0] == one
