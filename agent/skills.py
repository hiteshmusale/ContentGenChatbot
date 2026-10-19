"""Loads agent.md and the per-swimlane skill.md files; skill.md is the source of truth
for each lane's input schema."""
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).parent
_INPUT = re.compile(r"^-\s+(\w+)\s+\((required|optional)\):\s*(.+)$")


@dataclass(frozen=True)
class Field:
    name: str
    required: bool
    description: str


@dataclass(frozen=True)
class Skill:
    swimlane: str
    workflow: str
    label: str
    when: str
    inputs: tuple[Field, ...]
    plan_wording: str

    @property
    def required(self) -> list[Field]:
        return [f for f in self.inputs if f.required]


def _sections(text: str) -> dict[str, str]:
    out: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:].strip().lower()
            out[current] = []
        elif current:
            out[current].append(line)
    return {k: "\n".join(v).strip() for k, v in out.items()}


def _parse(path: Path) -> Skill:
    text = path.read_text()
    meta = dict(re.findall(r"^(swimlane|workflow|label):\s*(.+)$", text, flags=re.M))
    sec = _sections(text)
    fields = tuple(
        Field(m.group(1), m.group(2) == "required", m.group(3).strip())
        for line in sec["inputs"].splitlines()
        if (m := _INPUT.match(line.strip()))
    )
    if not fields:
        raise ValueError(f"{path.name}: no inputs found under '## Inputs'")
    return Skill(
        meta["swimlane"], meta["workflow"], meta["label"], sec["when to use"], fields,
        sec.get("plan wording", ""),
    )


@lru_cache
def load_skills() -> dict[str, Skill]:
    skills = [_parse(p) for p in sorted((HERE / "skills").glob("*.skill.md"))]
    return {s.swimlane: s for s in skills}


@lru_cache
def load_agent_md() -> str:
    return (HERE / "agent.md").read_text()
