"""Stored emails (assets) and workspace logo files."""
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import service, storage
from backend.models import Asset
from workflows import render
from workflows.adapt import AdaptEnv

LOGO_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".svg"}


# ---- logos -------------------------------------------------------------------------

def _logo_dir(workspace_id: int) -> Path:
    return storage.abs_path(f"workspaces/{workspace_id}/logos")


def save_logo(workspace_id: int, filename: str, content: bytes) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(filename).name)
    if Path(name).suffix.lower() not in LOGO_EXTS:
        raise ValueError(f"logo must be one of: {', '.join(sorted(LOGO_EXTS))}")
    storage.write_file(f"workspaces/{workspace_id}/logos/{name}", content)
    return name


def list_logos(workspace_id: int) -> list[str]:
    d = _logo_dir(workspace_id)
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


def logo_data_uri(workspace_id: int, name: str) -> str | None:
    path = _logo_dir(workspace_id) / Path(name).name  # basename only: no path traversal
    return render.data_uri(path) if path.is_file() else None


# ---- assets ------------------------------------------------------------------------

def create_asset(
    db: Session,
    workspace_id: int,
    *,
    name: str,
    origin: str,
    html: str,
    session_id: str | None = None,
    parent_id: int | None = None,
    meta: dict | None = None,
    tag_status: str = "untagged",
) -> Asset:
    from backend.config import settings

    asset = Asset(
        workspace_id=workspace_id, session_id=session_id, name=name, origin=origin,
        parent_asset_id=parent_id, html_path="", meta=meta or {}, tag_status=tag_status,
    )
    db.add(asset)
    db.flush()
    base = f"workspaces/{workspace_id}/assets/{asset.id}"
    asset.html_path = storage.write_file(f"{base}/email.html", html.encode("utf-8"))
    if settings.render_previews and render.render_preview(html, storage.abs_path(f"{base}/preview.png")):
        asset.preview_path = f"{base}/preview.png"
    db.commit()
    return asset


def update_html(asset: Asset, html: str) -> None:
    """Rewrite an email's HTML in place (used when tags change; the content is the same).
    The first version is kept once as email.original.html."""
    base = asset.html_path.rsplit("/", 1)[0]
    if "original_html" not in (asset.meta or {}):
        storage.write_file(f"{base}/email.original.html", storage.read_file(asset.html_path))
        asset.meta = {**(asset.meta or {}), "original_html": f"{base}/email.original.html"}
    storage.write_file(asset.html_path, html.encode("utf-8"))


def workspace_brand_name(db: Session, workspace_id: int) -> str | None:
    return next(
        (t.value for t in service.active_rows(db, workspace_id, "brand") if t.category == "brand" and t.key == "name"),
        None,
    )


def asset_html(asset: Asset) -> str:
    return storage.read_file(asset.html_path).decode("utf-8")


def list_assets(db: Session, workspace_id: int) -> list[Asset]:
    return list(
        db.scalars(select(Asset).where(Asset.workspace_id == workspace_id).order_by(Asset.id.desc()))
    )


def resolve_asset(db: Session, workspace_id: int, value: str) -> Asset | None:
    """'latest', an id ('3' or '#3'), or a name -> Asset (within the workspace)."""
    assets = list_assets(db, workspace_id)
    v = value.strip()
    if v.lower() in ("latest", "last", "most recent", "the latest", "it", ""):
        return assets[0] if assets else None
    # "3", "#3" or the option label "#3 My email": use the id if such an email exists,
    # otherwise fall through to matching by name (a name may itself start with a number)
    m = re.match(r"#?(\d+)(?:\s|$)", v)
    if m and (hit := next((a for a in assets if a.id == int(m.group(1))), None)):
        return hit
    return next((a for a in assets if a.name.lower() == v.lower()), None) or next(
        (a for a in assets if v.lower() in a.name.lower()), None
    )


def adapt_env(db: Session, workspace_id: int) -> AdaptEnv:
    isi = next(
        (r.value for r in service.active_rows(db, workspace_id, "rules")
         if r.rule_type == "required_block" and (r.name or "").lower() == "isi"),
        None,
    )
    return AdaptEnv(isi_text=isi, load_logo=lambda name: logo_data_uri(workspace_id, name))


DEFAULT_CTA_URL = "https://example.com/learn-more"


def render_email(
    db: Session,
    workspace_id: int,
    *,
    subject: str,
    preheader: str,
    headline: str,
    body: list[str],
    claims: list[dict],
    cta_text: str,
    cta_url: str = DEFAULT_CTA_URL,
) -> str:
    """Fill the email template with the workspace's brand, ISI and footer text. Claims are
    inserted exactly as given; only the copy fields come from the caller."""
    brand: dict[str, dict[str, str]] = {}
    for t in service.active_rows(db, workspace_id, "brand"):
        brand.setdefault(t.category, {})[t.key] = t.value
    rules = service.active_rows(db, workspace_id, "rules")
    is_isi = lambda r: r.rule_type == "required_block" and (r.name or "").lower() == "isi"  # noqa: E731
    isi = next((r.value for r in rules if is_isi(r)), None)
    if not brand or isi is None:
        raise ValueError("upload brand.md and compliance_rules.md first")
    footer = [r.value for r in rules if r.rule_type == "required_block" and not is_isi(r)]
    footer += [r.value for r in rules if r.rule_type == "mandatory_text"]
    logo_file = brand.get("logo", {}).get("path")
    return render.fill_email_template(
        brand_name=brand.get("brand", {}).get("name", "Brand"),
        colors=brand.get("color", {}),
        fonts=brand.get("font", {}),
        logo_src=logo_data_uri(workspace_id, logo_file) if logo_file else None,
        subject=subject, preheader=preheader, headline=headline, body=body, claims=claims,
        cta_text=cta_text, cta_url=cta_url, isi=isi, footer_lines=footer,
    )


def build_sample_email(db: Session, workspace_id: int) -> Asset:
    """A ready-made email built from the workspace's knowledge base, so edits can be tried
    without generating one first."""
    claims = service.active_rows(db, workspace_id, "claims")
    if not claims:
        raise ValueError("upload brand.md, claims.md and compliance_rules.md first")
    product = claims[0].product
    try:
        html = render_email(
            db, workspace_id,
            subject=f"Introducing {product}", preheader=f"Learn more about {product}",
            headline=f"Introducing {product}",
            body=["This is a sample email built from your knowledge base so you can try edits on it."],
            claims=[{"claim_id": c.claim_id, "text": c.text} for c in claims if c.product == product],
            cta_text="Learn more",
        )
    except ValueError:
        raise ValueError("upload brand.md, claims.md and compliance_rules.md first")
    brand_name = workspace_brand_name(db, workspace_id) or "Brand"
    return create_asset(
        db, workspace_id, name=f"{brand_name} sample email", origin="sample", html=html, tag_status="tagged"
    )
