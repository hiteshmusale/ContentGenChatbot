import pytest
from bs4 import BeautifulSoup

from tests.test_api import upload_all


@pytest.fixture
def ws(client, sample):
    wid = client.post("/workspaces", json={"name": "NovaHeart"}).json()["id"]
    upload_all(client, wid, sample)
    return wid


@pytest.fixture
def chat(client, ws):
    sid = client.post(f"/workspaces/{ws}/sessions").json()["id"]

    def send(text):
        r = client.post(f"/sessions/{sid}/messages", json={"content": text})
        assert r.status_code == 200, r.text
        return r.json()

    def decide(decision, feedback=None):
        r = client.post(f"/sessions/{sid}/plan", json={"decision": decision, "feedback": feedback})
        assert r.status_code == 200, r.text
        return r.json()

    send.decide, send.sid = decide, sid
    return send


GOOD_COPY = {
    "subject": "A new option for your patients",
    "preheader": "Learn about Cardiva",
    "headline": "Meet Cardiva",
    "body": ["Managing LDL-C is a daily challenge.", "Here is what the approved data show."],
    "cta_text": "Learn more",
}


def copy(**over):
    return {**GOOD_COPY, **over}


def fv(**kw):
    return {"inputs": [{"name": k, "value": v} for k, v in kw.items()]}


FULL = fv(
    product="cardiva", persona="hcp cardiologist", objective="launch",
    key_message="Strong LDL-C lowering",
)


def test_unclear_request_gets_a_reply_and_no_plan(chat, llm):
    llm.queue("Classification", {"swimlane": "unclear", "reply": "I can help with emails. What do you need?"})
    out = chat("hi")
    assert out["messages"][-1]["content"].startswith("I can help with emails")
    assert not out["awaiting_approval"]


def test_missing_inputs_are_asked_for_with_options(chat, llm):
    llm.queue("Classification", {"swimlane": "generate"})
    llm.queue("GatherResult", fv(objective="launch"))
    out = chat("write a launch email")
    reply = out["messages"][-1]["content"]
    assert "Generate from scratch" in reply
    assert "**persona**" in reply and "HCP Cardiologist, Primary Care Physician" in reply
    assert "**key_message**" in reply
    assert "**product**" not in reply  # single product is defaulted, not asked
    assert not out["awaiting_approval"]


def test_unknown_persona_is_rejected_with_options(chat, llm):
    llm.queue("Classification", {"swimlane": "generate"})
    llm.queue("GatherResult", fv(persona="Dentist", objective="launch", key_message="x"))
    reply = chat("email for dentists")["messages"][-1]["content"]
    assert "couldn't find persona 'Dentist'" in reply
    assert "HCP Cardiologist" in reply


def test_full_flow_plan_approve_result(chat, llm):
    llm.queue("Classification", {"swimlane": "generate"})
    llm.queue("GatherResult", FULL)
    llm.queue("ClaimSelection", {"claim_ids": ["CLM-001", "CLM-NOPE", "CLM-002"]})
    out = chat("Launch email for Cardiva aimed at cardiologists about strong LDL-C lowering")

    assert out["awaiting_approval"]
    plan = out["plan"]
    assert plan["workflow"] == "generate_email"
    assert plan["inputs"]["persona"] == "HCP Cardiologist"  # normalized
    assert [c["claim_id"] for c in plan["claims"]] == ["CLM-001", "CLM-002"]  # invalid id dropped
    assert plan["claims"][0]["text"] == "Cardiva lowered LDL-C by up to 52% in adults with primary hyperlipidemia."
    assert plan["doc_versions"] == {"brand": 1, "persona": 1, "claims": 1, "rules": 1}
    assert out["result"] is None  # nothing runs before approval
    assert "Approve to run it" in out["messages"][-1]["content"]

    llm.queue("EmailCopy", GOOD_COPY)
    done = chat.decide("approve")
    assert not done["awaiting_approval"]
    assert done["result"]["asset"]["workflow"] == "generate_email"
    assert done["result"]["validation"]["passed"] is True
    assert done["messages"][-1]["content"].startswith("Done. I generated")


