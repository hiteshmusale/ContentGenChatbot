from agent.subgraphs import EmailCopy, build_copy_graph
from workflows.generate import invented_numbers, numbers


def test_numbers_normalizes_commas_percent_and_trailing_dots():
    assert numbers("Up to 52% in 1,200 adults. Taken 2.5 times.") == {"52%", "1200", "2.5"}


def test_invented_numbers_only_reports_unsupported_ones():
    claims = ["lowered LDL-C by up to 52%", "once daily"]
    assert invented_numbers(["Up to 52% lower"], claims) == []
    assert invented_numbers(["52% and 40%", "in 3 weeks"], claims) == ["3", "40%"]
    assert invented_numbers(["no numerals here"], claims) == []


class Scripted:
    def __init__(self, copies):
        self.copies, self.prompts = list(copies), []

    def with_structured_output(self, schema, **kwargs):
        outer = self

        class R:
            def invoke(self, msgs):
                outer.prompts.append(msgs[-1].content)
                return schema(**outer.copies.pop(0))

        return R()


def c(headline):
    return {"subject": "s", "preheader": "p", "headline": headline, "body": ["b"], "cta_text": "Go"}


def run(copies, check, max_attempts=3):
    llm = Scripted(copies)
    g = build_copy_graph(llm, "sys", "brief", lambda cp: f"<h1>{cp.headline}</h1>", check, max_attempts)
    return g.invoke({}), llm


def test_loop_stops_as_soon_as_checks_pass():
    out, llm = run([c("bad"), c("good"), c("never used")], lambda cp, html: [] if cp.headline == "good" else ["nope"])
    assert out["attempt"] == 2 and out["copy"]["headline"] == "good" and out["problems"] == []
    assert len(llm.copies) == 1 and "nope" in llm.prompts[1] and "nope" not in llm.prompts[0]


def test_loop_is_capped_and_keeps_last_draft_and_history():
    out, llm = run([c("a"), c("b")], lambda cp, html: ["still bad"], max_attempts=2)
    assert out["attempt"] == 2 and out["copy"]["headline"] == "b" and out["html"] == "<h1>b</h1>"
    assert [h["attempt"] for h in out["history"]] == [1, 2] and out["problems"] == ["still bad"]
