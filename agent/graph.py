"""The agent: a LangGraph graph.

begin_turn -> classify -> gather -> draft_plan -> approval (interrupt) -> execute
           -> validate -> present

The LLM understands language (which swimlane, which input values, which approved claims
fit the message). Code owns everything that must be exact: the input schema (from
skill.md), existence checks against the knowledge base, questions for missing inputs,
plan rendering, and which workflow runs.
"""
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import interrupt
from pydantic import BaseModel, Field as PField

import workflows
from agent import tools
from agent.ops import validate_ops
from agent.skills import Skill, load_agent_md, load_skills
from backend import assets, compliance
from backend.models import Asset
from workflows import WorkflowContext, WorkflowError
from workflows.adapt import apply_ops, describe_op
from workflows import personalize
from workflows.sections import list_sections, short

# Knowledge-base documents each lane needs before it can run.
REQUIRED_DOCS = {
    "generate": ("brand", "persona", "claims", "rules"),
    "personalize": ("brand", "persona", "claims", "rules"),
    "adapt": ("brand", "rules"),
}
DOC_FILES = {"brand": "brand.md", "persona": "persona.md", "claims": "claims.md", "rules": "compliance_rules.md"}
TEMPLATE = "email_600"


class State(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    workspace_id: int
    swimlane: str | None
    inputs: dict[str, str]
    ready: bool
    plan: dict | None
    decision: dict | None
    result: dict | None


# ---- structured LLM outputs -------------------------------------------------------

class Classification(BaseModel):
    swimlane: Literal["generate", "adapt", "personalize", "unclear"]
    reply: str = PField(default="", description="If unclear: a short clarifying question or note. Otherwise empty.")


class FieldValue(BaseModel):
    name: str
    value: str


class GatherResult(BaseModel):
    inputs: list[FieldValue] = PField(
        description="Every input value known so far: previously collected values plus anything new in the latest messages."
    )


SectionType = Literal["preheader", "utility", "logo", "header", "subheader", "body", "claim", "cta", "isi", "footer", "image"]


class AdaptOp(BaseModel):
    type: Literal["swap_logo", "change_color", "replace_isi", "replace_text", "replace_section_text", "recolor_section"]
    target_type: SectionType | None = PField(default=None, description="replace_section_text / recolor_section / swap_logo: which kind of section the user means")
    target_ordinal: int | None = PField(default=None, description="1-based position among sections of that type (the 2nd subheader = 2). Leave empty if the user did not say which one")
    text: str | None = PField(default=None, description="replace_section_text: the new text for the whole section")
    property: Literal["background", "text"] | None = PField(default=None, description="recolor_section: background color or text color. Leave empty if the user did not say")
    logo: str | None = PField(default=None, description="swap_logo: filename of an uploaded logo, or 'brand' for the brand's own logo")
    color: str | None = PField(default=None, description="change_color: the color to replace, as a hex value or a brand color role (primary, secondary, accent, text)")
    to_color: str | None = PField(default=None, description="change_color: the new color as a hex value (translate color names to a hex)")
    find: str | None = PField(default=None, description="replace_text: exact text to find")
    replace: str | None = PField(default=None, description="replace_text: replacement text")


class AdaptOps(BaseModel):
    ops: list[AdaptOp] = PField(description="One entry per mechanical change the user asked for")
    unsupported: list[str] = PField(
        default_factory=list,
        description="Every requested change you could NOT express as an operation above, quoted briefly",
    )


class ClaimSelection(BaseModel):
    claim_ids: list[str] = PField(description="Ids of the approved claims that best support the message")


def _text(m: AnyMessage) -> str:
    return m.content if isinstance(m.content, str) else str(m.content)


def build_graph(llm, checkpointer, session_factory):
    skills = load_skills()
    system = load_agent_md()

    def ask(schema, instruction: str, messages: list[AnyMessage]):
        prompt = [SystemMessage(f"{system}\n\n## Current task\n{instruction}"), *messages]
        # json_schema = native structured output; forced tool calling is not reliable on this model
        return llm.with_structured_output(schema, method="json_schema").invoke(prompt)

    # ---- nodes --------------------------------------------------------------------

    def begin_turn(state: State):
        """A finished request is closed; a new message starts a fresh one."""
        if state.get("result") is not None:
            return {"swimlane": None, "inputs": {}, "ready": False, "plan": None, "result": None, "decision": None}
        return {}

    def classify(state: State):
        if state.get("swimlane"):
            return {}
        lanes = "\n".join(f"- {s.swimlane}: {s.when}" for s in skills.values())
        out = ask(
            Classification,
            "Decide which swimlane the user's request belongs to, using the whole conversation.\n"
            f"{lanes}\n- unclear: not an email generation/edit request, or cannot tell yet. "
            "Put a brief helpful reply in `reply`.",
            state["messages"],
        )
        if out.swimlane == "unclear":
            reply = out.reply or "I can generate a new email, make mechanical edits to one, or adapt one for a persona. What would you like to do?"
            return {"swimlane": None, "messages": [AIMessage(reply)]}
        return {"swimlane": out.swimlane}

    def _knowledge(db, ws: int) -> dict:
        return {
            "persona": [p.name for p in tools.list_personas(db, ws)],
            "product": tools.list_products(db, ws),
        }

    def gather(state: State):
        skill: Skill = skills[state["swimlane"]]
        ws = state["workspace_id"]
        with session_factory() as db:
            have = tools.active_versions(db, ws)
            missing_docs = [d for d in REQUIRED_DOCS[skill.swimlane] if d not in have]
            if missing_docs:
                files = ", ".join(DOC_FILES[d] for d in missing_docs)
                msg = f"Before I can do that, this workspace needs these documents uploaded (Knowledge base page): {files}."
                return {"swimlane": None, "ready": False, "messages": [AIMessage(msg)]}
            known = _knowledge(db, ws)
            needs_source = "source_asset" in {f.name for f in skill.inputs}
            asset_opts = [f"#{a.id} {a.name}" for a in assets.list_assets(db, ws)[:8]] if needs_source else []
            if needs_source and not asset_opts:
                msg = (
                    "There is no email to work on yet. Upload an HTML email or create the sample email "
                    "on the Assets page, then ask me again."
                )
                return {"swimlane": None, "ready": False, "messages": [AIMessage(msg)]}

        spec = "\n".join(
            f"- {f.name} ({'required' if f.required else 'optional'}): {f.description}" for f in skill.inputs
        )
        shown = {**known, **({"source_asset": ["latest", *asset_opts]} if needs_source else {})}
        options = "\n".join(f"- {k} options: {', '.join(v) or '(none)'}" for k, v in shown.items() if k in {f.name for f in skill.inputs})
        collected = "\n".join(f"- {k}: {v}" for k, v in state.get("inputs", {}).items()) or "(nothing yet)"
        out = ask(
            GatherResult,
            f"The user is using the '{skill.label}' lane. Extract these inputs from the conversation:\n{spec}\n\n"
            f"Valid options from the knowledge base (use these exact names when the user refers to them):\n{options}\n\n"
            f"Already collected:\n{collected}\n\n"
            "Return ALL known values (already collected + anything new or corrected in the latest messages; "
            "the latest message wins). Do not guess values the user has not given. Omit unknown inputs.",
            state["messages"],
        )

        valid = {f.name for f in skill.inputs}
        inputs = {fv.name: fv.value.strip() for fv in out.inputs if fv.name in valid and fv.value.strip()}
        problems: list[str] = []
        for key, names in known.items():
            if key not in valid:
                continue
            rejected = False
            if key in inputs:
                match = next((n for n in names if n.lower() == inputs[key].lower()), None)
                if match:
                    inputs[key] = match
                else:
                    problems.append(f"I couldn't find {key} '{inputs.pop(key)}' in the knowledge base.")
                    rejected = True
            if key not in inputs and len(names) == 1 and not rejected:
                inputs[key] = names[0]  # only one choice, no need to ask

        if needs_source and "source_asset" in inputs:
            with session_factory() as db:
                found = assets.resolve_asset(db, ws, inputs["source_asset"])
            if found:
                inputs["source_asset"] = str(found.id)
            else:
                problems.append(f"I couldn't find an email called '{inputs.pop('source_asset')}'.")

        missing = [f for f in skill.required if f.name not in inputs]
        if not missing and not problems:
            return {"inputs": inputs, "ready": True}

        lines = [*problems, f"I'll handle this with the **{skill.label}** lane. I still need:"]
        for f in missing:
            opt = f" Options: {', '.join(shown[f.name])}." if shown.get(f.name) else ""
            lines.append(f"- **{f.name}**: {f.description}.{opt}")
        return {"inputs": inputs, "ready": False, "messages": [AIMessage("\n".join(lines))]}

    def _inventory(html: str) -> str:
        secs = list_sections(html)
        if not secs:
            return "(this email has no tagged sections)"
        return "\n".join(f'- {s.type} {s.ordinal} of {s.count}: "{short(s.text, 50)}"' for s in secs[:40])

    def _plan_ops(db, ws, inputs, src, messages):
        brand = tools.get_brand(db, ws)
        colors = brand.get("color", {})
        logos = assets.list_logos(ws)
        brand_logo = brand.get("logo", {}).get("path")
        roles = ", ".join(f"{k}={v}" for k, v in colors.items())
        out = ask(
            AdaptOps,
            "Translate the user's requested changes into mechanical operations. Only use operations "
            "you are sure about. List anything you cannot express in `unsupported`; never drop a request silently. "
            "The email to edit has already been chosen, so do not list the choice of email (or 'email #N') as a request.\n"
            "To edit one specific part of the email, use replace_section_text / recolor_section (or swap_logo) with "
            "target_type and target_ordinal. If the user did not say WHICH one when there are several of that type, "
            "leave target_ordinal empty (you will be asked to clarify). Claims and the ISI can never be edited freehand.\n"
            f"The email's tagged sections, in order:\n{_inventory(assets.asset_html(src))}\n"
            "In the standard email template the colors are used like this: primary = headline banner, wordmark and "
            "ISI border; accent = the call-to-action button; secondary = page and ISI background; text = body text.\n"
            f"Uploaded logos: {', '.join(logos) or '(none)'} (brand logo: {brand_logo}).\n"
            f"Brand colors: {roles}.\nRequested changes: {inputs.get('changes', '')}",
            messages,
        )
        ops, problems = validate_ops([o.model_dump() for o in out.ops], colors, logos, brand_logo)
        skipped = [u.strip() for u in out.unsupported if u.strip()]
        preview: list[str] = []
        ambiguous = False
        if ops and not problems:
            dry = apply_ops(assets.asset_html(src), ops, assets.adapt_env(db, ws))
            problems += [e if e.endswith((".", "?", "!")) else f"{e}." for e in dry.errors]
            preview = [f"{describe_op(r.op)} ({r.detail})" for r in dry.reports if r.ok]
            ambiguous = dry.ambiguous
        return ops, preview, problems, skipped, ambiguous

    def draft_plan(state: State):
        skill = skills[state["swimlane"]]
        ws, inputs = state["workspace_id"], state["inputs"]
        claims: list[dict] = []
        ops: list[dict] = []
        rewritable: dict[str, int] = {}
        ops_preview: list[str] = []
        skipped: list[str] = []
        source = None
        with session_factory() as db:
            versions = tools.active_versions(db, ws)
            if "source_asset" in inputs:
                src = db.get(Asset, int(inputs["source_asset"]))
                source = {"id": src.id, "name": src.name}
            if skill.swimlane == "personalize":
                rewritable = personalize.summary(assets.asset_html(src))
                if not rewritable:
                    msg = (
                        "That email has no tagged headings or body text I can rewrite. Tag its sections on the "
                        "Assets page (the Sections tab, or auto-detect), then ask me again."
                    )
                    return {"plan": None, "ready": False, "messages": [AIMessage(msg)]}
            if skill.swimlane == "adapt":
                ops, ops_preview, problems, skipped, ambiguous = _plan_ops(db, ws, inputs, src, state["messages"])
                if problems or not ops:
                    lines = problems or ["I couldn't turn that into a specific change."]
                    lines += [f"I also can't do this one: {u}." for u in skipped]
                    lines.append(
                        "Tell me which one (for example, 'the 2nd one')." if ambiguous else
                        "Please tell me exactly what to change (for example: swap the logo, change a color, update the ISI, or replace specific text)."
                    )
                    return {"plan": None, "ready": False, "messages": [AIMessage("\n".join(lines))]}
            if skill.swimlane in ("generate", "personalize"):
                pool = {c.claim_id: c for c in tools.list_claims(db, ws, inputs.get("product"))}
                listing = "\n".join(f'- {cid}: "{c.text}"' for cid, c in pool.items())
                persona = tools.get_persona(db, ws, inputs["persona"]) if inputs.get("persona") else None
                audience = (
                    f"\nAudience: {persona.name}; {persona.description}. Concerns: {', '.join(persona.concerns)}"
                    if persona else ""
                )
                pick = ask(
                    ClaimSelection,
                    "Choose which approved claims to use in the email, most relevant first. Only choose from this list; "
                    f"never alter wording.\nClaims:\n{listing}\n\nRequest inputs: {inputs}{audience}",
                    state["messages"],
                )
                chosen = [cid for cid in dict.fromkeys(pick.claim_ids) if cid in pool] or list(pool)
                claims = [{"claim_id": cid, "text": pool[cid].text} for cid in chosen]

        plan = {
            "swimlane": skill.swimlane,
            "label": skill.label,
            "workflow": skill.workflow,
            "template": TEMPLATE,
            "inputs": inputs,
            "claims": claims,
            "source": source,
            "tag_status": src.tag_status if source else None,
            "ops": ops,
            "ops_preview": ops_preview,
            "rewritable": rewritable,
            "skipped": skipped,
            "doc_versions": {d: v for d, v in versions.items() if d in REQUIRED_DOCS[skill.swimlane]},
        }
        return {"plan": plan, "messages": [AIMessage(render_plan(plan))]}

    def approval(state: State):
        decision = interrupt({"plan": state["plan"]})
        kind = decision.get("decision")
        if kind == "approve":
            return {"decision": decision}
        if kind == "reject":
            return {
                "decision": decision, "swimlane": None, "inputs": {}, "ready": False, "plan": None,
                "messages": [AIMessage("Okay, I've cancelled that plan. Nothing was produced.")],
            }
        feedback = (decision.get("feedback") or "").strip() or "Please revise the plan."
        return {"decision": decision, "plan": None, "ready": False, "messages": [HumanMessage(feedback)]}

    def execute(state: State, config: RunnableConfig):
        plan = state["plan"]
        with session_factory() as db:
            ctx = WorkflowContext(db, state["workspace_id"], config["configurable"].get("thread_id"), llm)
            try:
                asset = workflows.WORKFLOWS[plan["workflow"]](ctx, plan["inputs"], plan)
            except WorkflowError as e:
                return {"result": {"asset": None, "validation": None, "error": str(e), "plan": plan}}
        return {"result": {"asset": asset, "validation": None, "plan": plan}}

    def validate(state: State):
        result = dict(state["result"])
        asset = result.get("asset")
        if asset is None:
            return {"result": result}
        if not asset.get("id"):  # placeholder workflows produce no stored email
            result["validation"] = {"passed": None, "findings": [], "skipped": True}
            return {"result": result}
        with session_factory() as db:
            v = compliance.validate_asset(db, db.get(Asset, asset["id"]))
            result["validation"] = {"passed": v.passed, "findings": v.findings, "skipped": False}
        return {"result": result}

    def present(state: State):
        r = state["result"]
        if r.get("error"):
            return {"messages": [AIMessage(f"I couldn't complete that: {r['error']}. Nothing was changed.")]}
        v, asset = r["validation"], r["asset"]
        if v["skipped"]:
            return {"messages": [AIMessage("Done (placeholder workflow: real output arrives in a later step). Nothing was validated.")]}
        errors = [f for f in v["findings"] if f["severity"] == "error"]
        warnings = [f for f in v["findings"] if f["severity"] == "warning"]
        verb = {
            "generate_email": "generated",
            "personalize_email": f"personalized the email for {asset.get('persona', 'the persona')} as",
        }.get(asset.get("workflow"), "saved the edited email as")
        head = f"Done. I {verb} **{asset['name']}** (#{asset['id']})."
        if errors:
            lines = [f"{head} **Compliance validation failed** with {len(errors)} error(s), so it can't be approved yet:"]
            lines += [f"- {f['message']}" for f in errors]
            lines.append("Tell me what to fix, for example to update the ISI block.")
        else:
            note = f" {len(warnings)} warning(s) for the reviewer." if warnings else ""
            lines = [f"{head} Compliance validation passed.{note} It now needs reviewer approval before it can be exported."]
        return {"messages": [AIMessage("\n".join(lines))]}

    # ---- routing ------------------------------------------------------------------

    def after_classify(state: State):
        return "gather" if state.get("swimlane") else END

    def after_gather(state: State):
        return "draft_plan" if state.get("ready") else END

    def after_plan(state: State):
        return "approval" if state.get("plan") else END

    def after_approval(state: State):
        return {"approve": "execute", "edit": "gather"}.get((state.get("decision") or {}).get("decision"), END)

    g = StateGraph(State)
    for name, fn in [
        ("begin_turn", begin_turn), ("classify", classify), ("gather", gather), ("draft_plan", draft_plan),
        ("approval", approval), ("execute", execute), ("validate", validate), ("present", present),
    ]:
        g.add_node(name, fn)
    g.add_edge(START, "begin_turn")
    g.add_edge("begin_turn", "classify")
    g.add_conditional_edges("classify", after_classify, ["gather", END])
    g.add_conditional_edges("gather", after_gather, ["draft_plan", END])
    g.add_conditional_edges("draft_plan", after_plan, ["approval", END])
    g.add_conditional_edges("approval", after_approval, ["execute", "gather", END])
    g.add_edge("execute", "validate")
    g.add_edge("validate", "present")
    g.add_edge("present", END)
    return g.compile(checkpointer=checkpointer)


def render_plan(plan: dict) -> str:
    lines = [
        f"**Execution plan**: {plan['label']}",
        f"Workflow `{plan['workflow']}` on template `{plan['template']}`.",
        "",
        "**Inputs**",
        *[
            f"- {k}: #{plan['source']['id']} {plan['source']['name']}" if k == "source_asset" and plan.get("source") else f"- {k}: {v}"
            for k, v in plan["inputs"].items()
        ],
    ]
    if plan.get("ops_preview"):
        lines += ["", "**Changes (nothing else will be touched). Check these are the right sections:**", *[f"- {o}" for o in plan["ops_preview"]]]
        if plan.get("tag_status") == "suggested":
            lines += ["", "_Note: this email's sections were auto-detected and not yet checked by a person. If a target looks wrong, correct the tags on the Assets page._"]
    if plan["claims"]:
        lines += ["", "**Approved claims to be used (verbatim)**"]
        lines += [f'- {c["claim_id"]}: "{c["text"]}"' for c in plan["claims"]]
    if plan.get("rewritable"):
        parts = ", ".join(f"{t} ({n})" for t, n in plan["rewritable"].items())
        lines += [
            "", f"**May be rewritten for this persona:** {parts}.",
            "**Never changed:** claim wording, the ISI, the footer, logos and images. "
            "Only tone and emphasis change" + (
                ". If the email has a single claims list, the claims above are moved to the front (wording untouched)."
                if plan["claims"] else "."
            ),
        ]
    if plan.get("skipped"):
        lines += ["", "**Not included** (I can't do these as mechanical edits):", *[f"- {u}" for u in plan["skipped"]]]
    versions = ", ".join(f"{d} v{v}" for d, v in plan["doc_versions"].items())
    lines += ["", f"**Knowledge applied**: {versions}", "", "Approve to run it, or tell me what to change."]
    return "\n".join(lines)