def test_typing_while_plan_pending_revises_the_plan(chat, llm):
    llm.queue("Classification", {"swimlane": "generate"})
    llm.queue("GatherResult", FULL)
    llm.queue("ClaimSelection", {"claim_ids": ["CLM-001"]})
    chat("launch email")
    llm.queue("GatherResult", fv(**{**{i["name"]: i["value"] for i in FULL["inputs"]}, "cta": "Request a sample"}))
    llm.queue("ClaimSelection", {"claim_ids": ["CLM-001", "CLM-002"]})
    out = chat("add a CTA: Request a sample")
    assert out["awaiting_approval"]
    assert out["plan"]["inputs"]["cta"] == "Request a sample"
    assert len(out["plan"]["claims"]) == 2


def test_reject_cancels_and_next_message_starts_fresh(chat, llm):
    llm.queue("Classification", {"swimlane": "generate"})
    llm.queue("GatherResult", FULL)
    llm.queue("ClaimSelection", {"claim_ids": ["CLM-001"]})
    chat("launch email")
    out = chat.decide("reject")
    assert not out["awaiting_approval"] and out["result"] is None
    assert "cancelled" in out["messages"][-1]["content"]

    llm.queue("Classification", {"swimlane": "unclear", "reply": "ok"})
    chat("hello again")
    assert llm.script["Classification"] == []  # classified anew, old lane not reused


def test_new_request_after_completion_resets_state(client, ws, chat, llm):
    client.post(f"/workspaces/{ws}/assets/sample")
    llm.queue("Classification", {"swimlane": "generate"})
    llm.queue("GatherResult", FULL)
    llm.queue("ClaimSelection", {"claim_ids": ["CLM-001"]})
    chat("launch email")
    llm.queue("EmailCopy", GOOD_COPY)
    chat.decide("approve")

    llm.queue("Classification", {"swimlane": "adapt"})
    llm.queue("GatherResult", fv(source_asset="latest", changes="change primary color"))
    llm.queue("AdaptOps", {"ops": [{"type": "change_color", "color": "primary", "to_color": "#7A1FA2"}]})
    out = chat("now make the primary color purple")
    assert out["awaiting_approval"]
    assert out["plan"]["swimlane"] == "adapt"
    assert out["plan"]["claims"] == []  # mechanical lane never selects claims


def test_missing_documents_block_the_lane(client, llm):
    wid = client.post("/workspaces", json={"name": "Empty"}).json()["id"]
    sid = client.post(f"/workspaces/{wid}/sessions").json()["id"]
    llm.queue("Classification", {"swimlane": "generate"})
    out = client.post(f"/sessions/{sid}/messages", json={"content": "launch email"}).json()
    reply = out["messages"][-1]["content"]
    assert "claims.md" in reply and "persona.md" in reply
    assert "GatherResult" not in llm.calls


def test_plan_endpoint_without_pending_plan_is_409(client, chat):
    r = client.post(f"/sessions/{chat.sid}/plan", json={"decision": "approve"})
    assert r.status_code == 409


def test_session_listing_and_title(client, ws, chat, llm):
    llm.queue("Classification", {"swimlane": "unclear", "reply": "ok"})
    chat("Write something nice")
    sessions = client.get(f"/workspaces/{ws}/sessions").json()
    assert [s["title"] for s in sessions] == ["Write something nice"]
    state = client.get(f"/sessions/{chat.sid}").json()
    assert [m["role"] for m in state["messages"]] == ["user", "assistant"]


# ---- adapt lane ----------------------------------------------------------------------

@pytest.fixture
def adapt_ws(client, ws, sample):
    client.post(f"/workspaces/{ws}/logos", files={"file": ("logo_new.svg", sample("logo_new.svg").encode(), "image/svg+xml")})
    sample_asset = client.post(f"/workspaces/{ws}/assets/sample").json()
    return ws, sample_asset["id"]


def start_adapt(chat, llm, ops, changes="x", source="latest", unsupported=()):
    llm.queue("Classification", {"swimlane": "adapt"})
    llm.queue("GatherResult", fv(source_asset=source, changes=changes))
    llm.queue("AdaptOps", {"ops": ops, "unsupported": list(unsupported)})
    return chat("edit the email")


