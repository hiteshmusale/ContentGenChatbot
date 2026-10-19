import pytest
from bs4 import BeautifulSoup

from workflows.adapt import AdaptEnv, apply_ops
from workflows.render import fill_email_template
from workflows.sections import (
    TargetError, apply_tags, auto_tag, candidate_list, list_sections, located_sections,
    resolve_target, strip_internal,
)

CLAIM = "Cardiva lowered LDL-C by up to 52% in adults with primary hyperlipidemia."
CLAIMS = {"CLM-001": CLAIM, "CLM-002": "Cardiva is taken once daily, with or without food."}
ISI = "IMPORTANT SAFETY INFORMATION: Cardiva is contraindicated in patients with known hypersensitivity."

# A realistic uploaded email: no tags at all, three subheaders, two images, hidden preheader.
MESSY = f"""<html><head><title>Cardiva update</title><style>.x{{color:red}}</style></head>
<body style="background-color:#f4f4f4">
<div style="display:none;max-height:0;overflow:hidden">Preview text here</div>
<table width="600"><tr><td><img src="https://cdn.test/novaheart-logo.png" alt="NovaHeart logo"></td></tr>
<tr><td bgcolor="#0b5cad" style="background-color:#0b5cad;padding:20px"><h1 style="color:#ffffff;font-size:26px">Introducing Cardiva</h1></td></tr>
<tr><td><img src="https://cdn.test/hero.jpg" alt="Hero"></td></tr>
<tr><td><h2>Why Cardiva</h2><p>Body paragraph one.</p>
<h2>Dosing made simple</h2><p>Body paragraph two.</p>
<p style="font-weight:bold;font-size:20px">Safety and tolerability</p>
<p>{CLAIM}</p></td></tr>
<tr><td align="center"><a href="https://x.test" style="display:inline-block;padding:14px 28px;background-color:#d9232e;color:#ffffff">Request a sample</a></td></tr>
<tr><td style="background-color:#e8f1fa;padding:20px">{ISI}</td></tr>
<tr><td>© NovaHeart <a href="#">Unsubscribe</a></td></tr></table></body></html>"""

EXPECTED = ["preheader", "logo", "header", "image", "subheader", "body", "subheader", "body",
            "subheader", "claim", "cta", "isi", "footer"]


def types(html):
    return [c.suggested for c in candidate_list(html, CLAIMS, "NovaHeart")]


def test_candidates_find_text_holders_and_images_but_not_wrappers_or_head():
    cands = candidate_list(MESSY, CLAIMS, "NovaHeart")
    texts = [c.text for c in cands]
    assert "Cardiva update" not in texts and ".x{color:red}" not in " ".join(texts)  # title/style skipped
    assert texts[0] == "Preview text here" and texts[2] == "Introducing Cardiva"
    assert len(cands) == len(EXPECTED)


def test_auto_detection_types():
    assert types(MESSY) == EXPECTED


def test_auto_tag_writes_slots_and_links_claims_to_approved_ids():
    soup = BeautifulSoup(auto_tag(MESSY, CLAIMS, "NovaHeart"), "html.parser")
    assert [e["data-slot"] for e in soup.find_all(attrs={"data-slot": True})] == EXPECTED
    claim = soup.select_one('[data-slot="claim"]')
    assert claim["data-claim-id"] == "CLM-001"


def test_sections_are_numbered_per_type_in_document_order():
    secs = list_sections(auto_tag(MESSY, CLAIMS, "NovaHeart"))
    subs = [s for s in secs if s.type == "subheader"]
    assert [(s.ordinal, s.count, s.text) for s in subs] == [
        (1, 3, "Why Cardiva"), (2, 3, "Dosing made simple"), (3, 3, "Safety and tolerability"),
    ]


def test_body_h1_fallback_and_large_font_header():
    no_h1 = "<html><body><p style='font-size:28px'>Big line</p><p>Some body text here.</p></body></html>"
    assert types(no_h1) == ["header", "body"]


def test_text_after_isi_is_footer():
    html = f"<html><body><p>Intro sentence here.</p><p>{ISI}</p><p>Sent by a company.</p></body></html>"
    assert types(html) == ["body", "isi", "footer"]


