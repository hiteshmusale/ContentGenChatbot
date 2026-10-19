import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.models import ChatSession, Workspace
from backend.schemas import ChatMessageOut, ChatStateOut, MessageIn, PlanDecisionIn, SessionOut

log = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])


def get_graph(request: Request):
    return request.app.state.graph


def _session(session_id: str, db: Session = Depends(get_db)) -> ChatSession:
    s = db.get(ChatSession, session_id)
    if s is None:
        raise HTTPException(404, "session not found")
    return s


def _config(s: ChatSession) -> dict:
    return {"configurable": {"thread_id": s.id}}


def _state_out(graph, s: ChatSession) -> ChatStateOut:
    snap = graph.get_state(_config(s))
    values = snap.values or {}
    awaiting = "approval" in (snap.next or ())
    msgs = [
        ChatMessageOut(
            role="user" if isinstance(m, HumanMessage) else "assistant",
            content=m.content if isinstance(m.content, str) else str(m.content),
        )
        for m in values.get("messages", [])
        if isinstance(m, (HumanMessage, AIMessage))
    ]
    return ChatStateOut(
        session_id=s.id,
        messages=msgs,
        awaiting_approval=awaiting,
        plan=values.get("plan") if awaiting else None,
        result=values.get("result"),
    )


def _run(graph, s: ChatSession, payload) -> ChatStateOut:
    try:
        graph.invoke(payload, _config(s))
    except Exception as e:  # LLM/network failures surface as a clean 502
        log.exception("agent run failed")
        raise HTTPException(502, f"agent run failed: {type(e).__name__}: {e}")
    return _state_out(graph, s)


@router.post("/workspaces/{workspace_id}/sessions", response_model=SessionOut, status_code=201)
def create_session(workspace_id: int, db: Session = Depends(get_db)):
    if db.get(Workspace, workspace_id) is None:
        raise HTTPException(404, "workspace not found")
    s = ChatSession(id=str(uuid.uuid4()), workspace_id=workspace_id)
    db.add(s)
    db.commit()
    return s


@router.get("/workspaces/{workspace_id}/sessions", response_model=list[SessionOut])
def list_sessions(workspace_id: int, db: Session = Depends(get_db)):
    return list(
        db.scalars(
            select(ChatSession)
            .where(ChatSession.workspace_id == workspace_id)
            .order_by(ChatSession.created_at.desc())
        )
    )


@router.get("/sessions/{session_id}", response_model=ChatStateOut)
def get_session(s: ChatSession = Depends(_session), graph=Depends(get_graph)):
    return _state_out(graph, s)


@router.post("/sessions/{session_id}/messages", response_model=ChatStateOut)
def post_message(
    body: MessageIn,
    s: ChatSession = Depends(_session),
    graph=Depends(get_graph),
    db: Session = Depends(get_db),
):
    text = body.content.strip()
    if not text:
        raise HTTPException(422, "message is empty")
    if s.title == "New chat":
        s.title = text[:60]
        db.commit()
    snap = graph.get_state(_config(s))
    if "approval" in (snap.next or ()):
        # typing while a plan is pending means "change the plan"
        return _run(graph, s, Command(resume={"decision": "edit", "feedback": text}))
    return _run(graph, s, {"messages": [HumanMessage(text)], "workspace_id": s.workspace_id})


@router.post("/sessions/{session_id}/plan", response_model=ChatStateOut)
def decide_plan(body: PlanDecisionIn, s: ChatSession = Depends(_session), graph=Depends(get_graph)):
    if "approval" not in (graph.get_state(_config(s)).next or ()):
        raise HTTPException(409, "no plan is awaiting approval")
    return _run(graph, s, Command(resume=body.model_dump()))
