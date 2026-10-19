"""Sections: which part of an email is the header, a subheader, a claim, the CTA, and so on.

Tags live in the HTML itself as `data-slot="<type>"` (claims also get `data-claim-id`).
Emails built from our template are tagged as they are generated. Uploaded emails are
auto-tagged by the deterministic rules below, and a person can correct the tags.

Terms:
  candidate  an element that holds visible text (outermost, no block children) or an image.
             Each gets a stable `data-eid` so a tag can be assigned to it.
  section    a candidate that carries a tag. Sections of one type are numbered in document
             order, which is what makes "the 2nd subheader" mean something precise.
"""
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

SECTION_TYPES = ("preheader", "utility", "logo", "header", "subheader", "body", "claim", "cta", "isi", "footer", "image")
LEGACY_SLOTS = {"headline": "header"}  # older emails
_CLEARABLE = set(SECTION_TYPES) | {"headline", "claims"}
INTERNAL_ATTRS = ("data-slot", "data-eid", "data-claim-id")

_BLOCK = {
    "table", "thead", "tbody", "tfoot", "tr", "td", "th", "div", "p", "ul", "ol", "li", "section",
    "article", "blockquote", "center", "main", "header", "footer", "h1", "h2", "h3", "h4", "h5", "h6",
}
_SKIP = {"head", "title", "style", "script", "noscript"}
_UTILITY = ("view in browser", "view online", "trouble viewing", "view this email", "view as a web page")
_FOOTER_LINKS = ("unsubscribe", "privacy policy", "terms of use", "terms & conditions", "manage preferences")
NEAR = 0.85


def norm(text: str) -> str:
    return " ".join(text.split())


@dataclass
class Section:
    type: str
    ordinal: int  # 1-based position among sections of the same type
    count: int  # how many sections of this type the email has
    text: str  # short label of the content, for showing to people


@dataclass
class Candidate:
    eid: int
    tag: str  # element name, e.g. "p", "a", "img"
    text: str
    type: str | None  # the tag currently on the element, if any
    suggested: str | None  # what the auto-detector would choose


# ---- finding candidates ---------------------------------------------------------------

def _own_text(el: Tag) -> bool:
    return any(isinstance(c, NavigableString) and not isinstance(c, Comment) and c.strip() for c in el.children)


def _has_block_descendant(el: Tag) -> bool:
    return any(isinstance(d, Tag) and d.name in _BLOCK for d in el.descendants)


def candidates(soup: BeautifulSoup) -> list[Tag]:
    out: list[Tag] = []
    ids: set[int] = set()
    for el in soup.find_all(True):
        if el.name in _SKIP or any(p.name in _SKIP for p in el.parents):
            continue
        if any(id(p) in ids for p in el.parents):
            continue  # inside an element we already treat as one block
        if el.name == "img" or (_own_text(el) and not _has_block_descendant(el)):
            out.append(el)
            ids.add(id(el))
    return out


def img_label(img: Tag) -> str:
    alt = (img.get("alt") or "").strip()
    if alt:
        return f"image: {alt}"
    src = img.get("src") or ""
    if src and not src.startswith("data:"):
        return f"image: {src.rsplit('/', 1)[-1].split('?')[0]}"
    return "image (embedded)"


def el_text(el: Tag) -> str:
    if el.name == "img":
        return img_label(el)
    t = norm(el.get_text(" "))
    if not t:
        img = el.find("img")
        return img_label(img) if img else ""
    return t


def annotate(html: str) -> str:
    """Give every candidate a stable `data-eid`. Existing ids are kept."""
    soup = BeautifulSoup(html, "html.parser")
    existing = [int(e["data-eid"]) for e in soup.find_all(attrs={"data-eid": True}) if str(e["data-eid"]).isdigit()]
    nxt = max(existing, default=-1) + 1
    for el in candidates(soup):
        if not el.has_attr("data-eid"):
            el["data-eid"] = str(nxt)
            nxt += 1
    return str(soup)


# ---- auto-detection (deterministic) ---------------------------------------------------

def _style(el: Tag) -> str:
    return (el.get("style") or "").lower().replace(" ", "")


def _font_px(style: str) -> float | None:
    m = re.search(r"font-size:(\d+(?:\.\d+)?)px", style)
    return float(m.group(1)) if m else None


