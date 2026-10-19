from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.models import Workspace
from backend.schemas import WorkspaceIn, WorkspaceOut

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.post("", response_model=WorkspaceOut, status_code=201)
def create_workspace(body: WorkspaceIn, db: Session = Depends(get_db)):
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "name is required")
    ws = Workspace(name=name)
    db.add(ws)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, f"workspace '{name}' already exists")
    return ws


@router.get("", response_model=list[WorkspaceOut])
def list_workspaces(db: Session = Depends(get_db)):
    return list(db.scalars(select(Workspace).order_by(Workspace.id)))
