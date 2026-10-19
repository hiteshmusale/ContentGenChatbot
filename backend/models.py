from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Document(Base):
    """One logical document per (workspace, doc_type); versions hang off it."""

    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("workspace_id", "doc_type"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    doc_type: Mapped[str] = mapped_column(String(20))  # brand | persona | claims | rules
    versions: Mapped[list["DocumentVersion"]] = relationship(
        back_populates="document", order_by="DocumentVersion.version"
    )


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("document_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    filename: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(String(500))  # relative to DATA_DIR
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | superseded
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    document: Mapped[Document] = relationship(back_populates="versions")


# Structured rows are tied to the document version they were parsed from, so
# history is kept and "current" simply means "rows of the active version".


class BrandToken(Base):
    __tablename__ = "brand_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id"), index=True)
    category: Mapped[str] = mapped_column(String(30))  # brand | color | font | logo | tone
    key: Mapped[str] = mapped_column(String(100))
    value: Mapped[str] = mapped_column(Text)


class Persona(Base):
    __tablename__ = "personas"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    tone: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(50))
    concerns: Mapped[list] = mapped_column(JSON, default=list)


class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id"), index=True)
    claim_id: Mapped[str] = mapped_column(String(50))
    product: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text)  # verbatim, never altered
    reference: Mapped[str] = mapped_column(Text)


class Rule(Base):
    __tablename__ = "rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id"), index=True)
    rule_type: Mapped[str] = mapped_column(String(30))  # required_block | banned_phrase | mandatory_text
    name: Mapped[str | None] = mapped_column(String(100))
    value: Mapped[str] = mapped_column(Text)


class ChatSession(Base):
    """Lists chats in the UI. Conversation content lives in the LangGraph checkpointer,
    keyed by this id as the thread_id."""

    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="New chat")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Asset(Base):
    """A stored email (HTML). Edits create a new asset pointing at its parent."""

    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    session_id: Mapped[str | None] = mapped_column(String(36))
    name: Mapped[str] = mapped_column(String(200))
    origin: Mapped[str] = mapped_column(String(20))  # sample | upload | adapt | generate | personalize
    parent_asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id"))
    html_path: Mapped[str] = mapped_column(String(500))  # relative to DATA_DIR
    preview_path: Mapped[str | None] = mapped_column(String(500))
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    # pending_review -> approved | rejected. Export is only allowed once approved.
    status: Mapped[str] = mapped_column(String(20), default="pending_review", server_default="pending_review")
    # How its sections were tagged: tagged (built into our template) | suggested (auto-detected,
    # not yet checked by a person) | confirmed (a person reviewed the tags) | untagged
    tag_status: Mapped[str] = mapped_column(String(20), default="untagged", server_default="untagged")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    @property
    def has_preview(self) -> bool:
        return self.preview_path is not None


class Validation(Base):
    __tablename__ = "validations"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True)
    passed: Mapped[bool]
    findings: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True)
    validation_id: Mapped[int | None] = mapped_column(ForeignKey("validations.id"))
    decision: Mapped[str] = mapped_column(String(20))  # approved | rejected
    comments: Mapped[str] = mapped_column(Text, default="")
    reviewer: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