def test_adapt_without_any_email_tells_user_to_add_one(chat, llm):
    llm.queue("Classification", {"swimlane": "adapt"})
    reply = chat("swap the logo")["messages"][-1]["content"]
    assert "no email to work on" in reply and "GatherResult" not in llm.calls


def test_adapt_plan_dry_runs_and_shows_exact_changes(client, adapt_ws, chat, llm):
    _, asset_id = adapt_ws
    out = start_adapt(chat, llm, [
        {"type": "change_color", "color": "primary", "to_color": "#7A1FA2"},
        {"type": "swap_logo", "logo": "logo_new.svg"},
        {"type": "replace_isi"},
    ])
    assert out["awaiting_approval"]
    plan = out["plan"]
    assert plan["source"]["id"] == asset_id and plan["workflow"] == "adapt_email"
    text = out["messages"][-1]["content"]
    assert "Change color #0B5CAD to #7A1FA2 everywhere it appears (3 occurrences" in text
    assert "Swap logo to logo_new.svg" in text and "nothing else will be touched" in text
    assert out["result"] is None


def test_adapt_approve_creates_child_asset_and_latest_moves(client, adapt_ws, chat, llm):
    ws, asset_id = adapt_ws
    start_adapt(chat, llm, [{"type": "change_color", "color": "primary", "to_color": "#7A1FA2"}])
    done = chat.decide("approve")
    res = done["result"]["asset"]
    assert res["parent_id"] == asset_id and res["id"] != asset_id
    assert "#7A1FA2" in res["html"] and "#0B5CAD" not in res["html"].upper()
    assert "saved the edited email" in done["messages"][-1]["content"]

    assets = client.get(f"/workspaces/{ws}/assets").json()
    assert [a["origin"] for a in assets] == ["adapt", "sample"]
    assert assets[0]["parent_asset_id"] == asset_id
    original = client.get(f"/assets/{asset_id}/html").text
    assert "#0B5CAD" in original  # the source email is never modified in place

    # "latest" now points at the edited email, so edits chain
    start_adapt(chat, llm, [{"type": "replace_isi"}])
    plan = client.get(f"/sessions/{chat.sid}").json()["plan"]
    assert plan["source"]["id"] == res["id"]
    second = chat.decide("approve")["result"]["asset"]
    assert second["name"].endswith("(adapted v2)") and second["name"].count("adapted") == 1


def test_adapt_problems_are_reported_instead_of_planned(chat, adapt_ws, llm):
    out = start_adapt(chat, llm, [
        {"type": "swap_logo", "logo": "ghost.png"},
        {"type": "change_color", "color": "accent", "to_color": "purple"},
    ])
    assert not out["awaiting_approval"]
    reply = out["messages"][-1]["content"]
    assert "ghost.png" in reply and "logo_new.svg" in reply and "purple" in reply


def test_adapt_color_not_in_email_is_caught_at_plan_time(chat, adapt_ws, llm):
    out = start_adapt(chat, llm, [{"type": "change_color", "color": "#123456", "to_color": "#000000"}])
    assert not out["awaiting_approval"]
    assert "does not appear in this email" in out["messages"][-1]["content"]


def test_adapt_unknown_source_email(chat, adapt_ws, llm):
    llm.queue("Classification", {"swimlane": "adapt"})
    llm.queue("GatherResult", fv(source_asset="the holiday email", changes="x"))
    reply = chat("edit")["messages"][-1]["content"]
    assert "couldn't find an email called 'the holiday email'" in reply and "Options: latest" in reply


def test_adapt_can_target_asset_by_id(chat, adapt_ws, llm):
    _, asset_id = adapt_ws
    out = start_adapt(chat, llm, [{"type": "replace_isi"}], source=f"#{asset_id}")
    assert out["plan"]["source"]["id"] == asset_id


def test_adapt_plan_never_silently_drops_a_request(chat, adapt_ws, llm):
    out = start_adapt(
        chat, llm, [{"type": "replace_isi"}], unsupported=["rewrite the headline to sound friendlier"]
    )
    assert out["awaiting_approval"]
    text = out["messages"][-1]["content"]
    assert "Not included" in text and "rewrite the headline" in text
    assert out["plan"]["skipped"] == ["rewrite the headline to sound friendlier"]


