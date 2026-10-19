import pytest

from workflows.render import fill_email_template
from workflows.validator import KnowledgeBase, validate_email

CLAIM = "Cardiva lowered LDL-C by up to 52% in adults with primary hyperlipidemia."
ISI = "IMPORTANT SAFETY INFORMATION: Cardiva is contraindicated in patients with known hypersensitivity."
DISC = "This material is for demonstration purposes only."
MANDATORY = "Please see full Prescribing Information."
LOGO = "data:image/svg+xml;base64,AAAA"


@pytest.fixture
def kb():
    return KnowledgeBase(
        claims={"CLM-001": CLAIM, "CLM-002": "Cardiva is taken once daily, with or without food."},
        required_blocks={"ISI": ISI, "Disclaimer": DISC},
        mandatory_text=[MANDATORY],
        banned_phrases=["guaranteed", "cure", "risk-free"],
        brand_colors=["#0B5CAD", "#E8F1FA", "#D9232E", "#1F2933"],
        brand_fonts=["Georgia, serif", "Arial, Helvetica, sans-serif"],
        logo_uris={LOGO},
    )


def make(**over):
    args = dict(
        brand_name="NovaHeart", colors={}, fonts={}, logo_src=LOGO, subject="Subj", preheader="Pre",
        headline="Introducing Cardiva", body=["Plain body copy."],
        claims=[{"claim_id": "CLM-001", "text": CLAIM}], cta_text="Learn more", cta_url="https://x.test",
        isi=ISI, footer_lines=[DISC, MANDATORY],
    )
    args.update(over)
    return fill_email_template(**args)


def codes(report, severity=None):
    return [f.code for f in report.findings if severity in (None, f.severity)]


def test_clean_email_passes_with_no_findings(kb):
    r = validate_email(make(), kb)
    assert r.passed and r.findings == []


def test_altered_tagged_claim_is_an_error(kb):
    r = validate_email(make(claims=[{"claim_id": "CLM-001", "text": CLAIM.replace("52%", "55%")}]), kb)
    assert not r.passed and codes(r, "error") == ["CLAIM_ALTERED"]
    assert "55%" in r.findings[0].message and "52%" in r.findings[0].message


def test_unknown_claim_id_is_an_error(kb):
    r = validate_email(make(claims=[{"claim_id": "CLM-999", "text": "Invented claim."}]), kb)
    assert "CLAIM_UNKNOWN" in codes(r, "error")


def test_untagged_verbatim_claim_is_fine_but_near_miss_is_caught(kb):
    ok = '<html><body><p>Cardiva lowered LDL-C by up to 52% in adults with primary hyperlipidemia.</p></body></html>'
    assert "CLAIM_ALTERED" not in codes(validate_email(ok, kb))
    sneaky = ok.replace("52%", "62%").replace("up to", "as much as")
    assert "CLAIM_ALTERED" in codes(validate_email(sneaky, kb), "error")


def test_whitespace_differences_in_claims_are_ignored(kb):
    r = validate_email(make(claims=[{"claim_id": "CLM-001", "text": CLAIM.replace(" ", "  \n ")}]), kb)
    assert r.passed


def test_missing_and_altered_isi(kb):
    assert "REQUIRED_BLOCK_MISSING" in codes(validate_email(make(isi="Be careful."), kb), "error")
    tweaked = make(isi=ISI.replace("contraindicated", "not recommended"))
    r = validate_email(tweaked, kb)
    assert "REQUIRED_BLOCK_ALTERED" in codes(r, "error")


def test_missing_disclaimer_and_mandatory_text(kb):
    r = validate_email(make(footer_lines=[]), kb)
    assert {"REQUIRED_BLOCK_MISSING", "MANDATORY_TEXT_MISSING"} <= set(codes(r, "error"))


@pytest.mark.parametrize("where", ["headline", "body", "preheader"])
def test_banned_phrase_case_insensitive_anywhere_visible(kb, where):
    val = {"headline": "GUARANTEED results", "body": ["a risk-free option"], "preheader": "a cure"}[where]
    r = validate_email(make(**{where: val if where != "body" else val}), kb)
    assert "BANNED_PHRASE" in codes(r, "error")


def test_banned_phrase_uses_word_boundaries(kb):
    r = validate_email(make(body=["A secure and accurate portal; procure samples."]), kb)
    assert "BANNED_PHRASE" not in codes(r)


def test_off_brand_color_and_font_are_warnings_not_errors(kb):
    r = validate_email(make(colors={"primary": "#7A1FA2"}, fonts={"body": "Comic Sans MS, cursive"}), kb)
    assert r.passed
    assert {"OFF_BRAND_COLOR", "OFF_BRAND_FONT"} <= set(codes(r, "warning"))
    assert any("#7A1FA2" in f.message for f in r.findings)


def test_neutral_grays_and_hex_shorthand_are_not_flagged(kb):
    html = make().replace("#FFFFFF", "#fff")
    assert validate_email(html.replace("#6B7280", "#999999"), kb).passed
    assert codes(validate_email(html, kb)) == []


def test_logo_checks(kb):
    assert "NO_LOGO" in codes(validate_email(make(logo_src=None), kb), "warning")
    assert "LOGO_NOT_APPROVED" in codes(validate_email(make(logo_src="https://cdn.test/other.png"), kb), "warning")


def test_missing_unsubscribe_and_no_claims_are_warnings(kb):
    html = make(claims=[]).replace("Unsubscribe", "Manage")
    r = validate_email(html.replace("#unsubscribe", "#prefs"), kb)
    assert r.passed and {"NO_UNSUBSCRIBE", "NO_CLAIMS"} <= set(codes(r, "warning"))


def test_validation_is_deterministic(kb):
    html = make(headline="a cure", colors={"accent": "#00FF00"})
    assert validate_email(html, kb).as_dict() == validate_email(html, kb).as_dict()


def test_paraphrase_next_to_the_verbatim_claim_is_still_caught(kb):
    para = "Cardiva lowered LDL-C by as much as 52% in adults with primary hyperlipidemia."
    r = validate_email(make(body=[para]), kb)  # CLM-001 is present verbatim AND reworded in the body
    assert not r.passed and "CLAIM_ALTERED" in codes(r, "error")
    assert para in r.findings[0].message


def test_verbatim_claim_appearing_twice_is_not_flagged(kb):
    r = validate_email(make(body=[CLAIM]), kb)  # same text as the tagged claim, exactly
    assert r.passed


def test_text_tagged_as_a_claim_but_not_approved_is_an_error(kb):
    html = make().replace("</ul>", '<li data-slot="claim">Cardiva cures hypertension.</li></ul>')
    r = validate_email(html, kb)
    assert not r.passed and any(f.code == "CLAIM_UNKNOWN" and "cures hypertension" in f.message for f in r.findings)
