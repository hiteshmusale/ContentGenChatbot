"""Write-copy / check / retry loop used by the generate and personalize lanes.

    write_copy -> check -> (pass or attempts exhausted: END) | (fail: write_copy again)

The model only writes the copy fields. Rendering and checking are injected as plain
functions, so the loop itself knows nothing about HTML, claims or the database.
"""
from typing import Callable, Literal, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field as PField

MAX_ATTEMPTS = 3


class EmailCopy(BaseModel):
    subject: str = PField(description="Email subject line, under 70 characters")
    preheader: str = PField(description="Inbox preview text, under 100 characters")
    headline: str = PField(description="Headline shown at the top of the email, under 80 characters")
    body: list[str] = PField(description="2 to 3 short paragraphs that introduce and frame the approved claims shown below the body")
    cta_text: str = PField(description="Button text, 2 to 4 words")


class SectionRewrite(BaseModel):
    type: Literal["preheader", "header", "subheader", "body", "cta"] = PField(
        description="Kind of section to rewrite. Only these kinds may be rewritten"
    )
    ordinal: int = PField(description="1-based position among sections of that kind, as numbered in the list you were given")
    text: str = PField(description="The new text for the whole section")


class PersonaRewrite(BaseModel):
    rewrites: list[SectionRewrite] = PField(
        description="Only the sections whose tone or emphasis should change for this audience. Omit sections that need no change"
    )


class CopyState(TypedDict, total=False):
    attempt: int
    copy: dict | None
    html: str | None
    problems: list[str]
    history: list[dict]  # [{"attempt": n, "problems": [...]}]


def build_copy_graph(
    llm,
    system: str,
    brief: str,
    render: Callable[[BaseModel], str],
    check: Callable[[BaseModel, str], list[str]],
    max_attempts: int = MAX_ATTEMPTS,
    schema: type[BaseModel] = EmailCopy,
):
    def write_copy(state: CopyState):
        attempt = state.get("attempt", 0) + 1
        prompt = brief
        if state.get("problems"):
            prev = state["copy"]
            fixes = "\n".join(f"- {p}" for p in state["problems"])
            prompt += (
                f"\n\nYour previous draft failed these checks:\n{fixes}\n\nPrevious draft: {prev}\n"
                "Rewrite it so every check passes. Do not add any claims or numbers of your own."
            )
        copy = llm.with_structured_output(schema, method="json_schema").invoke(
            [SystemMessage(system), HumanMessage(prompt)]
        )
        return {"attempt": attempt, "copy": copy.model_dump()}

    def check_node(state: CopyState):
        copy = schema(**state["copy"])
        html = render(copy)
        problems = check(copy, html)
        history = [*state.get("history", []), {"attempt": state["attempt"], "problems": problems}]
        return {"html": html, "problems": problems, "history": history}

    def after_check(state: CopyState):
        if not state["problems"] or state["attempt"] >= max_attempts:
            return END
        return "write_copy"

    g = StateGraph(CopyState)
    g.add_node("write_copy", write_copy)
    g.add_node("check", check_node)
    g.add_edge(START, "write_copy")
    g.add_edge("write_copy", "check")
    g.add_conditional_edges("check", after_check, ["write_copy", END])
    return g.compile()
