# Pharma Content Generation Chatbot: Technical Writeup (Draft v0.2)

> Scope: a **local portfolio project**, **v1 covers promotional emails only, with one template** (banners and posts are deferred),, not production. Claude models for the LLM, local file storage, Postgres for data, LangGraph for the agent loop, US English only, HTML-only inputs, templates supplied by us, claims used **verbatim**.

## 1. What we're building

A chat-driven tool that generates, edits and fixes pharma marketing content: **promotional emails, banners and social posts**. The user chats with an agent. The agent gathers the inputs it needs, proposes an execution plan, and, only after the user approves, runs a **deterministic backend workflow** to produce the asset.

The LLM does the understanding and planning. The backend workflows do the producing. Output is therefore repeatable, brand-safe and auditable, which matters in a regulated industry.

---

## 2. The user journey

### 2.1 Arrival and workspace setup
A user logs in and lands in a **workspace** (one per brand; the tool is multi-brand and multi-user). A new workspace is empty, so the first screen is an onboarding/upload step.

### 2.2 Uploading the knowledge base
The user uploads the documents that define the brand's rules:

| File | Purpose |
|---|---|
| `brand.md` | Voice, tone, colors, fonts, logo usage, layout guidance |
| `persona.md` | Audience personas (e.g. HCP cardiologist, patient/caregiver): needs, language, channels |
| `claims.md` | Approved claims, references, and the exact wording allowed |
| `compliance_rules.md` | ISI/fair-balance requirements, banned phrases, mandatory disclaimers, regional rules |
| *others* | Product monographs, past approved assets, templates, logo files |

On upload, the backend:
1. Saves the raw file to local disk (`./data/workspaces/<workspace_id>/docs/`), with its path and metadata in Postgres.
2. Parses and validates it. Markdown files get a light schema check (e.g. `claims.md` must have claim IDs and references), and the user is told what's missing.
3. Creates a **versioned** record. Every later generation pins to the document versions it used.
4. Converts the content into **structured tables** (brand tokens, personas, claims, rules). There is no embedding or vector store. The agent reads this data through exact-lookup tools, because claims must be verbatim and similarity search is the wrong tool for that.

### 2.3 Starting a chat
The user opens a chat session and states a goal, e.g. *"Create a launch email for Product X aimed at cardiologists"* or *"Swap the logo on this banner for the new one"*.

### 2.4 The agentic loop
For each message the agent runs a loop:

1. **Understand intent.** It classifies the request into a swimlane (section 4), or asks if it's ambiguous.
2. **Gather inputs.** It reads the swimlane's `skill.md`, which lists the required and optional inputs. It pulls what it can from the knowledge base and the conversation, and asks the user only for what's missing (product, asset type, persona, CTA, source asset and so on).
3. **Draft an execution plan.** The plan is shown in plain language: swimlane, workflow, resolved inputs, claims to be used, template, and the documents and versions applied.
4. **User approval gate.** Nothing runs until the user approves. They can edit inputs and the plan is regenerated.
5. **Execute.** The agent calls the matching backend workflow with the validated inputs.
6. **Validate.** The compliance validator checks the output (section 6).
7. **Present and iterate.** The user sees the asset with its validation report. They can request changes, which start the loop again, usually in the Mechanical adaptation lane. A reviewer then approves or rejects before export.

---

## 3. Architecture

```
 Streamlit UI (chat + upload + preview + approval)
            |
        FastAPI (workspaces, sessions, no real auth)
            |
   +--------+----------------------------+
   |  Agent runtime (LangGraph graph)    |
   |  agent.md (system behavior)         |
   |  skills/*.md (per-swimlane)         |
   |  tools: get_brand, get_persona,     |
   |         list_claims, get_rules,     |
   |         propose_plan, run_workflow, |
   |         validate, ask_user          |
   +--------+----------------------------+
            |
   Deterministic workflows (plain Python; retry loops as subgraphs)
     - generate_from_scratch
     - mechanical_adaptation
     - persona_personalization
     - template renderer (HTML/MJML/SVG -> PNG/HTML)
     - compliance validator
            |
   Postgres                    Local disk (./data: docs, assets)
```

