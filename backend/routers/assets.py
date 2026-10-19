from bs4 import BeautifulSoup
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, PlainTextResponse, Response
from sqlalchemy.orm import Session

from backend import assets, compliance, storage
from backend.db import get_db
from backend.models import Asset, Review, Validation, Workspace
from backend.schemas import AssetDetail, AssetOut, ReviewIn, SectionsOut, TagsIn, ValidationOut
from workflows import sections
from sqlalchemy import select

router = APIRouter(tags=["assets"])
MAX_HTML = 2_000_000
MAX_LOGO = 1_000_000


def _workspace(workspace_id: int, db: Session = Depends(get_db)) -> Workspace:
    ws = db.get(Workspace, workspace_id)
    if ws is None:
        raise HTTPException(404, "workspace not found")
    return ws


def _asset(asset_id: int, db: Session = Depends(get_db)) -> Asset:
    a = db.get(Asset, asset_id)
    if a is None:
        raise HTTPException(404, "asset not found")
    return a


@router.post("/workspaces/{workspace_id}/assets", response_model=AssetOut, status_code=201)
async def upload_asset(
    file: UploadFile = File(...), ws: Workspace = Depends(_workspace), db: Session = Depends(get_db)
):
    raw = await file.read()
    if len(raw) > MAX_HTML:
        raise HTTPException(413, "email HTML is too large (2 MB max)")
    try:
        html = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(422, "file must be UTF-8 HTML")
    if BeautifulSoup(html, "html.parser").find(["html", "body", "table", "div", "p"]) is None:
        raise HTTPException(422, "this doesn't look like an HTML email")
    name = (file.filename or "uploaded email").rsplit(".", 1)[0]
    # Storing renders a preview with Playwright's sync API, which cannot run inside this
    # async endpoint's event loop, so do the blocking work in a worker thread.
    return await run_in_threadpool(_store_upload, db, ws.id, name, html)


def _store_upload(db: Session, workspace_id: int, name: str, html: str) -> Asset:
    # detect the sections (header, subheaders, claims, CTA, ...) and tag them; a person can correct the tags
    claims = compliance.knowledge_base(db, workspace_id).claims
    tagged = sections.auto_tag(html, claims, assets.workspace_brand_name(db, workspace_id))
    asset = assets.create_asset(db, workspace_id, name=name, origin="upload", html=html, tag_status="suggested")
    assets.update_html(asset, tagged)  # stores the tagged version; the upload is kept as email.original.html
    db.commit()
    compliance.validate_asset(db, asset)
    return asset


@router.post("/workspaces/{workspace_id}/assets/sample", response_model=AssetOut, status_code=201)
def create_sample(ws: Workspace = Depends(_workspace), db: Session = Depends(get_db)):
    try:
        asset = assets.build_sample_email(db, ws.id)
    except ValueError as e:
        raise HTTPException(422, str(e))
    compliance.validate_asset(db, asset)
    return asset


@router.get("/workspaces/{workspace_id}/assets", response_model=list[AssetOut])
def list_assets(ws: Workspace = Depends(_workspace), db: Session = Depends(get_db)):
    return assets.list_assets(db, ws.id)


@router.get("/assets/{asset_id}/html", response_class=PlainTextResponse)
def get_html(a: Asset = Depends(_asset)):
    # served as text/plain on purpose: uploaded HTML must never execute on the API origin
    return assets.asset_html(a)


@router.get("/assets/{asset_id}/preview")
def get_preview(a: Asset = Depends(_asset)):
    if not a.preview_path:
        raise HTTPException(404, "no preview for this asset")
    return FileResponse(storage.abs_path(a.preview_path), media_type="image/png")


@router.post("/workspaces/{workspace_id}/logos", status_code=201)
async def upload_logo(file: UploadFile = File(...), ws: Workspace = Depends(_workspace)):
    raw = await file.read()
    if len(raw) > MAX_LOGO:
        raise HTTPException(413, "logo is too large (1 MB max)")
    try:
        return {"name": assets.save_logo(ws.id, file.filename or "logo.png", raw)}
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.get("/workspaces/{workspace_id}/logos")
def list_logos(ws: Workspace = Depends(_workspace)):
    return assets.list_logos(ws.id)


