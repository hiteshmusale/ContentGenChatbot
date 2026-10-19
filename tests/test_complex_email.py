"""The hard case: nested tables, columns, many CTAs/images/subheaders, a multi-part ISI."""
from collections import Counter
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from workflows.adapt import AdaptEnv, apply_ops
from workflows.sections import apply_tags, auto_tag, candidate_list, list_sections
from workflows.validator import KnowledgeBase, validate_email

COMPLEX = (Path(__file__).parent.parent / "samples" / "complex_email.html").read_text()
CLAIMS = {
    "CLM-001": "Cardiva lowered LDL-C by up to 52% in adults with primary hyperlipidemia.",
    "CLM-002": "Cardiva is taken once daily, with or without food.",
    "CLM-003": "Most common adverse reactions were headache and nausea.",
}
ISI = ("IMPORTANT SAFETY INFORMATION: Cardiva is contraindicated in patients with known hypersensitivity "
       "to any of its components. Please see full Prescribing Information.")

EXPECTED = (
    "preheader utility logo logo image header subheader body body cta image subheader claim claim cta "
    "subheader image subheader claim cta image subheader body cta image subheader body cta subheader claim "
    "subheader cta cta isi isi isi isi image image footer footer footer"
).split()


@pytest.fixture
def tagged():
    return auto_tag(COMPLEX, CLAIMS, "NovaHeart Pharma")


def env():
    return AdaptEnv(isi_text=ISI, load_logo=lambda n: "data:image/svg+xml;base64,AAAA" if n == "new.svg" else None)


def secs(html, type_):
    return [s.text for s in list_sections(html) if s.type == type_]


def test_every_element_is_typed_as_expected():
    assert [c.suggested for c in candidate_list(COMPLEX, CLAIMS, "NovaHeart Pharma")] == EXPECTED


def test_counts_per_type(tagged):
    counts = Counter(s.type for s in list_sections(tagged))
    assert counts == {"preheader": 1, "utility": 1, "logo": 2, "image": 7, "header": 1, "subheader": 8, "body": 4,
                      "claim": 4, "cta": 7, "isi": 4, "footer": 3}


def test_nested_cells_are_separate_sections(tagged):
    # h3 + claim paragraph + link live in one table cell; each is its own section
    assert "Simple dosing" in secs(tagged, "subheader")
    assert "Cardiva is taken once daily, with or without food." in secs(tagged, "claim")
    assert "Dosing guide" in secs(tagged, "cta")


def test_both_repeated_claims_get_their_approved_ids(tagged):
    soup = BeautifulSoup(tagged, "html.parser")
    ids = [e["data-claim-id"] for e in soup.select('[data-slot="claim"]')]
    assert ids == ["CLM-001", "CLM-002", "CLM-002", "CLM-003"]  # CLM-002 appears twice in the email


def test_ordinal_references_pick_the_right_elements(tagged):
    ctas = secs(tagged, "cta")
    assert ctas[0] == "Request a sample" and ctas[1] == "See the efficacy data" and ctas[-1] == "Talk to a representative"
    assert secs(tagged, "subheader")[3] == "Simple dosing"  # "the 4th subheader"
    assert secs(tagged, "logo")[1] == "image: Cardiology Society partner"  # "the 2nd logo"


def op_text(type_, n, text):
    return {"type": "replace_section_text", "target": {"type": type_, "ordinal": n}, "text": text}


def test_editing_the_second_cta_touches_only_that_link(tagged):
    res = apply_ops(tagged, [op_text("cta", 2, "Review the data")], env())
    assert res.reports[0].ok and "cta #2 of 7" in res.reports[0].detail
    after = secs(res.html, "cta")
    assert after[1] == "Review the data" and after[0] == "Request a sample" and len(after) == 7
    assert secs(res.html, "claim") == secs(tagged, "claim")  # the neighbouring claim is untouched