### 3.0 Orchestration: LangGraph for the agent, plain Python for workflows
- The **agent loop is a LangGraph graph**. Nodes: classify intent, gather inputs, draft plan, approval, execute, validate, present. The approval gate uses `interrupt()`: the graph pauses at the plan and resumes when the user approves or edits it.
- State is saved with the **Postgres checkpointer** (`PostgresSaver`), keyed by session (`thread_id`). This provides short-term memory and resume-mid-task.
- **Workflows are plain Python functions** with typed inputs and outputs. The linear ones (mechanical adaptation, rendering, validation) stay plain code, which is easier to test and keeps them deterministic.
- **Exception:** generate and personalize have a write-copy, validate, retry loop (regenerate with the validator's findings, max N attempts). Each is a small LangGraph subgraph with a conditional edge. The validator itself is plain Python.
- The LLM is Claude Sonnet via `langchain-anthropic`.

### 3.1 `agent.md`
The agent's constitution: role, tone, the rule that nothing executes without plan approval, that it never invents claims, how to ask clarifying questions, and how to choose a swimlane. It is loaded as the system prompt.

### 3.2 `skill.md` (one per swimlane)
Each skill file tells the agent:
- when this swimlane applies,
- the **input schema** (required and optional fields),
- which workflow to call and with what parameters,
- how to word the plan for approval,
- common failure modes.

Skills are loaded on demand once the swimlane is chosen, which keeps the context small.

### 3.3 Backend workflows
Workflows are ordinary code with typed inputs and outputs, called in-process by the agent (no job queue for a local project). The LLM may be called *inside* a workflow step (for example to write copy), but only with constrained inputs, such as approved claims and a fixed template slot, and the output is validated before moving on. The agent chooses which workflow to run and with which inputs. It does not improvise the steps.

---

## 4. The three swimlanes

### Lane 1: Generate from scratch
*New email, banner or post.*
Inputs: product, asset type, objective, persona, key message, CTA.
Flow: select template, then select approved claims relevant to the message, then generate copy constrained to those claims, then render, then validate.

### Lane 2: Mechanical adaptation
*Edit or fix an existing asset with no creative rewrite.* Examples: logo swap, color or font update, resizing banners to new dimensions, updating a footer or ISI block, fixing a flagged phrase.
Flow: ingest the existing asset (HTML or template-backed source), apply a deterministic transform, re-render, diff against the original, validate. This lane uses little or no LLM content generation, which is why it is the safest.

### Lane 3: Persona personalization
*Adapt an existing or approved asset for a different persona.*
Flow: load the persona, then re-select the claims emphasis and rewrite copy within claim boundaries (tone, reading level, channel conventions), then re-render and validate. Claim text itself is never altered, only selected and ordered.

---

## 5. Memory

### 5.1 Short-term memory (in scope)
- Handled by the LangGraph **Postgres checkpointer**, keyed by session `thread_id`. It stores message history and the graph state, which holds the current goal, chosen swimlane, resolved inputs, pending plan and latest asset. This lets the agent resume mid-task, including after an approval interrupt.
- We keep a lightweight `sessions` table (id, workspace, title, timestamps) for listing chats in the UI. The checkpointer owns the content.
- When history gets long, older turns are summarized and the structured state is kept intact.

### 5.2 Long-term memory (deprioritized for now)
Deferred to a later phase. Provisions so we can add it without a rewrite:
- Every table is keyed by `workspace_id` and `user_id`.
- Approved assets and user feedback are already stored (needed for audit), so they can later feed memory.
- A `memories` table placeholder is reserved, but nothing writes to it in v1.

Brand knowledge is not long-term memory. It lives in the structured knowledge base (section 2.2) and is read via lookup tools.

---

## 6. Compliance validation and approval

Compliance is a hard gate. It has two layers:

1. **Automated validator** (deterministic code):
   - every claim in the output is an approved claim inserted **verbatim** by ID (exact string match; copy around the claim may be generated, the claim text may not),
   - required elements are present (ISI, fair balance, disclaimers, references),
   - banned phrases are absent,
   - brand checks (correct logo, colors, fonts).
   It produces a pass/fail report with specific findings.
2. **Human approval.** Inside the Streamlit UI, a reviewer sees the asset next to the validation report and clicks approve or reject. Export is only enabled after approval. This is a simple status flag, with no roles or external MLR integration.

Each generation stores the inputs, document versions, plan, workflow version, validator results and approver, which is the audit trail.

---

## 7. Data model (Postgres)

- `workspaces`, `users` (simple, no real auth)
- `documents` (type, filename, storage path), `document_versions` (content, parsed JSON, version, status)
- `brand_tokens`, `personas`, `rules`, and `claims` (claim_id, verbatim text, references, product), all parsed from the uploaded md files
- `sessions` (id, workspace, title), plus the LangGraph checkpoint tables created by `PostgresSaver`
- `plans` (inputs JSON, status: proposed/approved/rejected, approved_by)
- `assets` (type, lane, storage path, parent_asset_id for edit lineage, doc_version_ids)
- `validations` (asset_id, findings, passed)
- `reviews` (asset_id, reviewer, decision, comments)
- `memories` (reserved, unused in v1)

Everything is scoped by `workspace_id`. File paths are stored relative to `./data`.

---

## 8. Rendering approach

Banners, posts and emails come from **templates** (MJML or HTML for email, HTML or SVG for banners and posts). Workflows fill slots (headline, body, claim blocks, CTA, logo, colors) and a renderer exports final files (headless-browser PNG for banners and posts, inlined HTML for email). This keeps layout and branding deterministic and makes mechanical adaptation straightforward. Templates come from us (a small built-in set). AI image generation is out of scope. Edits accept **HTML only** as input, which is why mechanical adaptation can be a plain DOM transform (BeautifulSoup/lxml).

---

## 9. Suggested build order

1. Workspaces, document upload, parsing into structured tables, versioning.
2. LangGraph agent graph with Postgres checkpointer (short-term memory), `agent.md`, and the approval `interrupt()`, tested with stub workflows.
3. One end-to-end lane (Mechanical adaptation is the simplest): plan, approve, execute, render.
4. Compliance validator and the approve/reject flow.
   A Streamlit UI is built alongside steps 1 to 4 to test each piece.
5. Lane 1 (Generate from scratch).
6. Lane 3 (Persona personalization).
7. Long-term memory.

---

## 10. Decisions made

- LLM: Claude Sonnet (`claude-sonnet-5-5`) for everything: agent loop, planning and copy generation. Kept in one config setting.
- Storage: local disk plus Postgres. No vector DB.
- Templates: ours, built-in.
- Input format for edits: HTML only.
- Review: in-tool approve/reject flag.
- Language and region: US English.
- Claims: verbatim only.
- UI: Streamlit (frontend) + FastAPI (backend).

## 11. v1 scope: emails, one template

- **Asset type:** promotional email only. Banners and posts are deferred, and the architecture stays asset-type agnostic.
- **Template:** one built-in HTML email template, **600px wide** (the email industry standard), single column, table-based layout with inline CSS so it renders in major email clients.
- **Slots:** logo, preheader, headline, body copy, verbatim claim block(s), CTA button, ISI/fair-balance block, footer with unsubscribe and disclaimer text, and brand colors and fonts from `brand_tokens`.
- **Rendering:** the filled HTML is the deliverable, and a Playwright screenshot is used only for the preview.
- **Lanes in v1:** all three still apply to email (generate, mechanical edits such as logo, colors and ISI swap, and persona rewrite), but all share the same template and slots.

## 12. Still open

- None right now.

---

## 13. Project structure

```
chatbot/
├── README.md
├── pyproject.toml               # fastapi, sqlalchemy, alembic, langgraph, langchain-anthropic,
│                                #   langgraph-checkpoint-postgres, beautifulsoup4, playwright, streamlit
├── .env.example                 # ANTHROPIC_API_KEY, DATABASE_URL, MODEL=claude-sonnet-5-5
├── docker-compose.yml           # Postgres only
├── data/                        # local file storage (gitignored)
│   └── workspaces/<id>/{docs,assets}/
├── agent/
│   ├── agent.md                 # system behavior
│   ├── skills/
│   │   ├── generate.skill.md
│   │   ├── adapt.skill.md
│   │   └── personalize.skill.md
│   ├── graph.py                 # LangGraph agent graph + checkpointer + interrupt
│   ├── subgraphs.py             # write-copy / validate / retry loops (generate, personalize)
│   └── tools.py                 # get_brand, get_persona, list_claims, get_rules,
│                                #   propose_plan, run_workflow, validate, ask_user
├── backend/
│   ├── main.py                  # FastAPI app
│   ├── db.py                    # SQLAlchemy engine and models
│   ├── migrations/              # Alembic
│   ├── routers/                 # workspaces, documents, sessions, plans, assets, reviews
│   ├── ingestion/               # md parsers: brand, persona, claims, rules
│   └── storage.py               # local file helpers
├── workflows/
│   ├── generate_email.py
│   ├── adapt_email.py           # logo, colors, ISI swap (BeautifulSoup)
│   ├── personalize_email.py
│   ├── render.py                # template fill and Playwright preview
│   ├── validator.py             # verbatim claims, required blocks, banned phrases
│   └── templates/email_600.html
├── ui/
│   └── app.py                   # Streamlit
├── samples/                     # example brand.md, persona.md, claims.md, compliance_rules.md
└── tests/
```

---

## 14. Step 1 build plan: workspaces, upload, parsing, versioning

**Goal:** a user can create a workspace, upload the four md files, and see them parsed into structured data. No agent yet.

### 14.1 Tasks
1. **Scaffolding.** Python project, `docker-compose.yml` with Postgres, `.env`, FastAPI skeleton with a health endpoint.
2. **DB schema and migrations (Alembic)** for: `workspaces`, `users`, `documents`, `document_versions`, `brand_tokens`, `personas`, `rules`, `claims`. The remaining tables (sessions, plans, assets and so on) come in later steps.
3. **Local storage helper.** Save uploads to `data/workspaces/<id>/docs/<type>/v<N>_<filename>`, with relative paths in Postgres.
4. **Sample docs.** Write realistic fake-brand `brand.md`, `persona.md`, `claims.md`, `compliance_rules.md` that fix the md formats the parsers expect.
5. **Parsers** (one per doc type, pure functions, with unit tests):
   - `claims.md` gives `claim_id`, verbatim text, reference, product. Reject duplicate IDs and missing references.
   - `brand.md` gives colors, fonts, logo path, tone notes.
   - `persona.md` gives name, description, language and tone guidance, key concerns.
   - `compliance_rules.md` gives required blocks (ISI, disclaimer), banned phrases, mandatory text.
6. **Upload endpoint** `POST /workspaces/{id}/documents`: store the file, parse it, and on success create a new `document_version` marked active and replace the structured rows for that doc type. On failure return specific parse errors and keep the previous active version.
7. **Read endpoints** to list documents and versions and to fetch parsed brand, personas, claims and rules.
8. **Minimal Streamlit page:** create or select a workspace, upload the four files, and show parsed results and any errors.

### 14.2 Definition of done
- Uploading the sample files populates all four structured tables.
- Re-uploading a changed `claims.md` creates version 2 and keeps version 1 in history.
- A malformed `claims.md` is rejected with a clear error and the old version stays active.
- Parser unit tests pass.

### 14.3 Later steps (from section 9)
2. The LangGraph agent with checkpointer and approval interrupt, then the first lane (mechanical adaptation), then the validator and approval, then generate, personalize, and long-term memory last.


---

## 15. Step 3 as built: mechanical adaptation

- **Template:** `workflows/templates/email_600.html` (Jinja, autoescaped). Slots are tagged `data-slot` (logo, headline, body, claims, cta, isi, footer) and each claim is tagged `data-claim-id`.
- **Assets:** an `assets` table stores each email (HTML on disk, optional PNG preview). Edits create a child asset (`parent_asset_id`) and never modify the source. "latest" means the most recent asset in the workspace, so edits chain. Emails come from an HTML upload or a sample built from the knowledge base.
- **Logos:** uploaded per workspace (`png/jpg/gif/svg`, 1 MB) and embedded as data URIs.
- **Ops:** `swap_logo`, `change_color`, `replace_isi`, `replace_text` (`workflows/adapt.py`, pure functions, no LLM). Text inside approved claims is never edited.
- **Agent role:** the model only *proposes* ops (plus anything it cannot express, shown under "Not included"). `agent/ops.py` validates them against the workspace's logos and brand colors, and the plan step **dry-runs** them on the real email, so the plan shows exact counts ("3 occurrences") and impossible ops are caught before approval.
- **Preview:** Playwright screenshot with JavaScript and network disabled, since the HTML may be uploaded or model-influenced. Uploaded HTML is served as `text/plain`.


---

## 16. Step 4 as built: compliance validation and review

**Validator** (`workflows/validator.py`, deterministic, no LLM; same email and knowledge base always give the same report).

| Severity | Check |
|---|---|
| error | A claim tagged `data-claim-id` is unknown or not verbatim (whitespace-insensitive, otherwise exact) |
| error | Untagged text that is a near match (>= 0.85 similarity) to an approved claim but not identical |
| error | A required block (ISI, disclaimer) is missing, or present but altered |
| error | Mandatory text is missing |
| error | A banned phrase appears in visible text, title or preheader (whole-word, case-insensitive: "secure" does not trip "cure") |
| warning | Off-brand color or font, a logo that is not one of the workspace's uploads, no logo, no unsubscribe link, no approved claims |

Errors block approval. Warnings are shown to the reviewer. The model never decides whether safety information appears: the ISI check is enforced here.

**Review flow**
- Every stored email gets a validation on creation (upload, sample, or workflow output). Results are kept in `validations` and decisions in `reviews`, which together with `assets.meta` (ops, document versions) form the audit trail.
- `assets.status`: `pending_review` -> `approved` or `rejected`. An edit creates a new asset and never changes the approved original.
- **Approving re-validates against the knowledge base as it is now.** An email checked against an old ISI cannot be approved after the rules change. Rejecting is always allowed.
- `GET /assets/{id}/export` returns 409 unless the asset is approved, and then serves it as an attachment.
- The chat reports failures in plain language (for example "the required ISI block is missing") so the user can ask for a fix.
- Reviewer identity is a free-text name; there is no authentication.

**Defense in depth.** `agent.md` makes the agent refuse to put unapproved claims into content (seen live: it declined "Guaranteed cure" on a button). The validator is the backstop for anything that gets past that, for example an uploaded non-compliant email.


---

## 17. Step 5 as built: Generate from scratch

**What the model writes, and what it cannot touch**
- The model writes only `subject`, `preheader`, `headline`, `body` (2 to 3 short paragraphs) and `cta_text` (`EmailCopy`).
- Approved claims are chosen at plan time (and shown to the user), then inserted into the template **by ID, verbatim**. The model is told to frame them, not repeat or reword them.
- Brand, ISI, footer and logo come from the knowledge base, never from the model. The CTA URL comes from the user's request if it contains one, otherwise a placeholder.

**Write / check / retry loop** (`agent/subgraphs.py`, a LangGraph subgraph with a conditional edge): `write_copy -> check -> (pass or 3 attempts: end) | (fail: write_copy with the findings)`. The loop is generic: rendering and checking are injected functions.

The check step combines:
1. The full compliance validator on the rendered HTML (banned phrases, required blocks, claims verbatim, and so on).
2. A numeric rule: any number in the model's copy must appear in an approved claim or in the user's own input. This is how invented statistics are caught, since they cannot be detected by comparing against the claims list.
3. Basic sanity (non-empty body).

If all 3 attempts fail, the last draft is **still saved** with its failing validation, so the user can see why. It can never be approved.

**Validator hole found and fixed while building this.** A reworded claim placed next to the verbatim claim passed, because near-miss detection was skipped for any claim ID that was also present. It now runs for every claim, excluding blocks exactly equal to the claim.

**Structured output.** All structured calls use `method="json_schema"` (native structured output). Forced tool calling is not reliable on `claude-sonnet-5-5`: it failed on the longer copy-writing prompt when the model answered in plain text instead of calling the tool.

**Known limits.** The validator checks claims by text similarity, so a heavily reworded claim that is under 85% similar can slip through, and a soft statement that is not a number or claim-shaped (for example an implied indication such as "designed for patients who need additional LDL-C lowering") is not detected. That is what the human reviewer is for. Persona personalization (step 6) is still a placeholder.


---

## 18. Section tagging and targeted edits

**Why.** Edits used to find their target by luck (first logo, a color replaced everywhere). Now every part of an email can be named, counted, and confirmed.

**Tags live in the HTML** as `data-slot="<type>"` (claims also carry `data-claim-id`). Types: `preheader`, `logo`, `header`, `subheader`, `body`, `claim`, `cta`, `isi`, `footer`, `image`. Sections of one type are numbered in document order, so "the 2nd subheader" is precise. `assets.tag_status` records how an email was tagged: `tagged` (built into our template), `suggested` (auto-detected, unchecked), `confirmed` (a person reviewed them), `untagged`.

**Candidates.** A candidate is the outermost element that holds visible text (and has no block children) or an image, skipping `<head>`, `<style>` and `<script>`. Each gets a stable `data-eid` so a tag can be assigned to it. A candidate with no tag is simply an untagged element.

**Auto-detection (`workflows/sections.py`, deterministic, no LLM).**

| Type | Rule (in this order) |
|---|---|
| preheader | hidden text (`display:none`, `max-height:0`, ...) |
| isi | contains "important safety information" |
| claim | equals or nearly equals (>= 0.85) an approved claim; gets that claim's id |
| footer | an unsubscribe link, "(c)", "all rights reserved", or plain text after the ISI |
| cta | an `<a>` that looks like a button (background, or padding + inline-block) or short text with an action verb |
| header | `<h1>`, else a first line with a large font, else the first subheader |
| subheader | `<h2>`-`<h6>`, or a short, bold or large line that is not a sentence |
| logo / image | an image with "logo" in alt/src/class or the brand name, or the very first element; other images are `image` |
| body | everything else |

Uploads are auto-tagged immediately (status `suggested`); the original upload is kept as `email.original.html`. On the Assets page, the **Sections** tab shows each detected element with its auto-detected type and lets a person correct any tag, then **Save tags** (status `confirmed`). Saving replaces all tags and re-validates, because tags change how claims are checked: text tagged as a claim that matches no approved claim is a validation error.

**Targeted edits.** New operations: `replace_section_text`, `recolor_section` (background or text), and `swap_logo` that can name which logo. The model proposes a target as type plus number; code resolves it. The model is given the email's section list so it can map "the part about dosing" or "the 2nd subheader" to a section.

**Asking before acting.**
- Several sections of the type and no number given: the agent asks, listing each with its text ("There are 3 subheaders: 1) ... 2) ... Which one do you mean?"), and nothing is applied.
- A number out of range, or a type the email does not have: explained, with the sections that do exist.
- Background vs text color not stated: asked.
- Once a target resolves, the plan shows exactly what it understood ("subheader #2 of 3 changed from ...") and says so if the tags are only auto-detected. Approving the plan is the confirmation.

**Guardrails.** Claims cannot be edited freehand (verbatim only), the ISI can only be replaced with the approved text, and images need a logo swap. Color scoping includes the wrapper cells that hold only the section, so a header's banner color (set on the surrounding cell) changes with it, while the ISI and CTA backgrounds do not.

**Export** strips `data-slot`, `data-eid` and `data-claim-id`, so recipients get clean HTML. The stored copy keeps its tags.

**Limits.** Detection is rule-based, so unusual layouts will need a person to correct tags (that is what the editor is for). A section's text is replaced as plain text, so inline styling inside it (for example a bold word) is lost. An ISI split across several paragraphs is detected only where the heading phrase appears.


### 18.1 Stress test: `samples/complex_email.html`

A deliberately hard email: nested tables, a two-column image-and-text block, a three-column feature row, 7 CTAs (a table-cell "bulletproof" button, text links, an outlined button), 9 images, 8 subheaders, a callout, a hidden preheader, and a 4-part ISI (paragraphs and a list). Running it exposed five misclassifications, now fixed (rules updated in the table above):

- `utility` is a new type for boilerplate such as "Trouble viewing this email? View in browser", so it does not count as "body paragraph 1".
- A short unpunctuated line directly under the header is a sub-headline (`subheader`).
- Every standalone link or button is a `cta`, except unsubscribe/privacy/terms links (`footer`) and view-in-browser links (`utility`). The old verb list tagged "See the efficacy data" as a CTA but not "Explore support".
- Small images above the header (height <= 80px) are logos, so a partner logo is "logo #2" and a large hero stays an `image`.
- An ISI that spans several paragraphs or list items in the same cell is detected as one ISI. Replacing it merges all parts into the single approved text and removes the leftovers (including an emptied list), and the plan says how many parts were merged.

Result for that email: 1 preheader, 1 utility, 2 logos, 7 images, 1 header, 8 subheaders, 4 body, 4 claims (the repeated claim is tagged both times), 7 CTAs, 4 ISI, 3 footer. "The 2nd CTA", "the 4th subheader" and "the 2nd logo" each resolve to one element, and "change the CTA" asks which of the 7.

**A bug this surfaced.** Uploaded emails never had a preview on the real server: the upload endpoint is `async`, and Playwright's sync API cannot run inside a running event loop (the failure was only logged). The blocking store step now runs in a worker thread, and a regression test runs the real endpoint with previews switched on. The preview renderer blocks external images by design, so emails with remote images show their alt text in the preview.


---

## 19. Step 6 as built: Persona personalization

**What it does.** Takes an existing, tagged email and a target persona and rewrites **tone and emphasis only**. Nothing else changes.

| May change | Never changes |
|---|---|
| preheader, header, subheaders, body paragraphs, CTA text | claim wording, the ISI, the footer, utility links, logos and images |

The plan shows both lists and how many sections of each kind are rewritable, so the user knows the scope before approving.

**How.** The model is given the persona (description, tone, concerns), the brand tone, optional user notes, and the numbered list of rewritable sections with their current text. It returns `PersonaRewrite`: a list of "section type + number -> new text" entries, covering only the sections it wants to change. Deterministic code applies them with the same `replace_section_text` operation the adapt lane uses, so the same guardrails hold (claims and the ISI cannot be edited, numbers are resolved against the email's own tags). The schema does not even offer the model the protected types.

**Claim emphasis.** At plan time the model picks which approved claims matter most for the persona. If all the email's claims sit in one list, those claims are moved to the front, as whole elements, so their text cannot change. If the claims are spread across different cells (as in the complex sample), the order is left alone, and the plan says the move applies only when there is a single list.

**Retry loop** (the same subgraph as generate, now parameterized by schema), max 3 attempts, with the failed checks fed back. The checks:
- the rewrite applied cleanly (valid section numbers),
- no number appears that is not already in the email, the approved claims, or the user's notes,
- no rewrite is more than about 2.5x the original length,
- the compliance validator reports **no new errors**.

"New" matters: problems that already existed in the source email are not allowed to block personalizing it, so a non-compliant upload can still be adapted. The result is then reported honestly (validation still fails and it cannot be approved).

**Result.** A new asset (`origin = personalize`, `parent_asset_id` = the source, named like `<email> (Primary Care Physician)`) with `meta` holding the persona, the rewrites, the claim order, the attempt count and the document versions. The source email is never modified. If all 3 attempts fail, the last draft is saved with its failing validation, as in generate.

**Tested live.** The sample email for a Primary Care Physician: header, preheader, CTA and body rewritten, two claims swapped with their text identical, ISI and footer unchanged, validation passed on the first attempt. The 42-section complex email: 9 sections changed, none of the protected types changed, about 4 seconds.

**Limits.** Section text is replaced as plain text, so inline formatting inside a rewritten section (a bold word, a line break) is lost. The numeric check blocks invented statistics but cannot detect a soft, non-numeric overclaim, so the human reviewer remains the last check. Emails must be tagged; an untagged email is refused with an instruction to tag it first.