def test_near_miss_of_a_claim_is_tagged_with_that_claim_id():
    html = "<html><body><p>Cardiva lowered LDL-C by as much as 52% in adults with primary hyperlipidemia.</p></body></html>"
    soup = BeautifulSoup(auto_tag(html, CLAIMS), "html.parser")
    assert soup.find("p")["data-claim-id"] == "CLM-001"  # so the validator will hold it to the approved text


def test_apply_tags_replaces_everything_and_unlisted_become_untagged():
    tagged = auto_tag(MESSY, CLAIMS, "NovaHeart")
    cands = candidate_list(tagged, CLAIMS)
    hero = next(c for c in cands if c.text == "image: Hero")
    out = apply_tags(tagged, {hero.eid: "logo"}, CLAIMS)
    secs = list_sections(out)
    assert [(s.type, s.ordinal) for s in secs] == [("logo", 1)]  # only what was listed survives
    assert list_sections(apply_tags(out, {}, CLAIMS)) == []


def test_apply_tags_rejects_unknown_type():
    with pytest.raises(ValueError):
        apply_tags(MESSY, {0: "banner"}, CLAIMS)


def test_legacy_headline_and_container_tags_still_work():
    legacy = '<html><body><h1 data-slot="headline">Old</h1><td data-slot="claims"><li data-claim-id="CLM-001">x</li></td></body></html>'
    assert [(s.type, s.text) for s in list_sections(legacy)] == [("header", "Old"), ("claim", "x")]


def soup_of(html):
    return BeautifulSoup(html, "html.parser")


def test_resolve_target_by_number_and_errors():
    soup = soup_of(auto_tag(MESSY, CLAIMS, "NovaHeart"))
    sec, el = resolve_target(soup, "subheader", 2)
    assert sec.text == "Dosing made simple" and el.name == "h2"
    assert resolve_target(soup, "header", None)[0].text == "Introducing Cardiva"  # only one: no need to say which

    with pytest.raises(TargetError) as e:
        resolve_target(soup, "subheader", None)
    assert e.value.ambiguous
    msg = str(e.value)
    assert "3 subheaders" in msg and '1) "Why Cardiva"' in msg and '2) "Dosing made simple"' in msg and "Which one" in msg

    with pytest.raises(TargetError, match="only 3"):
        resolve_target(soup, "subheader", 4)
    with pytest.raises(TargetError, match="no preheader|no footer") as e2:
        resolve_target(soup_of("<p data-slot='body'>x</p>"), "footer", None)
    assert "Sections found: body (1)" in str(e2.value) and not e2.value.ambiguous


def test_strip_internal_removes_every_tagging_attribute_and_nothing_else():
    clean = strip_internal(auto_tag(MESSY, CLAIMS, "NovaHeart"))
    assert "data-slot" not in clean and "data-eid" not in clean and "data-claim-id" not in clean
    assert 'href="https://x.test"' in clean and "Dosing made simple" in clean


# ---- targeted edits ------------------------------------------------------------------

@pytest.fixture
def tagged():
    return auto_tag(MESSY, CLAIMS, "NovaHeart")


def env():
    return AdaptEnv(isi_text=ISI, load_logo=lambda n: "data:image/svg+xml;base64,AAAA" if n == "new.svg" else None)


def op_text(n, text):
    return {"type": "replace_section_text", "target": {"type": "subheader", "ordinal": n}, "text": text}


def test_replace_text_of_the_second_subheader_only(tagged):
    res = apply_ops(tagged, [op_text(2, "Simple once-daily dosing")], env())
    assert res.reports[0].ok and 'subheader #2 of 3 changed from "Dosing made simple"' in res.reports[0].detail
    subs = [s.text for s in list_sections(res.html) if s.type == "subheader"]
    assert subs == ["Why Cardiva", "Simple once-daily dosing", "Safety and tolerability"]
    assert res.html.count("Body paragraph") == 2  # nothing else touched