def test_adapt_with_only_unsupported_requests_makes_no_plan(chat, adapt_ws, llm):
    out = start_adapt(chat, llm, [], unsupported=["write a new headline"])
    assert not out["awaiting_approval"]
    assert "can't do this one: write a new headline" in out["messages"][-1]["content"]


def test_adapt_result_is_validated_and_needs_review(client, adapt_ws, chat, llm):
    start_adapt(chat, llm, [{"type": "change_color", "color": "primary", "to_color": "#7A1FA2"}])
    done = chat.decide("approve")
    v = done["result"]["validation"]
    assert v["passed"] is True and not v["skipped"]
    assert "OFF_BRAND_COLOR" in [f["code"] for f in v["findings"]]  # a warning, not a blocker
    msg = done["messages"][-1]["content"]
    assert "validation passed" in msg and "reviewer approval" in msg

    asset = client.get(f"/assets/{done['result']['asset']['id']}").json()
    assert asset["status"] == "pending_review" and asset["validations"][0]["passed"]


def test_adapt_on_noncompliant_email_reports_errors(client, ws, chat, llm):
    bare = "<html><body><table><tr><td style='color:#0B5CAD'>Hello, guaranteed results.</td></tr></table></body></html>"
    client.post(f"/workspaces/{ws}/assets", files={"file": ("bare.html", bare.encode(), "text/html")})
    start_adapt(chat, llm, [{"type": "change_color", "color": "primary", "to_color": "#112233"}])
    msg = chat.decide("approve")["messages"][-1]["content"]
    assert "validation failed" in msg and "can't be approved yet" in msg
    assert "ISI block is missing" in msg and 'Banned phrase "guaranteed"' in msg


def test_editing_an_approved_email_leaves_it_approved_and_new_copy_pending(client, adapt_ws, chat, llm):
    _, asset_id = adapt_ws
    assert client.post(f"/assets/{asset_id}/review", json={"decision": "approved", "reviewer": "Dana"}).status_code == 200
    start_adapt(chat, llm, [{"type": "change_color", "color": "primary", "to_color": "#7A1FA2"}])
    child = chat.decide("approve")["result"]["asset"]["id"]
    assert client.get(f"/assets/{asset_id}").json()["status"] == "approved"
    assert client.get(f"/assets/{child}").json()["status"] == "pending_review"
    assert client.get(f"/assets/{child}/export").status_code == 409


def test_adapt_accepts_the_option_label_as_source(chat, adapt_ws, llm):
    _, asset_id = adapt_ws
    out = start_adapt(chat, llm, [{"type": "replace_isi"}], source=f"#{asset_id} NovaHeart sample email")
    assert out["plan"]["source"]["id"] == asset_id


# ---- generate lane -------------------------------------------------------------------

def start_generate(chat, llm, claim_ids=("CLM-001", "CLM-002")):
    llm.queue("Classification", {"swimlane": "generate"})
    llm.queue("GatherResult", FULL)
    llm.queue("ClaimSelection", {"claim_ids": list(claim_ids)})
    return chat("Launch email for Cardiva aimed at cardiologists")


def test_generate_creates_stored_email_with_verbatim_claims(client, ws, chat, llm):
    from bs4 import BeautifulSoup

    start_generate(chat, llm)
    llm.queue("EmailCopy", GOOD_COPY)
    done = chat.decide("approve")
    asset = done["result"]["asset"]
    assert asset["attempts"] == 1 and asset["parent_id"] is None
    soup = BeautifulSoup(asset["html"], "html.parser")
    assert soup.select_one('[data-slot="header"]').get_text() == "Meet Cardiva"
    claims = {li["data-claim-id"]: li.get_text(strip=True) for li in soup.select("[data-claim-id]")}
    assert list(claims) == ["CLM-001", "CLM-002"]  # exactly the approved selection
    assert claims["CLM-001"] == "Cardiva lowered LDL-C by up to 52% in adults with primary hyperlipidemia."
    assert "IMPORTANT SAFETY INFORMATION" in soup.select_one('[data-slot="isi"]').get_text()

    stored = client.get(f"/assets/{asset['id']}").json()
    assert stored["origin"] == "generate" and stored["status"] == "pending_review"
    assert stored["meta"]["claims"] == ["CLM-001", "CLM-002"] and stored["meta"]["attempts"] == 1
    assert stored["meta"]["doc_versions"] == {"brand": 1, "persona": 1, "claims": 1, "rules": 1}
    assert "reviewer approval" in done["messages"][-1]["content"]