def _detail(db: Session, a: Asset) -> AssetDetail:
    vals = db.scalars(select(Validation).where(Validation.asset_id == a.id).order_by(Validation.id.desc()))
    revs = db.scalars(select(Review).where(Review.asset_id == a.id).order_by(Review.id.desc()))
    return AssetDetail.model_validate(
        {**AssetOut.model_validate(a).model_dump(), "meta": a.meta, "validations": list(vals), "reviews": list(revs)},
        from_attributes=True,
    )


@router.get("/assets/{asset_id}", response_model=AssetDetail)
def asset_detail(a: Asset = Depends(_asset), db: Session = Depends(get_db)):
    return _detail(db, a)


@router.post("/assets/{asset_id}/validate", response_model=ValidationOut)
def validate_asset(a: Asset = Depends(_asset), db: Session = Depends(get_db)):
    return compliance.validate_asset(db, a)


@router.post("/assets/{asset_id}/review", response_model=AssetDetail)
def review_asset(body: ReviewIn, a: Asset = Depends(_asset), db: Session = Depends(get_db)):
    try:
        compliance.review_asset(db, a, body.decision, body.comments, body.reviewer)
    except compliance.ReviewBlocked as e:
        errors = [f for f in e.validation.findings if f["severity"] == "error"]
        raise HTTPException(409, {"message": "cannot approve: compliance errors", "findings": errors})
    return _detail(db, a)


@router.get("/assets/{asset_id}/export")
def export_asset(a: Asset = Depends(_asset)):
    if a.status != "approved":
        raise HTTPException(409, f"export is only available once approved (status: {a.status})")
    return Response(
        sections.strip_internal(assets.asset_html(a)),  # recipients get clean HTML, without our tags
        media_type="text/html",
        headers={"Content-Disposition": f'attachment; filename="email-{a.id}.html"'},
    )


def _sections_out(db: Session, a: Asset) -> SectionsOut:
    html = assets.asset_html(a)
    kb = compliance.knowledge_base(db, a.workspace_id)
    cands = sections.candidate_list(html, kb.claims, assets.workspace_brand_name(db, a.workspace_id))
    return SectionsOut(
        tag_status=a.tag_status,
        sections=[vars(s) for s in sections.list_sections(html)],
        candidates=[vars(c) for c in cands],
    )


def _retag(db: Session, a: Asset, tags: dict[int, str | None], status: str) -> SectionsOut:
    kb = compliance.knowledge_base(db, a.workspace_id)
    try:
        html = sections.apply_tags(assets.asset_html(a), tags, kb.claims)
    except ValueError as e:
        raise HTTPException(422, str(e))
    assets.update_html(a, html)
    a.tag_status = status
    db.commit()
    compliance.validate_asset(db, a)  # tags change how claims are checked
    return _sections_out(db, a)


@router.get("/assets/{asset_id}/sections", response_model=SectionsOut)
def get_sections(a: Asset = Depends(_asset), db: Session = Depends(get_db)):
    return _sections_out(db, a)


@router.post("/assets/{asset_id}/tags/suggest", response_model=SectionsOut)
def suggest_tags(a: Asset = Depends(_asset), db: Session = Depends(get_db)):
    """(Re-)run auto-detection and apply it, replacing any existing tags."""
    kb = compliance.knowledge_base(db, a.workspace_id)
    cands = sections.candidate_list(assets.asset_html(a), kb.claims, assets.workspace_brand_name(db, a.workspace_id))
    return _retag(db, a, {c.eid: c.suggested for c in cands}, "suggested")


@router.put("/assets/{asset_id}/tags", response_model=SectionsOut)
def set_tags(body: TagsIn, a: Asset = Depends(_asset), db: Session = Depends(get_db)):
    """Replace all tags with the given ones (anything not listed becomes untagged)."""
    return _retag(db, a, {t.eid: t.type for t in body.tags}, "confirmed")
