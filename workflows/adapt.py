"""Mechanical adaptation: deterministic HTML transforms. No LLM involved here; the agent
turns the user's request into structured ops, this module only applies them.

Ops (dicts):
  {"type": "swap_logo",    "logo": "<file in workspace logos>", "target": {"type": "logo", "ordinal": 2}?}
  {"type": "change_color", "color": "#RRGGBB", "to_color": "#RRGGBB"}
  {"type": "replace_isi"}                                   # uses the current ISI rule
  {"type": "replace_text", "find": "...", "replace": "..."} # never touches approved claims
  {"type": "replace_section_text", "target": {"type": "subheader", "ordinal": 2}, "text": "..."}
  {"type": "recolor_section", "target": {...}, "property": "background"|"text", "to_color": "#RRGGBB"}

A target names a section by type and number ("the 2nd subheader"). If the number is left
out and the email has several sections of that type, the op is reported as ambiguous so the
agent can ask which one, rather than guessing.
"""
import re
from dataclasses import dataclass, field
from typing import Callable

from bs4 import BeautifulSoup, NavigableString, Tag

from workflows.sections import TargetError, located_sections, norm, resolve_target, short

OP_TYPES = ("swap_logo", "change_color", "replace_isi", "replace_text", "replace_section_text", "recolor_section")
HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


class AdaptError(Exception):
    pass


@dataclass
class AdaptEnv:
    isi_text: str | None = None
    load_logo: Callable[[str], str | None] = lambda name: None  # filename -> data URI


@dataclass
class OpReport:
    op: dict
    ok: bool
    detail: str
    ambiguous: bool = False  # the target matched several sections and the user must choose


@dataclass
class AdaptResult:
    html: str
    reports: list[OpReport] = field(default_factory=list)

    @property
    def ambiguous(self) -> bool:
        return any(r.ambiguous for r in self.reports)

    @property
    def errors(self) -> list[str]:
        return [f"{describe_op(r.op)}: {r.detail}" for r in self.reports if not r.ok]


def norm_hex(value: str | None) -> str | None:
    """'#abc' -> '#AABBCC'; None if not a hex color."""
    if not value or not HEX.match(value.strip()):
        return None
    h = value.strip().lstrip("#").upper()
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return f"#{h}"


def tlabel(target: dict | None) -> str:
    t = target or {}
    return f"{t['type']} #{t['ordinal']}" if t.get("ordinal") else f"the {t.get('type', 'section')}"


def describe_op(op: dict) -> str:
    t = op["type"]
    if t == "swap_logo":
        where = f" ({tlabel(op['target'])})" if op.get("target") else ""
        return f"Swap logo{where} to {op['logo']}"
    if t == "change_color":
        return f"Change color {op['color']} to {op['to_color']} everywhere it appears"
    if t == "replace_isi":
        return "Replace the ISI block with the current approved ISI text"
    if t == "replace_section_text":
        return f"Replace the text of {tlabel(op['target'])} with \"{op['text']}\""
    if t == "recolor_section":
        return f"Set the {op['property']} color of {tlabel(op['target'])} to {op['to_color']}"
    return f"Replace text \"{op['find']}\" with \"{op['replace']}\""


def _in_claim(node) -> bool:
    return any(
        p.get("data-claim-id") is not None or p.get("data-slot") in ("claim", "claims")
        for p in node.parents if hasattr(p, "get")
    )


def _swap_logo(soup: BeautifulSoup, op: dict, env: AdaptEnv) -> str:
    src = env.load_logo(op["logo"])
    if src is None:
        raise AdaptError(f"logo file '{op['logo']}' not found")
    if any(s.type == "logo" for s, _ in located_sections(soup)):
        # several logos and no number given -> TargetError(ambiguous) asks the user which
        _, el = resolve_target(soup, "logo", (op.get("target") or {}).get("ordinal"))
    else:
        el = soup.find("img")  # untagged email: fall back to the first image
        if el is None:
            raise AdaptError("no logo found (no tagged logo and no <img> in the email)")
    img = el if el.name == "img" else el.find("img")
    if img is None:  # a text wordmark: put the image inside it
        el.clear()
        img = soup.new_tag("img", alt="Logo", height="40", style="display:block;height:40px;border:0;")
        el.append(img)
    img["src"] = src
    return f"logo set to {op['logo']}"


def _replace_section_text(soup: BeautifulSoup, op: dict) -> str:
    tgt = op["target"]
    if tgt["type"] == "claim":
        raise AdaptError("approved claims must stay verbatim, so their text cannot be edited")
    if tgt["type"] == "isi":
        raise AdaptError("the ISI can only be replaced with the approved ISI text, not freehand")
    if tgt["type"] in ("logo", "image"):
        raise AdaptError("that is an image: use a logo swap instead")
    if not (op.get("text") or "").strip():
        raise AdaptError("the new text is empty")
    sec, el = resolve_target(soup, tgt["type"], tgt.get("ordinal"))
    el.clear()
    el.append(NavigableString(op["text"].strip()))
    return f'{sec.type} #{sec.ordinal} of {sec.count} changed from "{short(sec.text)}"'


_BG = re.compile(r"(background-color\s*:\s*)([^;\"]+)", re.I)
_FG = re.compile(r"(?<![-\w])(color\s*:\s*)([^;\"]+)", re.I)


def _scope(el: Tag) -> list[Tag]:
    """The section's element, its descendants, and the wrapper cells that hold only it (so a
    header's banner color, which sits on the surrounding cell, is included)."""
    chain, text = [el], norm(el.get_text(" "))
    for p in el.parents:
        if p.name in ("table", "body", "html", "[document]") or norm(p.get_text(" ")) != text:
            break
        chain.append(p)
    return chain + [d for d in el.descendants if isinstance(d, Tag)]


