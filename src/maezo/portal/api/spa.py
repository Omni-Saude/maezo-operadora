"""Same-origin delivery of the portal web bundle (`src/maezo/portal/web`, `vite build`).

The BFF's session and CSRF cookies are `__Host-` and same-origin by design, so the SPA is served
by this very process on the public origin instead of a second host. Rules:

- Only `GET`/`HEAD` outside `/api`. Anything under `/api` that no API route matched stays a plain
  404 — the SPA fallback never answers for the API.
- A path is served only if it resolves to a regular file INSIDE the bundle root. Traversal
  (`..`, encoded or not), backslashes, NUL and dot-files are refused with 404 before touching disk.
- Extensionless paths are client routes and get `index.html`; a missing file with an extension is
  a 404 (a stale `/assets/x.js` must never be answered with HTML).
- `index.html` is `no-store`; hashed files under `/assets/` are `immutable` for a year.
- CSP is strict (no `unsafe-inline`), frames are refused, and the headers are the SPA's own: the
  API's `default-src 'none'` CSP would forbid the bundle's scripts if both were sent.
"""

from __future__ import annotations

import logging
import mimetypes
import stat
from pathlib import Path
from typing import Final

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response

_LOG = logging.getLogger("maezo.portal.web")

#: Where `deploy/Dockerfile` copies `dist/`. Fixed path inside the image, not configuration.
DEFAULT_WEB_ROOT: Final = Path("/app/portal-web")

CSP: Final = (
    "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
    "img-src 'self' data:; font-src 'self'; object-src 'none'; base-uri 'none'; "
    "form-action 'self'; frame-ancestors 'none'"
)
SECURITY_HEADERS: Final = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
}
NO_STORE: Final = "no-store"
IMMUTABLE: Final = "public, max-age=31536000, immutable"


def is_api_path(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


class WebBundle:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve(strict=True)
        self.index = self.root / "index.html"
        if not self.index.is_file():
            raise FileNotFoundError("index.html")

    def _headers(self, cache: str) -> dict[str, str]:
        return {**SECURITY_HEADERS, "Cache-Control": cache}

    def not_found(self) -> Response:
        return Response(
            "Not Found", status_code=404, media_type="text/plain", headers=self._headers(NO_STORE)
        )

    def _resolve(self, relative: str) -> Path | None:
        if "\\" in relative or "\x00" in relative:
            return None
        parts = [p for p in relative.split("/") if p]
        if any(p in (".", "..") or p.startswith(".") for p in parts):
            return None
        candidate = self.root.joinpath(*parts) if parts else self.index
        try:
            resolved = candidate.resolve(strict=True)
            mode = resolved.stat().st_mode
        except (OSError, RuntimeError):
            return None
        if not resolved.is_relative_to(self.root) or not stat.S_ISREG(mode):
            return None
        return resolved

    def respond(self, path: str) -> Response:
        relative = path.lstrip("/")
        if not relative or relative == "index.html":
            return self._file(self.index)
        found = self._resolve(relative)
        if found is not None:
            return self._file(found)
        last = relative.rsplit("/", 1)[-1]
        if "." in last or relative.startswith("assets/"):
            return self.not_found()
        if "\\" in relative or "\x00" in relative or any(p in (".", "..") for p in relative.split("/")):
            return self.not_found()
        return self._file(self.index)

    def _file(self, file: Path) -> Response:
        if file == self.index:
            cache = NO_STORE
        elif file.parent == self.root / "assets":
            cache = IMMUTABLE
        else:
            cache = "no-cache"
        media = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
        return FileResponse(file, media_type=media, headers=self._headers(cache))


def load_bundle(root: Path | None, *, production: bool) -> WebBundle | None:
    """None when there is nothing to serve. Missing bundle in production is logged loudly: the API
    keeps working, and every non-API path is a 404 (never a directory listing or a guessed file)."""
    target = root if root is not None else (DEFAULT_WEB_ROOT if production else None)
    if target is None:
        return None
    try:
        return WebBundle(target)
    except (OSError, RuntimeError):
        if production:
            _LOG.error("portal_web_bundle_missing root=%s", target)
        else:
            _LOG.warning("portal_web_bundle_missing root=%s", target)
        return None


def install(app: FastAPI, bundle: WebBundle) -> None:
    async def serve(request: Request) -> Response:
        path = request.scope.get("path", "")
        if is_api_path(path):
            return Response('{"detail":"Not Found"}', status_code=404, media_type="application/json")
        return bundle.respond(path)

    app.add_api_route("/{path:path}", serve, methods=["GET", "HEAD"], include_in_schema=False)
