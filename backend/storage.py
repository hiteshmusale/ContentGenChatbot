import re
from pathlib import Path

from backend.config import settings


def save_doc(workspace_id: int, doc_type: str, version: int, filename: str, content: bytes) -> str:
    """Write an upload under DATA_DIR and return its path relative to DATA_DIR."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", Path(filename).name) or "upload.md"
    rel = Path("workspaces") / str(workspace_id) / "docs" / doc_type / f"v{version}_{safe}"
    full = settings.data_dir / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_bytes(content)
    return rel.as_posix()


def write_file(rel: str, content: bytes) -> str:
    full = settings.data_dir / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_bytes(content)
    return rel


def read_file(rel: str) -> bytes:
    return (settings.data_dir / rel).read_bytes()


def abs_path(rel: str) -> Path:
    return settings.data_dir / rel