def test_generate_model_only_sees_approved_claims_and_rules(chat, llm):
    start_generate(chat, llm, claim_ids=("CLM-001",))
    llm.queue("EmailCopy", GOOD_COPY)
    chat.decide("approve")
    prompt = next(p for name, p in llm.prompts if name == "EmailCopy")
    assert 'CLM-001: "Cardiva lowered LDL-C' in prompt
    assert "CLM-002" not in prompt  # unselected claims are not offered
    assert "guaranteed" in prompt and "miracle" in prompt  # banned phrases are stated up front
    assert "HCP Cardiologist" in prompt and "Clinical, concise" in prompt  # persona drives tone


def test_generate_retries_with_the_failed_checks_fed_back(chat, llm):
    start_generate(chat, llm)
    llm.queue("EmailCopy", copy(headline="Guaranteed results"), GOOD_COPY)
    done = chat.decide("approve")
    asset = done["result"]["asset"]
    assert asset["attempts"] == 2 and done["result"]["validation"]["passed"] is True
    assert "Meet Cardiva" in asset["html"] and "Guaranteed" not in asset["html"]
    second_prompt = [p for name, p in llm.prompts if name == "EmailCopy"][1]
    assert 'Banned phrase "guaranteed"' in second_prompt and "previous draft failed" in second_prompt
    assert any("Attempt 1 failed" in line for line in asset["report"])


def test_generate_rejects_invented_statistics(chat, llm):
    start_generate(chat, llm)
    llm.queue("EmailCopy", copy(body=["Reduces stroke risk by 40% in 3 weeks."]), GOOD_COPY)
    asset = chat.decide("approve")["result"]["asset"]
    assert asset["attempts"] == 2
    second = [p for name, p in llm.prompts if name == "EmailCopy"][1]
    assert "Unsupported number(s)" in second and "40%" in second


def test_generate_allows_numbers_from_claims_and_user_input(chat, llm):
    start_generate(chat, llm)
    llm.queue("EmailCopy", copy(body=["Up to 52% LDL-C lowering, once daily."]))
    assert chat.decide("approve")["result"]["asset"]["attempts"] == 1


def test_generate_flags_reworded_claims(chat, llm):
    start_generate(chat, llm)
    sneaky = copy(body=["Cardiva lowered LDL-C by as much as 52% in adults with primary hyperlipidemia."])
    llm.queue("EmailCopy", sneaky, GOOD_COPY)
    asset = chat.decide("approve")["result"]["asset"]
    assert asset["attempts"] == 2  # near-verbatim paraphrase of a claim is an error, so it retried


def test_generate_gives_up_after_three_attempts_but_saves_the_failing_draft(client, ws, chat, llm):
    start_generate(chat, llm)
    bad = copy(headline="A miracle cure")
    llm.queue("EmailCopy", bad, bad, bad)
    done = chat.decide("approve")
    asset, validation = done["result"]["asset"], done["result"]["validation"]
    assert asset["attempts"] == 3 and llm.calls.count("EmailCopy") == 3  # capped, no 4th call
    assert validation["passed"] is False
    msg = done["messages"][-1]["content"]
    assert "validation failed" in msg and "Banned phrase" in msg
    r = client.post(f"/assets/{asset['id']}/review", json={"decision": "approved", "reviewer": "Dana"})
    assert r.status_code == 409  # a failing draft can never be approved


def test_generate_cta_url_is_taken_from_the_request(client, ws, chat, llm):
    llm.queue("Classification", {"swimlane": "generate"})
    llm.queue("GatherResult", fv(**{**{i["name"]: i["value"] for i in FULL["inputs"]}, "cta": "Book at https://brand.test/book"}))
    llm.queue("ClaimSelection", {"claim_ids": ["CLM-001"]})
    chat("launch email")
    llm.queue("EmailCopy", GOOD_COPY)
    html = chat.decide("approve")["result"]["asset"]["html"]
    assert 'href="https://brand.test/book"' in html