def test_missing_ordinal_with_several_matches_is_ambiguous_not_guessed(tagged):
    res = apply_ops(tagged, [op_text(None, "x")], env())
    assert not res.reports[0].ok and res.ambiguous and res.html == tagged
    assert "Which one do you mean" in res.errors[0]


def test_claims_and_isi_cannot_be_edited_freehand(tagged):
    claim = apply_ops(tagged, [{"type": "replace_section_text", "target": {"type": "claim", "ordinal": 1}, "text": "Cures everything"}], env())
    assert "verbatim" in claim.errors[0] and "Cures" not in claim.html
    isi = apply_ops(tagged, [{"type": "replace_section_text", "target": {"type": "isi", "ordinal": None}, "text": "Safe!"}], env())
    assert "approved ISI text" in isi.errors[0]
    img = apply_ops(tagged, [{"type": "replace_section_text", "target": {"type": "logo", "ordinal": None}, "text": "x"}], env())
    assert "logo swap" in img.errors[0]


def test_recolor_header_background_changes_its_banner_but_not_other_sections(tagged):
    op = {"type": "recolor_section", "target": {"type": "header", "ordinal": None}, "property": "background", "to_color": "#112233"}
    res = apply_ops(tagged, [op], env())
    assert res.reports[0].ok and "#112233" in res.html
    soup = soup_of(res.html)
    banner = soup.find("h1").parent
    assert banner["bgcolor"] == "#112233" and "#112233" in banner["style"]  # the cell around the header
    assert "#e8f1fa" in res.html and "#d9232e" in res.html  # ISI and CTA backgrounds untouched


def test_recolor_text_and_default_when_nothing_to_replace(tagged):
    op = {"type": "recolor_section", "target": {"type": "cta", "ordinal": None}, "property": "text", "to_color": "#000000"}
    cta = soup_of(apply_ops(tagged, [op], env()).html).select_one('[data-slot="cta"]')
    assert "color:#000000" in cta["style"] and "background-color:#d9232e" in cta["style"]  # only the text color changed
    plain = auto_tag("<html><body><h1>T</h1></body></html>", {})
    op2 = {"type": "recolor_section", "target": {"type": "header", "ordinal": None}, "property": "background", "to_color": "#abcdef"}
    assert "background-color:#ABCDEF" in apply_ops(plain, [op2], env()).html


def test_swap_the_second_logo_when_there_are_two():
    html = ('<html><body><img data-slot="logo" src="a.png" alt="A"><p data-slot="body">hi</p>'
            '<img data-slot="logo" src="b.png" alt="B"></body></html>')
    ambiguous = apply_ops(html, [{"type": "swap_logo", "logo": "new.svg"}], env())
    assert ambiguous.ambiguous and "2 logos" in ambiguous.errors[0] and "Which one" in ambiguous.errors[0]
    res = apply_ops(html, [{"type": "swap_logo", "logo": "new.svg", "target": {"type": "logo", "ordinal": 2}}], env())
    imgs = soup_of(res.html).find_all("img")
    assert imgs[0]["src"] == "a.png" and imgs[1]["src"].startswith("data:image/svg+xml")


def test_template_emails_are_tagged_granularly():
    html = fill_email_template(
        brand_name="N", colors={}, fonts={}, logo_src=None, subject="s", preheader="p", headline="Hi",
        body=["One.", "Two."], claims=[{"claim_id": "CLM-001", "text": CLAIM}, {"claim_id": "CLM-002", "text": "x"}],
        cta_text="Go", cta_url="#", isi=ISI, footer_lines=["f"],
    )
    got = [(s.type, s.ordinal, s.count) for s in list_sections(html)]
    assert got == [("preheader", 1, 1), ("logo", 1, 1), ("header", 1, 1), ("body", 1, 2), ("body", 2, 2),
                   ("claim", 1, 2), ("claim", 2, 2), ("cta", 1, 1), ("isi", 1, 1), ("footer", 1, 1)]
    res = apply_ops(html, [{"type": "replace_section_text", "target": {"type": "body", "ordinal": 2}, "text": "Changed."}], env())
    assert [s.text for s in list_sections(res.html) if s.type == "body"] == ["One.", "Changed."]
