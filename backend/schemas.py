from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class _ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class WorkspaceIn(BaseModel):
    name: str


class WorkspaceOut(_ORM):
    id: int
    name: str
    created_at: datetime


class VersionOut(_ORM):
    id: int
    version: int
    filename: str
    status: str
    created_at: datetime


class DocumentOut(_ORM):
    id: int
    doc_type: str
    versions: list[VersionOut]


class BrandTokenOut(_ORM):
    category: str
    key: str
    value: str


class PersonaOut(_ORM):
    name: str
    description: str
    tone: str
    language: str
    concerns: list[str]


class ClaimOut(_ORM):
    claim_id: str
    product: str
    text: str
    reference: str


class RuleOut(_ORM):
    rule_type: str
    name: str | None
    value: str


class SessionOut(_ORM):
    id: str
    workspace_id: int
    title: str
    created_at: datetime


class MessageIn(BaseModel):
    content: str


class PlanDecisionIn(BaseModel):
    decision: Literal["approve", "reject", "edit"]
    feedback: str | None = None


class ChatMessageOut(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatStateOut(BaseModel):
    session_id: str
    messages: list[ChatMessageOut]
    awaiting_approval: bool
    plan: dict | None
    result: dict | None


class AssetOut(_ORM):
    id: int
    workspace_id: int
    name: str
    origin: str
    parent_asset_id: int | None
    has_preview: bool
    status: str
    tag_status: str
    created_at: datetime


class FindingOut(BaseModel):
    severity: Literal["error", "warning"]
    code: str
    message: str


class ValidationOut(_ORM):
    id: int
    passed: bool
    findings: list[FindingOut]
    created_at: datetime


class ReviewIn(BaseModel):
    decision: Literal["approved", "rejected"]
    comments: str = ""
    reviewer: str = "reviewer"


class ReviewOut(_ORM):
    id: int
    decision: str
    comments: str
    reviewer: str
    created_at: datetime


class AssetDetail(AssetOut):
    meta: dict
    validations: list[ValidationOut]
    reviews: list[ReviewOut]


class SectionOut(BaseModel):
    type: str
    ordinal: int
    count: int
    text: str


class CandidateOut(BaseModel):
    eid: int
    tag: str
    text: str
    type: str | None
    suggested: str | None


class SectionsOut(BaseModel):
    tag_status: str
    sections: list[SectionOut]
    candidates: list[CandidateOut]


class TagIn(BaseModel):
    eid: int
    type: str | None = None


class TagsIn(BaseModel):
    tags: list[TagIn]
