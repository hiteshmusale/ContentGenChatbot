"""Template fill and preview rendering. Used by the sample-email builder now and by the
generate workflow later."""
import base64
import logging
import mimetypes
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

log = logging.getLogger(__name__)
TEMPLATE_DIR = Path(__file__).parent / "templates"
_env = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    autoescape=select_autoescape(["html"]),
    undefined=StrictUndefined,
)

DEFAULT_COLORS = {"primary": "#0B5CAD", "secondary": "#E8F1FA", "accent": "#D9232E", "text": "#1F2933"}
DEFAULT_FONTS = {"heading": "Georgia, serif", "body": "Arial, Helvetica, sans-serif"}


def data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def fill_email_template(
    *,
    brand_name: str,
    colors: dict[str, str],
    fonts: dict[str, str],
    logo_src: str | None,
    subject: str,
    preheader: str,
    headline: str,
    body: list[str],
    claims: list[dict],
    cta_text: str,
    cta_url: str,
    isi: str,
    footer_lines: list[str],
    unsubscribe_url: str = "#unsubscribe",
) -> str:
    """Fill the single 600px email template. Claim text is inserted exactly as given
    (HTML-escaped only), tagged with its claim id so it can be verified later."""
    return _env.get_template("email_600.html").render(
        brand_name=brand_name,
        color={**DEFAULT_COLORS, **colors},
        font={**DEFAULT_FONTS, **fonts},
        logo_src=logo_src,
        subject=subject,
        preheader=preheader,
        headline=headline,
        body=body,
        claims=claims,
        cta_text=cta_text,
        cta_url=cta_url,
        isi=isi,
        footer_lines=footer_lines,
        unsubscribe_url=unsubscribe_url,
    )


def render_preview(html: str, out_path: Path) -> bool:
    """Screenshot the email to a PNG. JavaScript and network are disabled, since the HTML
    may be user-uploaded or model-influenced. Returns False (never raises) if rendering
    is unavailable."""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                ctx = browser.new_context(java_script_enabled=False, viewport={"width": 680, "height": 800})
                ctx.route("**/*", lambda route: route.abort() if not route.request.url.startswith("data:") else route.continue_())
                page = ctx.new_page()
                page.set_content(html, wait_until="load")
                out_path.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(out_path), full_page=True)
            finally:
                browser.close()
        return True
    except Exception:
        log.exception("preview rendering failed")
        return False
