from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import storage
from backend.ingestion import DOC_TYPES, ParseError
from backend.models import Document, DocumentVersion


def ingest_document(
    db: Session, workspace_id: int, doc_type: str, filename: str, content: bytes
) -> DocumentVersion:
    """Parse first; only if parsing succeeds do we store the file and cut a new version.

    On ParseError nothing is written and the previously active version stays active.
    """
    parser, model = DOC_TYPES[doc_type]
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise ParseError(["file must be UTF-8 text"])
    rows = parser(text)

    doc = db.scalar(
        select(Document).where(Document.workspace_id == workspace_id, Document.doc_type == doc_type)
    )
    if doc is None:
        doc = Document(workspace_id=workspace_id, doc_type=doc_type)
        db.add(doc)
        db.flush()

    for v in doc.versions:
        v.status = "superseded"
    number = max((v.version for v in doc.versions), default=0) + 1

    version = DocumentVersion(
        document_id=doc.id,
        version=number,
        filename=filename,
        storage_path=storage.save_doc(workspace_id, doc_type, number, filename, content),
        status="active",
    )
    db.add(version)
    db.flush()
    db.add_all(model(workspace_id=workspace_id, version_id=version.id, **row) for row in rows)
    db.commit()
    return version


def active_rows(db: Session, workspace_id: int, doc_type: str) -> list:
    """Structured rows parsed from the currently active version of a document."""
    _, model = DOC_TYPES[doc_type]
    return list(
        db.scalars(
            select(model)
            .join(DocumentVersion, DocumentVersion.id == model.version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(
                model.workspace_id == workspace_id,
                Document.doc_type == doc_type,
                DocumentVersion.status == "active",
            )
            .order_by(model.id)
        )
    )
