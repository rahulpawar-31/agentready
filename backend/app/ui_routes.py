"""Serve the one-page web UI (Plan 5): four read-only routes and strict security headers.

The files in app/ui/ are read into memory when this module is imported (editing them needs a restart). Only files
whose names match NAME_PATTERN are ever served under /ui/, from that in-memory table, so there is no path to look up on
disk and nothing to traverse. REST responses are not touched: the headers below are sent by the UI routes only."""
import os
import re
import stat
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
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
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()",
}
NOT_FOUND_BODY = b'{"detail":"Not Found"}'


def _load() -> tuple[bytes, dict[str, tuple[bytes, str]]]:
    index = b""
    files: dict[str, tuple[bytes, str]] = {}
    if (not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY")
            or os.open not in os.supports_dir_fd):
        return index, files
    directory_fd = -1
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        directory_fd = os.open(UI_DIR, directory_flags)
    except OSError:
        return index, files
    try:
        for name in sorted(os.listdir(directory_fd)):
            if name != "index.html" and not NAME_PATTERN.fullmatch(name):
                continue
            fd = -1
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)   # O_NONBLOCK: a named pipe must not block start-up
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    continue
                with os.fdopen(fd, "rb") as asset:
                    fd = -1
                    body = asset.read()
            except OSError:
                continue
            finally:
                if fd >= 0:
                    os.close(fd)
            if name == "index.html":
                index = body
            else:
                files[name] = (body, CONTENT_TYPES[Path(name).suffix])
    finally:
        os.close(directory_fd)
    return index, files


INDEX_HTML, UI_FILES = _load()


def lookup(name: str) -> tuple[bytes, str] | None:
    """The served body and content type for an asset name, or None for anything that is not a listed file."""
    if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
        return None
    return UI_FILES.get(name)


def _response(body: bytes, content_type: str, head: bool = False) -> Response:
    """The page or asset. For HEAD the headers are those of GET (including its Content-Length) and the body is empty."""
    headers = {**UI_HEADERS, "Content-Type": content_type}
    if head:
        return Response(content=b"", headers={**headers, "Content-Length": str(len(body))})
    return Response(content=body, headers=headers)


def _not_found(head: bool):
    if head:                                    # the same JSON 404 as any unknown path, without a body
        return Response(content=b"", status_code=404, media_type="application/json", headers={"Content-Length": str(len(NOT_FOUND_BODY))})
    raise HTTPException(404, "Not Found")


def install_ui(app: FastAPI) -> None:
    """Register GET and HEAD for / and /ui/{name}. Neither route appears in the OpenAPI document."""

    @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
    def ui_index(request: Request):
        return _response(INDEX_HTML, HTML_TYPE, head=request.method == "HEAD")

    @app.api_route("/ui/{name}", methods=["GET", "HEAD"], include_in_schema=False)
    def ui_asset(name: str, request: Request):
        head = request.method == "HEAD"
        found = lookup(name)
        if found is None:
            return _not_found(head)
        return _response(*found, head=head)