def _recolor_section(soup: BeautifulSoup, op: dict) -> str:
    tgt, prop, new = op["target"], op["property"], norm_hex(op["to_color"])
    if prop not in ("background", "text") or not new:
        raise AdaptError("need a property (background or text) and a hex color")
    sec, el = resolve_target(soup, tgt["type"], tgt.get("ordinal"))
    pattern = _BG if prop == "background" else _FG
    changed = 0
    for node in _scope(el):
        style = node.get("style", "")
        if pattern.search(style):
            node["style"] = pattern.sub(lambda m: f"{m.group(1)}{new}", style)
            changed += 1
        if prop == "background" and node.has_attr("bgcolor"):
            node["bgcolor"] = new
            changed += 1
    if changed == 0:  # nothing to replace: set it on the section itself
        css = "background-color" if prop == "background" else "color"
        el["style"] = f"{el.get('style', '').rstrip('; ')};{css}:{new}".lstrip(";")
        changed = 1
    return f"{prop} color of {sec.type} #{sec.ordinal} of {sec.count} set to {new} ({changed} element{'s' if changed != 1 else ''})"


def _change_color(html: str, op: dict) -> tuple[str, str]:
    old, new = norm_hex(op["color"]), norm_hex(op["to_color"])
    if not old or not new:
        raise AdaptError("colors must be hex values like #0B5CAD")
    pattern = re.compile(rf"(?<![0-9A-Fa-f]){re.escape(old)}(?![0-9A-Fa-f])", re.IGNORECASE)
    if len(old) == 7:  # also match the 3-digit shorthand form of the same color
        short = "#" + "".join(old[i] for i in (1, 3, 5)) if old[1] == old[2] and old[3] == old[4] and old[5] == old[6] else None
        if short:
            pattern = re.compile(
                rf"(?<![0-9A-Fa-f])(?:{re.escape(old)}|{re.escape(short)})(?![0-9A-Fa-f])", re.IGNORECASE
            )
    out, n = pattern.subn(new, html)
    if n == 0:
        raise AdaptError(f"color {old} does not appear in this email")
    return out, f"{n} occurrence{'s' if n != 1 else ''} of {old} changed to {new}"


def locate_isi(soup: BeautifulSoup):
    slot = soup.select_one('[data-slot="isi"]')
    if slot is not None:
        return slot
    best = None
    for el in soup.find_all(["td", "div", "p", "section"]):
        text = el.get_text(" ", strip=True).lower()
        if "important safety information" in text:
            best = el  # last match in document order is the deepest one
    return best


def _remove(el: Tag) -> None:
    """Delete an element and any wrapper (e.g. a list) it leaves empty, stopping at table cells."""
    parent = el.parent
    el.decompose()
    while parent is not None and parent.name not in ("td", "th", "body", "html", "[document]") \
            and not parent.get_text(strip=True) and parent.find("img") is None:
        up = parent.parent
        parent.decompose()
        parent = up


def _replace_isi(soup: BeautifulSoup, env: AdaptEnv) -> str:
    if not env.isi_text:
        raise AdaptError("no ISI block is defined in the compliance rules")
    parts = [el for sec, el in located_sections(soup) if sec.type == "isi"] or (
        [locate_isi(soup)] if locate_isi(soup) is not None else []
    )
    if not parts:
        raise AdaptError("could not find an ISI block in this email")
    first, rest = parts[0], parts[1:]
    first.clear()
    first.append(NavigableString(env.isi_text))
    for extra in rest:  # an ISI spread over several paragraphs becomes the one approved block
        _remove(extra)
    return "ISI block replaced" + (f" ({len(rest) + 1} ISI sections merged into the approved text)" if rest else "")


def _replace_text(soup: BeautifulSoup, op: dict) -> str:
    find, repl = op["find"], op["replace"]
    if not find:
        raise AdaptError("text to find is empty")
    done = protected = 0
    for node in list(soup.find_all(string=lambda s: find in s)):
        if node.parent.name in ("script", "style"):
            continue
        if _in_claim(node):
            protected += 1
            continue
        node.replace_with(NavigableString(str(node).replace(find, repl)))
        done += 1
    if done == 0:
        if protected:
            raise AdaptError("that text is inside an approved claim, which cannot be edited")
        raise AdaptError(f"text \"{find}\" not found in this email")
    note = f", {protected} inside approved claims left untouched" if protected else ""
    return f"replaced in {done} place{'s' if done != 1 else ''}{note}"


def apply_ops(html: str, ops: list[dict], env: AdaptEnv) -> AdaptResult:
    """Apply ops in order. An op that cannot apply is reported and skipped, never guessed."""
    reports: list[OpReport] = []
    for op in ops:
        try:
            t = op["type"]
            if t == "change_color":
                html, detail = _change_color(html, op)
            else:
                soup = BeautifulSoup(html, "html.parser")
                if t == "swap_logo":
                    detail = _swap_logo(soup, op, env)
                elif t == "replace_isi":
                    detail = _replace_isi(soup, env)
                elif t == "replace_text":
                    detail = _replace_text(soup, op)
                elif t == "replace_section_text":
                    detail = _replace_section_text(soup, op)
                elif t == "recolor_section":
                    detail = _recolor_section(soup, op)
                else:
                    raise AdaptError(f"unknown operation '{t}'")
                html = str(soup)
            reports.append(OpReport(op, True, detail))
        except TargetError as e:
            reports.append(OpReport(op, False, str(e), ambiguous=e.ambiguous))
        except AdaptError as e:
            reports.append(OpReport(op, False, str(e)))
    return AdaptResult(html, reports)
