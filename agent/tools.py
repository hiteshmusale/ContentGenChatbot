"""Knowledge-base lookups. Plain functions over the structured tables; the graph nodes
call them deterministically so values (personas, products, claim ids) are never guessed."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import service
from backend.models import Document, DocumentVersion

DOC_TYPES = ("brand", "persona", "claims", "rules")


def get_brand(db: Session, workspace_id: int) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for t in service.active_rows(db, workspace_id, "brand"):
        out.setdefault(t.category, {})[t.key] = t.value
    return out


def list_personas(db: Session, workspace_id: int):
    return service.active_rows(db, workspace_id, "persona")


def get_persona(db: Session, workspace_id: int, name: str):
    for p in list_personas(db, workspace_id):
        if p.name.lower() == name.strip().lower():
            return p
    return None


def list_claims(db: Session, workspace_id: int, product: str | None = None):
    claims = service.active_rows(db, workspace_id, "claims")
    if product:
        claims = [c for c in claims if c.product.lower() == product.strip().lower()]
    return claims


def list_products(db: Session, workspace_id: int) -> list[str]:
    return sorted({c.product for c in list_claims(db, workspace_id)})


def get_rules(db: Session, workspace_id: int):
    return service.active_rows(db, workspace_id, "rules")


def active_versions(db: Session, workspace_id: int) -> dict[str, int]:
    """{doc_type: active version number} for every uploaded document type."""
    rows = db.execute(
        select(Document.doc_type, DocumentVersion.version)
        .join(DocumentVersion, DocumentVersion.document_id == Document.id)
        .where(Document.workspace_id == workspace_id, DocumentVersion.status == "active")
    )
    return {t: v for t, v in rows}
