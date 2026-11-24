"""Serve the one-page web UI (Plan 5): four read-only routes and strict security headers.

The files in app/ui/ are read into memory when this module is imported (editing them needs a restart). Only files
whose names match NAME_PATTERN are ever served under /ui/, from that in-memory table, so there is no path to look up on
disk and nothing to traverse. REST responses are not touched: the headers below are sent by the UI routes only."""
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response

UI_DIR = Path(__file__).parent / "ui"
NAME_PATTERN = re.compile(r"[a-z0-9_-]+\.(js|css)")
CONTENT_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}
HTML_TYPE = "text/html; charset=utf-8"

UI_HEADERS = {
    "Content-Security-Policy": ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
                                "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def _load() -> tuple[bytes, dict[str, tuple[bytes, str]]]:
    index = b""
    files: dict[str, tuple[bytes, str]] = {}
    if not UI_DIR.is_dir():
        return index, files
    for path in sorted(UI_DIR.iterdir()):
        if path.is_symlink() or not path.is_file():
            continue
        if path.name == "index.html":
            index = path.read_bytes()
        elif NAME_PATTERN.fullmatch(path.name):
            files[path.name] = (path.read_bytes(), CONTENT_TYPES[path.suffix])
    return index, files


INDEX_HTML, UI_FILES = _load()


def lookup(name: str) -> tuple[bytes, str] | None:
    """The served body and content type for an asset name, or None for anything that is not a listed file."""
    if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
        return None
    return UI_FILES.get(name)


def _response(body: bytes, content_type: str) -> Response:
    return Response(content=body, headers={**UI_HEADERS, "Content-Type": content_type})


def install_ui(app: FastAPI) -> None:
    """Register GET / and GET /ui/{name}. Neither route appears in the OpenAPI document."""

    @app.get("/", include_in_schema=False)
    def ui_index():
        return _response(INDEX_HTML, HTML_TYPE)

    @app.get("/ui/{name}", include_in_schema=False)
    def ui_asset(name: str):
        found = lookup(name)
        if found is None:
            raise HTTPException(404, "Not Found")
        return _response(*found)
