"""Workflow implementations. Each takes (ctx, inputs, plan) and returns a result dict.
adapt_email is real and deterministic; generate and personalize are placeholders until
steps 5 and 6."""
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from backend import assets
from backend.models import Asset
from workflows.adapt import apply_ops


class WorkflowError(Exception):
    pass


@dataclass
class WorkflowContext:
    db: Session
    workspace_id: int
    session_id: str | None = None
    llm: Any = None  # chat model, for workflows that write copy


def adapt_email(ctx: WorkflowContext, inputs: dict, plan: dict) -> dict:
    src = ctx.db.get(Asset, int(inputs["source_asset"]))
    if src is None or src.workspace_id != ctx.workspace_id:
        raise WorkflowError("the source email no longer exists")
    result = apply_ops(assets.asset_html(src), plan["ops"], assets.adapt_env(ctx.db, ctx.workspace_id))
    if result.errors:
        raise WorkflowError("; ".join(result.errors))
    details = [r.detail for r in result.reports]
    n = src.meta.get("edit_n", 0) + 1
    root = re.sub(r"( \(adapted[^)]*\))+$", "", src.name)
    new = assets.create_asset(
        ctx.db, ctx.workspace_id,
        name=f"{root} (adapted v{n})", origin="adapt", html=result.html,
        session_id=ctx.session_id, parent_id=src.id, tag_status=src.tag_status,
        meta={"ops": plan["ops"], "report": details, "doc_versions": plan["doc_versions"], "edit_n": n},
    )
    return {
        "workflow": "adapt_email", "id": new.id, "name": new.name, "html": result.html,
        "has_preview": new.has_preview, "parent_id": src.id, "report": details,
    }


def generate_email(ctx: WorkflowContext, inputs: dict, plan: dict) -> dict:
    """Write copy with the model, insert approved claims verbatim, render, and re-check.

    The copy loop retries (max 3) with the failed checks fed back. If it never passes,
    the last draft is still saved, with its failing validation, so the user can see why.
    """
    from agent import subgraphs
    from agent.skills import load_agent_md
    from backend import compliance, service
    from workflows.generate import invented_numbers
    from workflows.validator import validate_email

    db, ws = ctx.db, ctx.workspace_id
    if ctx.llm is None:
        raise WorkflowError("no language model is configured for copy writing")
    claims = plan["claims"]
    kb = compliance.knowledge_base(db, ws)
    persona = next((p for p in service.active_rows(db, ws, "persona") if p.name == inputs.get("persona")), None)
    brand = service.active_rows(db, ws, "brand")
    tone = "; ".join(t.value for t in brand if t.category == "tone")
    cta_url = next(iter(re.findall(r"https?://\S+", inputs.get("cta", ""))), assets.DEFAULT_CTA_URL)

    claim_lines = "\n".join(f'- {c["claim_id"]}: "{c["text"]}"' for c in claims)
    brief = (
        f"Write the copy for a promotional email.\n"
        f"Product: {inputs.get('product')}\nObjective: {inputs.get('objective')}\n"
        f"Key message: {inputs.get('key_message')}\nCTA wish: {inputs.get('cta') or '(none given)'}\n"
        f"Audience: {persona.name if persona else inputs.get('persona')}"
        + (f"; {persona.description}\nAudience tone: {persona.tone}\nAudience concerns: {', '.join(persona.concerns)}" if persona else "")
        + f"\nBrand tone: {tone}\n\n"
        "These approved claims will be shown verbatim in a list right below your body copy. Write copy that "
        f"introduces and frames them. Do NOT repeat, reword or add to them:\n{claim_lines}\n\n"
        "Rules: make no factual, efficacy, safety or statistical statement of your own; use no numbers except "
        "ones that appear in the approved claims; do not use these banned phrases: "
        f"{', '.join(kb.banned_phrases)}. US English."
    )
    allowed_sources = [c["text"] for c in claims] + [str(v) for v in inputs.values()]

    def render(copy) -> str:
        return assets.render_email(
            db, ws, subject=copy.subject, preheader=copy.preheader, headline=copy.headline,
            body=copy.body, claims=claims, cta_text=copy.cta_text, cta_url=cta_url,
        )

    def check(copy, html: str) -> list[str]:
        texts = [copy.subject, copy.preheader, copy.headline, copy.cta_text, *copy.body]
        problems = []
        if bad := invented_numbers(texts, allowed_sources):
            problems.append(
                f"Unsupported number(s) in the copy: {', '.join(bad)}. Only numbers from the approved claims may appear."
            )
        if not copy.body or any(not p.strip() for p in copy.body):
            problems.append("The body must have at least one non-empty paragraph.")
        problems += [f.message for f in validate_email(html, kb).findings if f.severity == "error"]
        return problems

    graph = subgraphs.build_copy_graph(ctx.llm, load_agent_md(), brief, render, check)
    out = graph.invoke({})
    copy, html, history = out["copy"], out["html"], out["history"]

    name = f"{inputs.get('product')}: {inputs.get('objective')}"[:100]
    new = assets.create_asset(
        db, ws, name=name, origin="generate", html=html, session_id=ctx.session_id, tag_status="tagged",
        meta={
            "inputs": inputs, "claims": [c["claim_id"] for c in claims], "copy": copy,
            "attempts": len(history), "doc_versions": plan["doc_versions"],
        },
    )
    report = [f"Copy written in {len(history)} attempt{'s' if len(history) != 1 else ''}"]
    for h in history:
        if h["problems"]:
            report.append(f"Attempt {h['attempt']} failed checks: " + "; ".join(h["problems"]))
    return {
        "workflow": "generate_email", "id": new.id, "name": new.name, "html": html,
        "has_preview": new.has_preview, "parent_id": None, "report": report, "attempts": len(history),
    }


