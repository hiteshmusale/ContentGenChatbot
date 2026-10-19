# Pharma Content Chatbot

Chat-driven tool for generating and editing pharma promotional emails. Local portfolio project.
Design: see [TECHNICAL_WRITEUP.md](TECHNICAL_WRITEUP.md).

**Status:** all three swimlanes work: Generate from scratch, Mechanical adaptation (including targeted edits such as "the 2nd subheader"), and Persona personalization, behind a LangGraph agent with a plan-approval gate, section tagging for uploaded emails, a deterministic compliance validator, and reviewer approve/reject with export locked until approval. Long-term memory is not built.

## Run

```bash
docker compose up -d                 # Postgres
uv sync
uv run playwright install chromium   # email previews
uv run alembic upgrade head          # create tables
uv run uvicorn backend.main:app      # API on :8000
PYTHONPATH=. uv run streamlit run ui/app.py   # UI on :8501
```

Needs `ANTHROPIC_API_KEY` (in `.env` or your shell) for the chat page.

Sample (fictional) documents to upload are in `samples/`, including `complex_email.html`, a hard test email for section tagging and targeted edits.

## Test

```bash
uv run pytest        # parsers + API, uses in-memory SQLite, no Postgres needed
```
