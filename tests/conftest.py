from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from langgraph.checkpoint.memory import InMemorySaver

from agent.graph import build_graph
from backend import models  # noqa: F401  (register tables)
from backend.config import settings
from backend.db import Base, get_db
from backend.main import app

SAMPLES = Path(__file__).parent.parent / "samples"


@pytest.fixture
def sample():
    return lambda name: (SAMPLES / name).read_text()


class FakeLLM:
    """Scripted stand-in for the chat model: `script` maps a structured-output schema
    name to the list of results to return, in order."""

    def __init__(self):
        self.script: dict[str, list] = {}
        self.calls: list[str] = []
        self.prompts: list[tuple[str, str]] = []  # (schema, all prompt text)

    def queue(self, schema_name: str, *results):
        self.script.setdefault(schema_name, []).extend(results)

    def with_structured_output(self, schema, **kwargs):
        fake = self

        class _Runner:
            def invoke(self, prompt):
                fake.calls.append(schema.__name__)
                fake.prompts.append((schema.__name__, "\n".join(m.content for m in prompt)))
                queue = fake.script.get(schema.__name__)
                assert queue, f"unscripted LLM call for {schema.__name__}"
                result = queue.pop(0)
                return schema(**result) if isinstance(result, dict) else result

        return _Runner()


@pytest.fixture
def llm():
    return FakeLLM()


@pytest.fixture
def client(tmp_path, monkeypatch, llm):
    """API client on in-memory SQLite and a temp data dir (no Postgres needed)."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "render_previews", False)  # Chromium is only used in test_preview
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    app.state.graph = build_graph(llm, InMemorySaver(), Session)
    yield TestClient(app)
    app.dependency_overrides.clear()
