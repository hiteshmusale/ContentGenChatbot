"""Runs the validator against a workspace's current knowledge base, records results, and
applies review decisions."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import assets, service
from backend.models import Asset, Review, Validation
from workflows import render
from workflows.validator import KnowledgeBase, validate_email


class ReviewBlocked(Exception):
    def __init__(self, validation: Validation):
        super().__init__("validation has errors")
        self.validation = validation


def knowledge_base(db: Session, workspace_id: int) -> KnowledgeBase:
    rows = lambda t: service.active_rows(db, workspace_id, t)  # noqa: E731
    rules = rows("rules")
    brand = rows("brand")
    return KnowledgeBase(
        claims={c.claim_id: c.text for c in rows("claims")},
        required_blocks={r.name: r.value for r in rules if r.rule_type == "required_block"},
        mandatory_text=[r.value for r in rules if r.rule_type == "mandatory_text"],
        banned_phrases=[r.value for r in rules if r.rule_type == "banned_phrase"],
        brand_colors=[t.value for t in brand if t.category == "color"],
        brand_fonts=[t.value for t in brand if t.category == "font"],
        logo_uris={
            uri for name in assets.list_logos(workspace_id) if (uri := assets.logo_data_uri(workspace_id, name))
        },
    )


def validate_asset(db: Session, asset: Asset) -> Validation:
    """Validate against the knowledge base as it is *now* and keep the result."""
    report = validate_email(assets.asset_html(asset), knowledge_base(db, asset.workspace_id))
    v = Validation(asset_id=asset.id, passed=report.passed, findings=report.as_dict()["findings"])
    db.add(v)
    db.commit()
    return v


def latest_validation(db: Session, asset_id: int) -> Validation | None:
    return db.scalar(
        select(Validation).where(Validation.asset_id == asset_id).order_by(Validation.id.desc())
    )


def review_asset(db: Session, asset: Asset, decision: str, comments: str, reviewer: str) -> Review:
    """Approving re-validates first, so an email checked against an old ISI cannot be
    approved after the rules changed. Rejecting is always allowed."""
    v = validate_asset(db, asset)
    if decision == "approved" and not v.passed:
        raise ReviewBlocked(v)
    review = Review(
        asset_id=asset.id, validation_id=v.id, decision=decision,
        comments=comments.strip(), reviewer=reviewer.strip() or "reviewer",
    )
    asset.status = decision
    db.add(review)
    db.commit()
    return review
