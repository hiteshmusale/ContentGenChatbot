import pytest

from workflows.render import fill_email_template, render_preview


def test_preview_renders_png_without_network(tmp_path):
    html = fill_email_template(
        brand_name="X", colors={}, fonts={}, logo_src=None, subject="s", preheader="p", headline="Hi",
        body=["<script>document.title='ran'</script>"], claims=[], cta_text="Go", cta_url="#",
        isi="ISI", footer_lines=[],
    )
    out = tmp_path / "p.png"
    if not render_preview(html, out):
        pytest.skip("Chromium not installed (run: uv run playwright install chromium)")
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" and out.stat().st_size > 2000


def test_uploaded_emails_get_a_preview_through_the_real_async_endpoint(client, monkeypatch):
    """Regression: the upload endpoint is async, and Playwright's sync API fails inside a
    running event loop, so uploads silently had no preview. Other tests switch previews off."""
    from backend.config import settings

    monkeypatch.setattr(settings, "render_previews", True)
    wid = client.post("/workspaces", json={"name": "Previews"}).json()["id"]
    html = "<html><body><table><tr><td><h1>Hello</h1></td></tr></table></body></html>"
    asset = client.post(f"/workspaces/{wid}/assets", files={"file": ("a.html", html.encode(), "text/html")}).json()
    if not asset["has_preview"]:
        from workflows.render import render_preview  # distinguish "no Chromium" from the bug itself

        import tempfile, pathlib

        if not render_preview(html, pathlib.Path(tempfile.mkdtemp()) / "p.png"):
            pytest.skip("Chromium not installed")
    assert asset["has_preview"] is True
    png = client.get(f"/assets/{asset['id']}/preview")
    assert png.status_code == 200 and png.content[:4] == b"\x89PNG"
