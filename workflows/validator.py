"""Deterministic compliance validation of an email's HTML against the knowledge base.

Errors block approval; warnings are shown to the reviewer but do not.
  errors:   altered or unknown claims, missing/altered required blocks (ISI, disclaimer),
            missing mandatory text, banned phrases
  warnings: off-brand colors/fonts, a logo that is not one of the workspace's, no
            unsubscribe link, no approved claims at all
No LLM is involved: the same email and knowledge base always give the same report.
"""
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from bs4 import BeautifulSoup

NEAR_MISS = 0.85  # similarity at which "not identical" is treated as "altered"
NEUTRAL_EXTRA = {"#6B7280"}  # the template's footer gray
_HEX = re.compile(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})(?![0-9a-zA-Z_-])")
_FONT = re.compile(r"font-family\s*:\s*([^;\"]+)", re.IGNORECASE)


@dataclass
class KnowledgeBase:
    claims: dict[str, str] = field(default_factory=dict)  # claim_id -> verbatim text
    required_blocks: dict[str, str] = field(default_factory=dict)  # name -> text
    mandatory_text: list[str] = field(default_factory=list)
    banned_phrases: list[str] = field(default_factory=list)
    brand_colors: list[str] = field(default_factory=list)
    brand_fonts: list[str] = field(default_factory=list)
    logo_uris: set[str] = field(default_factory=set)  # data URIs of the workspace's logos


@dataclass
class Finding:
    severity: str  # error | warning
    code: str
    message: str

    def as_dict(self) -> dict:
        return {"severity": self.severity, "code": self.code, "message": self.message}


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(f.severity == "error" for f in self.findings)

    def as_dict(self) -> dict:
        return {"passed": self.passed, "findings": [f.as_dict() for f in self.findings]}


def norm(text: str) -> str:
    return " ".join(text.split())


def _hex6(h: str) -> str:
    h = h.lstrip("#").upper()
    return "#" + ("".join(c * 2 for c in h) if len(h) == 3 else h)


def _is_gray(h: str) -> bool:
    return h[1:3] == h[3:5] == h[5:7]


def _visible_text(soup: BeautifulSoup) -> str:
    for el in soup(["script", "style"]):
        el.decompose()
    return norm(soup.get_text(" "))


def _blocks(soup: BeautifulSoup):
    """Text of block-ish elements that are not themselves tagged claims."""
    for el in soup.find_all(["li", "p", "td", "div", "span", "h1", "h2", "h3"]):
        if el.get("data-claim-id") is None and not el.find(attrs={"data-claim-id": True}):
            t = norm(el.get_text(" "))
            if t:
                yield t


def _near(a: str, b: str) -> bool:
    if abs(len(a) - len(b)) > 0.3 * max(len(a), len(b)):
        return False
    m = SequenceMatcher(None, a, b, autojunk=False)
    return m.quick_ratio() >= NEAR_MISS and m.ratio() >= NEAR_MISS


def validate_email(html: str, kb: KnowledgeBase) -> Report:
    report = Report()
    add = lambda sev, code, msg: report.findings.append(Finding(sev, code, msg))  # noqa: E731
    soup = BeautifulSoup(html, "html.parser")

    # ---- gather style info before text extraction removes <style> ----
    style_texts = [el["style"] for el in soup.find_all(style=True)]
    style_texts += [el.get_text() for el in soup.find_all("style")]
    style_texts += [str(el[a]) for el in soup.find_all(True) for a in ("bgcolor", "color") if el.has_attr(a)]
    imgs = [img.get("src", "") for img in soup.find_all("img")]
    has_unsub = any("unsubscribe" in (a.get_text() + a.get("href", "")).lower() for a in soup.find_all("a"))
    tagged = soup.find_all(attrs={"data-claim-id": True})
    title = norm(soup.title.get_text()) if soup.title else ""
    blocks = list(_blocks(BeautifulSoup(html, "html.parser")))
    text = _visible_text(soup)
    full = f"{title} {text}"

    # ---- claims: verbatim ----
    for el in tagged:
        cid, found = el["data-claim-id"], norm(el.get_text(" "))
        expected = kb.claims.get(cid)
        if expected is None:
            add("error", "CLAIM_UNKNOWN", f"{cid} is not an approved claim")
        elif found != norm(expected):
            add("error", "CLAIM_ALTERED", f'{cid} is not verbatim. Approved: "{expected}" Found: "{found}"')
    # Text tagged as a claim that matches no approved claim is an unapproved claim.
    for el in soup.find_all(attrs={"data-slot": "claim"}):
        if el.get("data-claim-id") is None:
            add("error", "CLAIM_UNKNOWN", f'Text tagged as a claim is not an approved claim: "{norm(el.get_text(" "))}"')

    # A near-identical but not identical block is a reworded claim. This runs for every
    # claim, including ones that also appear verbatim, so a paraphrase placed next to the
    # real claim is still caught. Blocks exactly equal to the claim are verbatim, not altered.
    for cid, claim in kb.claims.items():
        want = norm(claim)
        for b in blocks:
            if b != want and _near(b, want):
                add("error", "CLAIM_ALTERED", f'Text looks like {cid} but is not verbatim. Approved: "{claim}" Found: "{b}"')
                break
    if not tagged and not any(norm(c) in text for c in kb.claims.values()):
        add("warning", "NO_CLAIMS", "The email contains no approved claims")

    # ---- required blocks and mandatory text ----
    for name, want in kb.required_blocks.items():
        if norm(want) in text:
            continue
        near = next((b for b in blocks if _near(b, norm(want))), None)
        if near:
            add("error", "REQUIRED_BLOCK_ALTERED", f'The {name} block does not match the approved text. Approved: "{want}" Found: "{near}"')
        else:
            add("error", "REQUIRED_BLOCK_MISSING", f"The required {name} block is missing")
    for want in kb.mandatory_text:
        if norm(want) not in text:
            add("error", "MANDATORY_TEXT_MISSING", f'Mandatory text is missing: "{want}"')

    # ---- banned phrases ----
    for phrase in kb.banned_phrases:
        m = re.search(rf"(?<![\w-]){re.escape(phrase)}(?![\w-])", full, re.IGNORECASE)
        if m:
            s = max(0, m.start() - 30)
            add("error", "BANNED_PHRASE", f'Banned phrase "{phrase}" found: "...{full[s:m.end() + 30].strip()}..."')

    # ---- brand (warnings) ----
    allowed = {_hex6(c) for c in kb.brand_colors} | NEUTRAL_EXTRA
    used = {_hex6(m.group(0)) for s in style_texts for m in _HEX.finditer(s)}
    for c in sorted(used - allowed):
        if not _is_gray(c):
            add("warning", "OFF_BRAND_COLOR", f"Color {c} is not one of the brand colors")
    brand_families = {f.split(",")[0].strip().strip("'\"").lower() for f in kb.brand_fonts}
    seen_fonts = {m.group(1).split(",")[0].strip().strip("'\"").lower() for s in style_texts for m in _FONT.finditer(s)}
    for f in sorted(seen_fonts - brand_families):
        if brand_families:
            add("warning", "OFF_BRAND_FONT", f"Font '{f}' is not one of the brand fonts")
    if not imgs:
        add("warning", "NO_LOGO", "The email has no logo image")
    elif kb.logo_uris and not any(src in kb.logo_uris for src in imgs):
        add("warning", "LOGO_NOT_APPROVED", "The logo is not one of this workspace's uploaded logos")
    if not has_unsub:
        add("warning", "NO_UNSUBSCRIBE", "The email has no unsubscribe link")
    return report
