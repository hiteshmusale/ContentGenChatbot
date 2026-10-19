from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import service
from backend.db import get_db
from backend.ingestion import ParseError
from backend.models import Document, Workspace
from backend.schemas import (
    BrandTokenOut,
    ClaimOut,
    DocumentOut,
    PersonaOut,
    RuleOut,
    VersionOut,
)

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["documents"])

DocType = Literal["brand", "persona", "claims", "rules"]


def _workspace(workspace_id: int, db: Session = Depends(get_db)) -> Workspace:
    ws = db.get(Workspace, workspace_id)
    if ws is None:
        raise HTTPException(404, "workspace not found")
    return ws


@router.post("/documents", response_model=VersionOut, status_code=201)
async def upload_document(
    doc_type: DocType = Form(...),
    file: UploadFile = File(...),
    ws: Workspace = Depends(_workspace),
    db: Session = Depends(get_db),
):
    content = await file.read()
    try:
        return service.ingest_document(db, ws.id, doc_type, file.filename or "upload.md", content)
    except ParseError as e:
        db.rollback()
        raise HTTPException(422, {"message": f"could not parse {doc_type} document", "errors": e.errors})


@router.get("/documents", response_model=list[DocumentOut])
def list_documents(ws: Workspace = Depends(_workspace), db: Session = Depends(get_db)):
    return list(db.scalars(select(Document).where(Document.workspace_id == ws.id)))


@router.get("/brand", response_model=list[BrandTokenOut])
def get_brand(ws: Workspace = Depends(_workspace), db: Session = Depends(get_db)):
    return service.active_rows(db, ws.id, "brand")


@router.get("/personas", response_model=list[PersonaOut])
def get_personas(ws: Workspace = Depends(_workspace), db: Session = Depends(get_db)):
    return service.active_rows(db, ws.id, "persona")


@router.get("/claims", response_model=list[ClaimOut])
def get_claims(ws: Workspace = Depends(_workspace), db: Session = Depends(get_db)):
    return service.active_rows(db, ws.id, "claims")


@router.get("/rules", response_model=list[RuleOut])
def get_rules(ws: Workspace = Depends(_workspace), db: Session = Depends(get_db)):
    return service.active_rows(db, ws.id, "rules")