# ---- section-targeted edits ------------------------------------------------------------

@pytest.fixture
def messy(client, ws):
    from tests.test_sections import MESSY

    r = client.post(f"/workspaces/{ws}/assets", files={"file": ("messy.html", MESSY.encode(), "text/html")})
    return r.json()["id"]


def section_op(n=None, text="Simple once-daily dosing", type_="replace_section_text", target="subheader", **extra):
    return {"type": type_, "target_type": target, "target_ordinal": n, "text": text, **extra}


def test_second_subheader_edit_shows_the_exact_section_for_confirmation(client, messy, chat, llm):
    out = start_adapt(chat, llm, [section_op(2)])
    assert out["awaiting_approval"]
    text = out["messages"][-1]["content"]
    assert 'Replace the text of subheader #2 with "Simple once-daily dosing"' in text
    assert 'subheader #2 of 3 changed from "Dosing made simple"' in text  # which one it understood
    assert "Check these are the right sections" in text
    assert "auto-detected and not yet checked" in text  # tags came from detection, not a person

    done = chat.decide("approve")
    html = done["result"]["asset"]["html"]
    assert "Simple once-daily dosing" in html and "Why Cardiva" in html and "Safety and tolerability" in html
    assert "Dosing made simple" not in html


def test_ambiguous_reference_asks_which_one_then_proceeds(client, messy, chat, llm):
    out = start_adapt(chat, llm, [section_op(None)])
    assert not out["awaiting_approval"]
    reply = out["messages"][-1]["content"]
    assert "There are 3 subheaders" in reply and '1) "Why Cardiva"' in reply and '3) "Safety and tolerability"' in reply
    assert "Tell me which one" in reply

    llm.queue("GatherResult", fv(source_asset="latest", changes="change the second subheader"))
    llm.queue("AdaptOps", {"ops": [section_op(2)], "unsupported": []})
    out = chat("the second one")  # the conversation continues, nothing was applied yet
    assert out["awaiting_approval"] and out["plan"]["ops"][0]["target"] == {"type": "subheader", "ordinal": 2}
    assert client.get(f"/assets/{messy}/html").text.count("Dosing made simple") == 1  # source untouched


def test_the_prompt_lists_the_emails_sections_so_the_model_can_map_references(client, messy, chat, llm):
    start_adapt(chat, llm, [section_op(2)])
    prompt = next(p for name, p in llm.prompts if name == "AdaptOps")
    assert 'subheader 2 of 3: "Dosing made simple"' in prompt and 'header 1 of 1: "Introducing Cardiva"' in prompt


def test_claim_and_isi_edits_are_refused_with_a_reason(client, messy, chat, llm):
    out = start_adapt(chat, llm, [section_op(1, "Cures all", target="claim")])
    assert not out["awaiting_approval"] and "verbatim" in out["messages"][-1]["content"]
    out = start_adapt(chat, llm, [section_op(None, "Safe", target="isi")])
    assert "approved ISI text" in out["messages"][-1]["content"]


def test_number_out_of_range_and_missing_section_are_explained(client, messy, chat, llm):
    out = start_adapt(chat, llm, [section_op(5)])
    assert "only 3" in out["messages"][-1]["content"]
    out = start_adapt(chat, llm, [section_op(None, "x", target="header", type_="replace_section_text")])
    assert out["awaiting_approval"]  # exactly one header: no need to say which


def test_recolor_asks_background_or_text_when_unspecified(client, messy, chat, llm):
    op = {"type": "recolor_section", "target_type": "header", "target_ordinal": None, "to_color": "#112233", "property": None}
    out = start_adapt(chat, llm, [op])
    assert not out["awaiting_approval"] and "background color or the text color" in out["messages"][-1]["content"]
    op["property"] = "background"
    out = start_adapt(chat, llm, [op])
    assert out["awaiting_approval"] and "Set the background color of the header to #112233" in out["messages"][-1]["content"] and "header #1 of 1" in out["messages"][-1]["content"]


