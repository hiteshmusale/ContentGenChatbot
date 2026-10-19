from contextlib import asynccontextmanager

from fastapi import FastAPI
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from agent.graph import build_graph
from agent.llm import get_llm
from backend.config import settings
from backend.db import SessionLocal
from backend.routers import assets, chat, documents, workspaces


@asynccontextmanager
async def lifespan(app: FastAPI):
    conninfo = settings.database_url.replace("postgresql+psycopg://", "postgresql://")
    pool = ConnectionPool(
        conninfo,
        max_size=10,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        open=True,
    )
    saver = PostgresSaver(pool)
    saver.setup()  # creates the checkpoint tables if missing
    app.state.graph = build_graph(get_llm(), saver, SessionLocal)
    yield
    pool.close()


app = FastAPI(title="Pharma Content Chatbot", lifespan=lifespan)
app.include_router(workspaces.router)
app.include_router(documents.router)
app.include_router(assets.router)
app.include_router(chat.router)


@app.get("/health")
def health():
    return {"status": "ok"}
