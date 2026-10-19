"""Tiny markdown helpers shared by the four document parsers."""
import re
from dataclasses import dataclass, field


class ParseError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass
class Section:
    title: str
    lines: list[str] = field(default_factory=list)


_KV = re.compile(r"^\s*(?:[-*]\s+)?([^:]+?):\s*(.*)$")
_BULLET = re.compile(r"^\s*[-*]\s+(.*\S)\s*$")


def split_sections(text: str) -> tuple[str | None, list[Section]]:
    """Return (H1 title, [H2 sections]). HTML comments and blank lines are dropped."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    h1: str | None = None
    sections: list[Section] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.startswith("## "):
            sections.append(Section(line[3:].strip()))
        elif line.startswith("# "):
            h1 = line[2:].strip()
        elif sections:
            sections[-1].lines.append(line)
    return h1, sections


def kv(line: str) -> tuple[str, str] | None:
    """Split 'key: value' (optionally bulleted) on the FIRST colon only."""
    m = _KV.match(line)
    return (m.group(1).strip(), m.group(2).strip()) if m else None


def bullet(line: str) -> str | None:
    m = _BULLET.match(line)
    return m.group(1) if m else None