def _hidden(style: str) -> bool:
    return any(k in style for k in ("display:none", "max-height:0", "font-size:0", "opacity:0"))


def _near(a: str, b: str) -> bool:
    if abs(len(a) - len(b)) > 0.3 * max(len(a), len(b), 1):
        return False
    m = SequenceMatcher(None, a, b, autojunk=False)
    return m.quick_ratio() >= NEAR and m.ratio() >= NEAR


def match_claim(text: str, claims: dict[str, str]) -> str | None:
    """Claim id whose approved text this equals or nearly equals."""
    t = norm(text)
    for cid, c in claims.items():
        if t == norm(c):
            return cid
    for cid, c in claims.items():
        if _near(t, norm(c)):
            return cid
    return None


def _classify(el: Tag, idx: int, claims: dict[str, str], brand: str | None) -> str:
    style, text = _style(el), el_text(el)
    low = text.lower()
    if el.name == "img":
        hint = "logo" in f"{el.get('alt', '')} {el.get('src', '')} {' '.join(el.get('class', []))}".lower()
        if brand and brand.lower() in (el.get("alt") or "").lower():
            hint = True
        return "logo" if hint or idx == 0 else "image"
    if _hidden(style):
        return "preheader"
    if "important safety information" in low:
        return "isi"
    if match_claim(text, claims):
        return "claim"
    if any(k in low for k in _UTILITY):
        return "utility"
    if any(k in low for k in _FOOTER_LINKS):
        return "footer"
    if el.name == "a":
        return "cta"  # a link standing on its own (buttons and text links alike) is a call to action
    if any(k in low for k in ("©", "all rights reserved")):
        return "footer"
    if el.name == "h1":
        return "header"
    px = _font_px(style)
    bold = any(k in style for k in ("font-weight:bold", "font-weight:700", "font-weight:600")) or el.name in ("b", "strong")
    if el.name in ("h2", "h3", "h4", "h5", "h6"):
        return "subheader"
    if len(text) <= 90 and not text.endswith((".", "!", "?")) and (bold or (px and px >= 18)):
        return "subheader"
    return "body"


def _height_px(el: Tag) -> float | None:
    h = el.get("height")
    if h and str(h).rstrip("px").isdigit():
        return float(str(h).rstrip("px"))
    m = re.search(r"(?<![-\w])height:(\d+(?:\.\d+)?)px", _style(el))
    return float(m.group(1)) if m else None


def suggest_types(cands: list[Tag], claims: dict[str, str], brand: str | None = None) -> list[str]:
    types = [_classify(el, i, claims, brand) for i, el in enumerate(cands)]
    if "header" not in types:  # no <h1>: take a large first line, else the first subheader
        big = next((i for i, (el, t) in enumerate(zip(cands, types)) if t in ("body", "subheader") and (_font_px(_style(el)) or 0) >= 22), None)
        first_sub = types.index("subheader") if "subheader" in types else None
        pick = big if big is not None else first_sub
        if pick is not None:
            types[pick] = "header"
    if "header" in types:
        h = types.index("header")
        # a short unpunctuated line right under the header is its sub-headline
        if h + 1 < len(types) and types[h + 1] == "body":
            t = el_text(cands[h + 1])
            if len(t) <= 100 and not t.endswith((".", "!", "?", ":", ",")):
                types[h + 1] = "subheader"
        # small images above the header (partner logos, badges) are logos; big ones are the hero
        for i in range(h):
            if types[i] == "image" and (_height_px(cands[i]) or 999) <= 80:
                types[i] = "logo"
    # an ISI often runs over several paragraphs/list items in the same cell: keep them together
    for i, t in enumerate(list(types)):
        if t == "isi":
            cell = cands[i].find_parent(["td", "th"])
            for j in range(i + 1, len(cands)):
                if cell is not None and cell in cands[j].parents and types[j] in ("body", "footer", "subheader"):
                    types[j] = "isi"
    if "isi" in types:  # plain text after the ISI is footer material
        first_isi = types.index("isi")
        types = [("footer" if i > first_isi and t == "body" else t) for i, t in enumerate(types)]
    return types


def current_type(el: Tag) -> str | None:
    slot = LEGACY_SLOTS.get(el.get("data-slot"), el.get("data-slot"))
    if slot in SECTION_TYPES:
        return slot
    return "claim" if el.get("data-claim-id") is not None else None


