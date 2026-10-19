import pytest
from bs4 import BeautifulSoup

from agent.ops import validate_ops
from workflows.adapt import AdaptEnv, apply_ops, norm_hex
from workflows.render import fill_email_template

CLAIM = "Cardiva lowered LDL-C by up to 52% in adults with primary hyperlipidemia."
ISI = "IMPORTANT SAFETY INFORMATION: Original ISI text."


@pytest.fixture
def html():
    return fill_email_template(
        brand_name="NovaHeart", colors={}, fonts={}, logo_src=None, subject="Subj", preheader="Pre",
        headline="Introducing Cardiva", body=["Plain body copy & more."],
        claims=[{"claim_id": "CLM-001", "text": CLAIM}], cta_text="Learn more", cta_url="https://x.test",
        isi=ISI, footer_lines=["Disclaimer text."],
    )


def env(**kw):
    return AdaptEnv(isi_text="IMPORTANT SAFETY INFORMATION: Updated ISI.", load_logo=lambda n: "data:image/svg+xml;base64,AAAA" if n == "new.svg" else None, **kw)


def text(html, sel):
    return BeautifulSoup(html, "html.parser").select_one(sel).get_text(" ", strip=True)


def test_template_is_600px_with_verbatim_tagged_claims(html):
    assert 'width="600"' in html and "max-width:600px" in html
    assert text(html, '[data-claim-id="CLM-001"]') == CLAIM  # verbatim, markup-free
    assert text(html, '[data-slot="isi"]') == ISI


def test_norm_hex():
    assert norm_hex("#abc") == "#AABBCC" and norm_hex("0B5CAD") is None and norm_hex("blue") is None


def test_change_color_replaces_all_case_insensitively_and_touches_nothing_else(html):
    res = apply_ops(html, [{"type": "change_color", "color": "#0b5cad", "to_color": "#7a1fa2"}], env())
    assert res.reports[0].ok and "#0B5CAD" not in res.html.upper()
    assert res.html.count("#7A1FA2") == html.upper().count("#0B5CAD")
    assert res.html.replace("#7A1FA2", "#0B5CAD") == html  # byte-identical otherwise


def test_change_color_missing_color_fails_cleanly(html):
    res = apply_ops(html, [{"type": "change_color", "color": "#123456", "to_color": "#000000"}], env())
    assert not res.reports[0].ok and "does not appear" in res.errors[0]
    assert res.html == html


def test_swap_logo_replaces_placeholder_with_image(html):
    res = apply_ops(html, [{"type": "swap_logo", "logo": "new.svg"}], env())
    assert res.reports[0].ok
    img = BeautifulSoup(res.html, "html.parser").select_one('[data-slot="logo"] img')
    assert img["src"] == "data:image/svg+xml;base64,AAAA"


def test_swap_logo_unknown_file_and_uploaded_html_without_slots():
    res = apply_ops("<html><body><img src='old.png'></body></html>", [{"type": "swap_logo", "logo": "new.svg"}], env())
    assert res.reports[0].ok and 'src="data:image/svg+xml;base64,AAAA"' in res.html  # falls back to first <img>
    res = apply_ops("<p>no image</p>", [{"type": "swap_logo", "logo": "new.svg"}], env())
    assert not res.reports[0].ok
    res = apply_ops("<img src='a'>", [{"type": "swap_logo", "logo": "nope.svg"}], env())
    assert "not found" in res.errors[0]


def test_replace_isi_in_slot_and_by_heuristic(html):
    res = apply_ops(html, [{"type": "replace_isi"}], env())
    assert text(res.html, '[data-slot="isi"]') == "IMPORTANT SAFETY INFORMATION: Updated ISI."
    legacy = "<div><p>Hello</p><div><b>IMPORTANT SAFETY INFORMATION</b> old text</div></div>"
    res = apply_ops(legacy, [{"type": "replace_isi"}], env())
    assert "Updated ISI" in res.html and "old text" not in res.html and "Hello" in res.html
    assert not apply_ops("<p>nothing</p>", [{"type": "replace_isi"}], env()).reports[0].ok


def test_replace_text_edits_copy_but_never_approved_claims(html):
    res = apply_ops(html, [{"type": "replace_text", "find": "Plain body copy", "replace": "Fixed copy"}], env())
    assert res.reports[0].ok and "Fixed copy & more." in BeautifulSoup(res.html, "html.parser").get_text()
    res = apply_ops(html, [{"type": "replace_text", "find": "lowered LDL-C", "replace": "cured"}], env())
    assert not res.reports[0].ok and "approved claim" in res.errors[0]
    assert text(res.html, '[data-claim-id="CLM-001"]') == CLAIM


def test_failed_ops_do_not_block_later_ops(html):
    ops = [{"type": "change_color", "color": "#123456", "to_color": "#000000"}, {"type": "replace_isi"}]
    res = apply_ops(html, ops, env())
    assert [r.ok for r in res.reports] == [False, True]


COLORS = {"primary": "#0B5CAD", "accent": "#D9232E"}


def test_validate_ops_resolves_roles_and_reports_problems():
    ops, problems = validate_ops(
        [
            {"type": "change_color", "color": "primary", "to_color": "#abc"},
            {"type": "swap_logo", "logo": "BRAND"},
            {"type": "change_color", "color": "accent", "to_color": "purple"},
            {"type": "swap_logo", "logo": "ghost.png"},
            {"type": "replace_text", "find": " "},
        ],
        COLORS, ["logo.svg", "new.svg"], "logo.svg",
    )
    assert ops == [
        {"type": "change_color", "color": "#0B5CAD", "to_color": "#AABBCC"},
        {"type": "swap_logo", "logo": "logo.svg"},
    ]
    assert len(problems) == 3
    assert any("purple" in p for p in problems) and any("ghost.png" in p for p in problems)
