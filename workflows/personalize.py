"""Pure helpers for the personalize lane. Persona personalization changes tone and
emphasis only: it may rewrite headings, body copy and button text, and reorder which
approved claim comes first. It never edits claims, the ISI, the footer or images."""
import copy as _copy

from bs4 import BeautifulSoup

from workflows.sections import Section, located_sections, norm

REWRITABLE = ("preheader", "header", "subheader", "body", "cta")


def rewritable_sections(html: str) -> list[Section]:
    return [s for s in all_sections(html) if s.type in REWRITABLE]


def all_sections(html: str) -> list[Section]:
    return [s for s, _ in located_sections(BeautifulSoup(html, "html.parser"))]


def summary(html: str) -> dict[str, int]:
    """{type: count} of what may be rewritten, for showing in the plan."""
    out: dict[str, int] = {}
    for s in rewritable_sections(html):
        out[s.type] = out.get(s.type, 0) + 1
    return out


def visible_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for el in soup(["script", "style"]):
        el.decompose()
    return norm(soup.get_text(" "))


def ops_from_rewrites(rewrites: list[dict]) -> list[dict]:
    return [
        {"type": "replace_section_text", "target": {"type": r["type"], "ordinal": r["ordinal"]}, "text": r["text"]}
        for r in rewrites
    ]


def reorder_claims(html: str, lead_ids: list[str]) -> tuple[str, list[str]]:
    """Move the claims in `lead_ids` to the front of the claims list, keeping the rest in
    their order. Only when every claim sits in the same container (a single list); the
    claim elements are moved whole, so their text is never touched.
    Returns (html, resulting claim id order) and leaves the html as-is when nothing applies."""
    soup = BeautifulSoup(html, "html.parser")
    els = [el for s, el in located_sections(soup) if s.type == "claim" and el.get("data-claim-id")]
    if len(els) < 2 or len({id(e.parent) for e in els}) != 1:
        return html, [e["data-claim-id"] for e in els]
    ids = [e["data-claim-id"] for e in els]
    lead = [c for c in dict.fromkeys(lead_ids) if c in ids]
    new_ids = lead + [c for c in ids if c not in lead]
    if new_ids == ids:
        return html, ids
    by_id = {e["data-claim-id"]: e for e in els}
    copies = [_copy.copy(by_id[c]) for c in new_ids]
    for slot, replacement in zip(els, copies):
        slot.replace_with(replacement)
    return str(soup), new_ids