def candidate_list(html: str, claims: dict[str, str], brand: str | None = None) -> list[Candidate]:
    soup = BeautifulSoup(annotate(html), "html.parser")
    cands = candidates(soup)
    sugg = suggest_types(cands, claims, brand)
    return [Candidate(int(el["data-eid"]), el.name, el_text(el), current_type(el), s) for el, s in zip(cands, sugg)]


# ---- applying tags --------------------------------------------------------------------

def apply_tags(html: str, tags: dict[int, str | None], claims: dict[str, str]) -> str:
    """Replace ALL tags with `tags` ({eid: type or None}). Candidates not listed end up
    untagged. A claim gets the id of the approved claim its text matches, so the validator
    can hold it to the approved wording."""
    soup = BeautifulSoup(annotate(html), "html.parser")
    for el in soup.find_all(True):
        if el.get("data-slot") in _CLEARABLE:
            del el["data-slot"]
        if el.has_attr("data-claim-id"):
            del el["data-claim-id"]
    by_eid = {int(el["data-eid"]): el for el in soup.find_all(attrs={"data-eid": True}) if str(el["data-eid"]).isdigit()}
    for eid, t in tags.items():
        if t is None or eid not in by_eid:
            continue
        if t not in SECTION_TYPES:
            raise ValueError(f"unknown section type '{t}'")
        el = by_eid[eid]
        el["data-slot"] = t
        if t == "claim":
            cid = match_claim(el_text(el), claims)
            if cid:
                el["data-claim-id"] = cid
    return str(soup)


def auto_tag(html: str, claims: dict[str, str], brand: str | None = None) -> str:
    cands = candidate_list(html, claims, brand)
    return apply_tags(html, {c.eid: c.suggested for c in cands}, claims)


# ---- reading sections and resolving "the 2nd subheader" -------------------------------

def located_sections(soup: BeautifulSoup) -> list[tuple[Section, Tag]]:
    found: list[tuple[str, Tag]] = []
    for el in soup.find_all(True):
        t = current_type(el)
        if t:
            found.append((t, el))
    counts: dict[str, int] = {}
    for t, _ in found:
        counts[t] = counts.get(t, 0) + 1
    seen: dict[str, int] = {}
    out = []
    for t, el in found:
        seen[t] = seen.get(t, 0) + 1
        out.append((Section(t, seen[t], counts[t], el_text(el)), el))
    return out


def list_sections(html: str) -> list[Section]:
    return [s for s, _ in located_sections(BeautifulSoup(html, "html.parser"))]


def short(text: str, n: int = 60) -> str:
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def plural(t: str) -> str:
    return {"cta": "CTAs", "isi": "ISI sections", "body": "body paragraphs", "utility": "utility links"}.get(t, f"{t}s")


class TargetError(Exception):
    def __init__(self, message: str, ambiguous: bool = False):
        super().__init__(message)
        self.ambiguous = ambiguous


def resolve_target(soup: BeautifulSoup, type_: str, ordinal: int | None) -> tuple[Section, Tag]:
    """The section a request points at. Raises TargetError (with a question the user can
    answer) when the email has none, the number is out of range, or it is ambiguous."""
    secs = located_sections(soup)
    same = [(s, el) for s, el in secs if s.type == type_]
    if not same:
        have = ", ".join(f"{t} ({sum(1 for s, _ in secs if s.type == t)})" for t in dict.fromkeys(s.type for s, _ in secs))
        raise TargetError(f"This email has no {type_} section. Sections found: {have or 'none (the email is not tagged yet)'}.")
    n = len(same)
    if ordinal is None:
        if n == 1:
            return same[0]
        opts = "; ".join(f'{s.ordinal}) "{short(s.text)}"' for s, _ in same)
        raise TargetError(f"There are {n} {plural(type_)}: {opts}. Which one do you mean?", ambiguous=True)
    if not 1 <= ordinal <= n:
        raise TargetError(f"There {'is only 1' if n == 1 else f'are only {n}'} {type_} section{'s' if n != 1 else ''}, so there is no number {ordinal}.")
    return same[ordinal - 1]


def strip_internal(html: str) -> str:
    """Remove our tagging attributes (for exporting to recipients)."""
    soup = BeautifulSoup(html, "html.parser")
    for el in soup.find_all(True):
        for a in INTERNAL_ATTRS:
            if el.has_attr(a):
                del el[a]
    return str(soup)