def test_confirmed_tags_drop_the_auto_detected_note(client, messy, chat, llm):
    cands = client.get(f"/assets/{messy}/sections").json()["candidates"]
    client.put(f"/assets/{messy}/tags", json={"tags": [{"eid": c["eid"], "type": c["type"]} for c in cands]})
    out = start_adapt(chat, llm, [section_op(2)])
    assert "auto-detected" not in out["messages"][-1]["content"]
    assert out["plan"]["tag_status"] == "confirmed"


def test_edited_email_keeps_its_tags_and_status(client, messy, chat, llm):
    start_adapt(chat, llm, [section_op(2)])
    child = chat.decide("approve")["result"]["asset"]["id"]
    sections = client.get(f"/assets/{child}/sections").json()
    assert sections["tag_status"] == "suggested"
    assert [s["text"] for s in sections["sections"] if s["type"] == "subheader"][1] == "Simple once-daily dosing"


# ---- personalize lane ----------------------------------------------------------------------

def rewrites(*items):
    return {"rewrites": [{"type": t, "ordinal": n, "text": text} for t, n, text in items]}


PCP = rewrites(
    ("header", 1, "A simple option for your patients"),
    ("body", 1, "Here is a practical look at the approved information."),
    ("cta", 1, "See the details"),
)


def start_personalize(chat, llm, source="latest", persona="primary care physician", claim_ids=("CLM-002", "CLM-001")):
    llm.queue("Classification", {"swimlane": "personalize"})
    llm.queue("GatherResult", fv(source_asset=source, product="cardiva", persona=persona))
    llm.queue("ClaimSelection", {"claim_ids": list(claim_ids)})
    return chat("adapt it for primary care physicians")


def test_personalize_plan_states_what_may_and_may_not_change(client, adapt_ws, chat, llm):
    out = start_personalize(chat, llm)
    assert out["awaiting_approval"]
    plan, text = out["plan"], out["messages"][-1]["content"]
    assert plan["workflow"] == "personalize_email" and plan["inputs"]["persona"] == "Primary Care Physician"
    assert plan["rewritable"] == {"preheader": 1, "header": 1, "body": 1, "cta": 1}
    assert "May be rewritten for this persona" in text and "Never changed" in text and "claim wording" in text
    assert [c["claim_id"] for c in plan["claims"]] == ["CLM-002", "CLM-001"]  # lead with the persona's claims
    assert out["result"] is None


def test_personalize_rewrites_copy_but_claims_isi_and_footer_are_byte_identical(client, adapt_ws, chat, llm):
    ws, src_id = adapt_ws
    start_personalize(chat, llm)
    llm.queue("PersonaRewrite", PCP)
    done = chat.decide("approve")
    asset = done["result"]["asset"]
    assert asset["parent_id"] == src_id and asset["persona"] == "Primary Care Physician"
    assert asset["name"].endswith("(Primary Care Physician)") and asset["attempts"] == 1

    before = BeautifulSoup(client.get(f"/assets/{src_id}/html").text, "html.parser")
    after = BeautifulSoup(asset["html"], "html.parser")
    text = lambda soup, t: [e.get_text(strip=True) for e in soup.select(f'[data-slot="{t}"]')]  # noqa: E731
    assert text(after, "header") == ["A simple option for your patients"] and text(after, "cta") == ["See the details"]
    assert text(after, "isi") == text(before, "isi") and text(after, "footer") == text(before, "footer")
    claims = {e["data-claim-id"]: e.get_text(strip=True) for e in after.select("[data-claim-id]")}
    assert claims == {e["data-claim-id"]: e.get_text(strip=True) for e in before.select("[data-claim-id]")}  # verbatim
    assert list(claims) == ["CLM-002", "CLM-001", "CLM-003"]  # emphasis: the persona's claims lead
    assert done["result"]["validation"]["passed"] is True
    assert "personalized the email for Primary Care Physician" in done["messages"][-1]["content"]
    assert any("Claims reordered" in line for line in asset["report"])