def personalize_email(ctx: WorkflowContext, inputs: dict, plan: dict) -> dict:
    """Rewrite an email's tone and emphasis for a persona, leaving claims, ISI, footer and
    images alone. The model proposes "section X becomes this text"; deterministic code
    applies it. Retries (max 3) with the failed checks fed back; the last draft is saved
    even if it still fails, so the user can see why."""
    from agent import subgraphs
    from agent.skills import load_agent_md
    from backend import compliance, service
    from workflows import personalize as pz
    from workflows.adapt import apply_ops
    from workflows.generate import invented_numbers
    from workflows.validator import validate_email

    db, ws = ctx.db, ctx.workspace_id
    if ctx.llm is None:
        raise WorkflowError("no language model is configured for copy writing")
    src = db.get(Asset, int(inputs["source_asset"]))
    if src is None or src.workspace_id != ws:
        raise WorkflowError("the source email no longer exists")
    src_html = assets.asset_html(src)
    kb = compliance.knowledge_base(db, ws)
    persona = next((p for p in service.active_rows(db, ws, "persona") if p.name == inputs.get("persona")), None)
    if persona is None:
        raise WorkflowError(f"persona '{inputs.get('persona')}' is no longer in the knowledge base")

    # claim emphasis: lead with the claims chosen for this persona (moved whole, never reworded)
    lead = [c["claim_id"] for c in plan["claims"]]
    base_html, claim_order = pz.reorder_claims(src_html, lead)
    sections = pz.rewritable_sections(base_html)
    if not sections:
        raise WorkflowError("this email has no tagged headings or body text to rewrite")

    tone = "; ".join(t.value for t in service.active_rows(db, ws, "brand") if t.category == "tone")
    inventory = "\n".join(f'- {s.type} {s.ordinal} of {s.count}: "{s.text[:300]}"' for s in sections)
    claims_in_email = [s.text for s in pz.all_sections(base_html) if s.type == "claim"]
    brief = (
        "Adapt this existing promotional email for a different audience. Change only tone and emphasis.\n"
        f"Product: {inputs.get('product')}\nAudience: {persona.name}; {persona.description}\n"
        f"Audience tone: {persona.tone}\nAudience concerns: {', '.join(persona.concerns)}\n"
        f"Extra guidance: {inputs.get('notes') or '(none)'}\nBrand tone: {tone}\n\n"
        f"Sections you may rewrite (numbered within each kind):\n{inventory}\n\n"
        "The email also contains approved claims, which are fixed and shown verbatim; write copy that frames them, "
        "and do not repeat, reword or add to them:\n" + "\n".join(f"- {c}" for c in claims_in_email) + "\n\n"
        "Rules: keep each section's meaning and facts; make no new factual, efficacy, safety or statistical statements; "
        "use no numbers except ones already in the email; keep lengths similar; return only the sections you actually "
        f"change; do not use these banned phrases: {', '.join(kb.banned_phrases)}. US English."
    )
    original = {(s.type, s.ordinal): s.text for s in sections}
    baseline = {f.message for f in validate_email(src_html, kb).findings if f.severity == "error"}
    env = assets.adapt_env(db, ws)
    apply_errors: list[str] = []

    def render(copy) -> str:
        res = apply_ops(base_html, pz.ops_from_rewrites([r.model_dump() for r in copy.rewrites]), env)
        apply_errors[:] = res.errors
        return res.html

    def check(copy, html: str) -> list[str]:
        problems = list(apply_errors)
        if not copy.rewrites:
            problems.append("No sections were rewritten. Rewrite the sections whose tone or emphasis should change.")
        for r in copy.rewrites:
            old = original.get((r.type, r.ordinal), "")
            if old and len(r.text) > max(60, 2.5 * len(old)):
                problems.append(f"The rewrite of {r.type} {r.ordinal} is much longer than the original; keep the length similar.")
        allowed = [pz.visible_text(src_html), inputs.get("notes", ""), *[c["text"] for c in plan["claims"]]]
        if bad := invented_numbers([r.text for r in copy.rewrites], allowed):
            problems.append(f"Unsupported number(s) in the rewrite: {', '.join(bad)}. Use only numbers already in the email.")
        # only problems this rewrite introduced: an email that was already non-compliant must not block itself
        problems += [f.message for f in validate_email(html, kb).findings if f.severity == "error" and f.message not in baseline]
        return problems

    graph = subgraphs.build_copy_graph(
        ctx.llm, load_agent_md(), brief, render, check, schema=subgraphs.PersonaRewrite
    )
    out = graph.invoke({})
    copy, html, history = out["copy"], out["html"], out["history"]

    root = re.sub(r"( \([^)]*\))+$", "", src.name)
    new = assets.create_asset(
        db, ws, name=f"{root} ({persona.name})", origin="personalize", html=html, session_id=ctx.session_id,
        parent_id=src.id, tag_status=src.tag_status,
        meta={
            "persona": persona.name, "notes": inputs.get("notes"), "rewrites": copy["rewrites"],
            "claim_lead": lead, "claim_order": claim_order, "attempts": len(history),
            "doc_versions": plan["doc_versions"],
        },
    )
    changed = [r for r in copy["rewrites"] if r["text"].strip() != original.get((r["type"], r["ordinal"]), "").strip()]
    report = [f"Changed {len(changed)} section(s) for {persona.name}, in {len(history)} attempt{'s' if len(history) != 1 else ''}"]
    if base_html != src_html:
        report.append(f"Claims reordered to lead with: {', '.join(c for c in claim_order if c in lead)} (wording unchanged)")
    for h in history:
        if h["problems"]:
            report.append(f"Attempt {h['attempt']} failed checks: " + "; ".join(h["problems"]))
    return {
        "workflow": "personalize_email", "id": new.id, "name": new.name, "html": html,
        "has_preview": new.has_preview, "parent_id": src.id, "report": report, "attempts": len(history),
        "persona": persona.name,
    }