def test_editing_a_subheader_next_to_a_claim_in_the_same_cell(tagged):
    res = apply_ops(tagged, [op_text("subheader", 4, "Once-daily dosing")], env())
    assert secs(res.html, "subheader")[3] == "Once-daily dosing"
    assert "Cardiva is taken once daily, with or without food." in secs(res.html, "claim")


def test_ambiguous_references_list_every_option(tagged):
    res = apply_ops(tagged, [op_text("cta", None, "x")], env())
    assert res.ambiguous
    msg = res.errors[0]
    assert "There are 7 CTAs" in msg and '1) "Request a sample"' in msg and '7) "Talk to a representative"' in msg
    assert apply_ops(tagged, [op_text("cta", 8, "x")], env()).reports[0].detail.startswith("There are only 7")


def test_second_logo_can_be_swapped_alone(tagged):
    res = apply_ops(tagged, [{"type": "swap_logo", "logo": "new.svg", "target": {"type": "logo", "ordinal": 2}}], env())
    imgs = {i["alt"]: i["src"] for i in BeautifulSoup(res.html, "html.parser").find_all("img")}
    assert imgs["Cardiology Society partner"].startswith("data:") and imgs["NovaHeart Pharma logo"].startswith("https://")
    assert apply_ops(tagged, [{"type": "swap_logo", "logo": "new.svg"}], env()).ambiguous  # two logos, none named


def test_recolor_first_cta_background_reaches_the_button_cell(tagged):
    op = {"type": "recolor_section", "target": {"type": "cta", "ordinal": 1}, "property": "background", "to_color": "#112233"}
    soup = BeautifulSoup(apply_ops(tagged, [op], env()).html, "html.parser")
    a = soup.find("a", string="Request a sample")
    assert a.parent["bgcolor"] == "#112233" and "#112233" in a.parent["style"]  # the table cell around the link
    other = soup.find("a", string="Visit the HCP site")
    assert "#112233" not in other["style"]


def test_replace_isi_merges_all_four_isi_parts_and_leaves_no_debris(tagged):
    res = apply_ops(tagged, [{"type": "replace_isi"}], env())
    assert res.reports[0].ok and "4 ISI sections merged" in res.reports[0].detail
    soup = BeautifulSoup(res.html, "html.parser")
    assert secs(res.html, "isi") == [ISI]
    cell = soup.select_one('[data-slot="isi"]').find_parent("td")
    assert cell.find("ul") is None and "Tell your patients" not in res.html  # no empty list left behind
    assert secs(res.html, "footer") == secs(tagged, "footer")  # footer untouched


def test_retagging_updates_the_counts(tagged):
    cands = candidate_list(tagged, CLAIMS)
    partner = next(c for c in cands if "partner" in c.text)
    tags = {c.eid: c.type for c in cands}
    tags[partner.eid] = "image"
    out = apply_tags(tagged, tags, CLAIMS)
    assert len(secs(out, "logo")) == 1 and len(secs(out, "image")) == 8


def test_complex_email_passes_compliance_with_only_warnings():
    kb = KnowledgeBase(
        claims=CLAIMS,
        required_blocks={"ISI": ISI, "Disclaimer": "This material is for demonstration purposes only and describes a fictional product."},
        mandatory_text=["Please see full Prescribing Information."],
        banned_phrases=["guaranteed", "miracle", "cure", "risk-free"],
        brand_colors=["#0B5CAD", "#E8F1FA", "#D9232E", "#1F2933"],
        brand_fonts=["Georgia, serif", "Arial, Helvetica, sans-serif"],
        logo_uris={"data:image/svg+xml;base64,AAAA"},
    )
    report = validate_email(auto_tag(COMPLEX, CLAIMS), kb)
    assert report.passed, [f.message for f in report.findings if f.severity == "error"]
    assert {f.code for f in report.findings} == {"OFF_BRAND_COLOR", "LOGO_NOT_APPROVED"}  # externally hosted logo, grey-blue rule