def test_personalize_never_modifies_the_source_email(client, adapt_ws, chat, llm):
    _, src_id = adapt_ws
    original = client.get(f"/assets/{src_id}/html").text
    start_personalize(chat, llm)
    llm.queue("PersonaRewrite", PCP)
    chat.decide("approve")
    assert client.get(f"/assets/{src_id}/html").text == original
    assert client.get(f"/assets/{src_id}").json()["status"] == "pending_review"


def test_personalize_retries_when_the_rewrite_invents_a_number(chat, adapt_ws, llm):
    start_personalize(chat, llm)
    bad = rewrites(("header", 1, "Cuts LDL-C 90% faster"))
    llm.queue("PersonaRewrite", bad, PCP)
    asset = chat.decide("approve")["result"]["asset"]
    assert asset["attempts"] == 2
    second = [p for n, p in llm.prompts if n == "PersonaRewrite"][1]
    assert "Unsupported number(s) in the rewrite: 90%" in second


def test_personalize_retries_on_banned_phrase_and_on_bad_section_number(chat, adapt_ws, llm):
    start_personalize(chat, llm)
    llm.queue(
        "PersonaRewrite",
        rewrites(("header", 1, "A guaranteed option")),
        rewrites(("body", 7, "Nope")),  # the email has one body paragraph
        PCP,
    )
    asset = chat.decide("approve")["result"]["asset"]
    assert asset["attempts"] == 3
    prompts = [p for n, p in llm.prompts if n == "PersonaRewrite"]
    assert 'Banned phrase "guaranteed"' in prompts[1]
    assert "only 1" in prompts[2] and "no number 7" in prompts[2]


def test_personalize_flags_a_reworded_claim_introduced_by_the_rewrite(chat, adapt_ws, llm):
    start_personalize(chat, llm)
    sneaky = rewrites(("body", 1, "Cardiva lowered LDL-C by as much as 52% in adults with primary hyperlipidemia."))
    llm.queue("PersonaRewrite", sneaky, PCP)
    assert chat.decide("approve")["result"]["asset"]["attempts"] == 2


def test_personalize_gives_up_after_three_attempts_and_saves_the_failing_draft(client, adapt_ws, chat, llm):
    start_personalize(chat, llm)
    bad = rewrites(("header", 1, "A miracle option"))
    llm.queue("PersonaRewrite", bad, bad, bad)
    done = chat.decide("approve")
    asset = done["result"]["asset"]
    assert asset["attempts"] == 3 and llm.calls.count("PersonaRewrite") == 3
    assert done["result"]["validation"]["passed"] is False
    assert client.post(f"/assets/{asset['id']}/review", json={"decision": "approved"}).status_code == 409


def test_an_already_noncompliant_source_does_not_block_personalizing_it(client, ws, chat, llm):
    from tests.test_sections import MESSY  # has no required ISI/disclaimer text: errors exist before we touch it

    aid = client.post(f"/workspaces/{ws}/assets", files={"file": ("m.html", MESSY.encode(), "text/html")}).json()["id"]
    assert client.get(f"/assets/{aid}").json()["validations"][0]["passed"] is False
    start_personalize(chat, llm, source=str(aid))
    llm.queue("PersonaRewrite", rewrites(("header", 1, "A simple option for your patients")))
    done = chat.decide("approve")
    assert done["result"]["asset"]["attempts"] == 1  # only NEW problems trigger a retry
    assert done["result"]["validation"]["passed"] is False  # but the result is honestly reported as non-compliant


def test_personalize_needs_tagged_sections(client, adapt_ws, chat, llm):
    ws, src_id = adapt_ws
    client.put(f"/assets/{src_id}/tags", json={"tags": []})  # clear every tag
    out = start_personalize(chat, llm)
    assert not out["awaiting_approval"] and "no tagged headings or body text" in out["messages"][-1]["content"]


def test_personalize_unknown_persona_lists_options(chat, adapt_ws, llm):
    llm.queue("Classification", {"swimlane": "personalize"})
    llm.queue("GatherResult", fv(source_asset="latest", product="cardiva", persona="Dentist"))
    reply = chat("adapt for dentists")["messages"][-1]["content"]
    assert "couldn't find persona 'Dentist'" in reply and "HCP Cardiologist" in reply
