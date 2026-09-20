#!/usr/bin/env python3
"""Run the synthetic browser journey of the portal slice (WP-J1-10).

WHAT THIS DRIVES. A real Chromium, against the REAL built SPA bundle (`npm run build` of
`src/maezo/portal/web`) and the REAL FastAPI BFF (`maezo.portal.api.create_app`) with its
production login/callback/session routes, transaction cookies and code single-claim fence.
The ONLY synthetic component is the identity source: the local-test stub authenticator
(`maezo.portal.api.local_auth.StubAuthenticator`) answers one fixed authorization code, and
`scripts` serves the bundle to the browser so no IdP and no network is contacted. The
journey asserts, per ADR-0049, D3 (no browser storage/cookie/URL PHI, no outbound requests)
and D10 (automated WCAG 2.2 AA scan + keyboard operation), and writes an evidence bundle.

WHAT THIS DOES NOT CLAIM. `--leg journey` — the engine-committed intake -> claim -> APROVAR
-> outcome path on the secured fixture — needs the ROOT-supplied custody materials that
every real-engine human-plane suite in this repository requires (`tests/integration/**`
`root_fixture` posture). Without them this script REFUSES with a specific message and a
distinct exit code; it never silently degrades to a smaller claim, and it never fabricates
an engine result. The human VoiceOver pass (D10) is a human step and stays outstanding.

Usage (session leg):
    npm run build -C src/maezo/portal/web        # or: --build
    uv run python scripts/dev/run_portal_journey.py --artifacts /tmp/portal-journey
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = REPO_ROOT / "src/maezo/portal/web"
E2E_DIR = WEB_DIR / "e2e"
FIXTURE = E2E_DIR / "fixtures/journey-fixture.json"

if str(REPO_ROOT / "src") not in sys.path:  # pragma: no cover - import bootstrap
    sys.path.insert(0, str(REPO_ROOT / "src"))

from maezo.portal.api.app import create_app  # noqa: E402
from maezo.portal.api.config import PortalSettings  # noqa: E402
from maezo.portal.api.local_auth import STUB_AUTHORIZATION_CODE, StubAuthenticator  # noqa: E402
from maezo.portal.api.records import MembershipRecord  # noqa: E402
from maezo.portal.api.store import LocalTestIdentityStore  # noqa: E402
from maezo.portal.contracts.models import MembershipBinding  # noqa: E402

# `public_origin` vouches for the Host the BFF enforces; the local origin adds a port.
PUBLIC_HOST_HEADER = "https://localhost"
IDP_ORIGIN = "https://humans.auth.sa-east-1.amazoncognito.com"
SESSION_EXIT_OK = 0
JOURNEY_CUSTODY_REFUSAL = 3


@dataclass(slots=True)
class JourneyIdentity:
    tenant: str
    issuer: str
    subject: str
    principal_ref: str
    audience: str
    roles: tuple[str, ...]
    groups: tuple[str, ...]


def load_identity() -> JourneyIdentity:
    fixture = json.loads(FIXTURE.read_text())
    from maezo.portal.api.local_auth import STUB_ISSUER, STUB_SUBJECT

    if fixture["audience"] != "staff" or fixture["roles"] != ["staff"]:
        raise SystemExit(f"{FIXTURE}: this script seeds the staff session identity only")
    return JourneyIdentity(
        tenant=fixture["tenant"],
        issuer=STUB_ISSUER,
        subject=STUB_SUBJECT,
        principal_ref=fixture["principal_ref"],
        audience=fixture["audience"],
        roles=tuple(fixture["roles"]),
        groups=tuple(fixture["groups"]),
    )


def seeded_store(identity: JourneyIdentity) -> LocalTestIdentityStore:
    store = LocalTestIdentityStore(identity.tenant)
    store.memberships[(identity.issuer, identity.subject)] = MembershipRecord(
        tenant=identity.tenant,
        issuer=identity.issuer,
        subject=identity.subject,
        principal_ref=identity.principal_ref,
        revision=1,
        audience="staff",
        memberships=(
            MembershipBinding(
                membership_ref="review-journey-1", roles=identity.roles, groups=identity.groups
            ),
        ),
        subject_bindings=(),
        reviewed_until=datetime.now(UTC) + timedelta(hours=2),
    )
    return store


def build_app(store: LocalTestIdentityStore, settings: PortalSettings):
    return create_app(settings, store=store, authenticator=StubAuthenticator(settings))


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


async def serve(app, port: int):
    import uvicorn

    configuration = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning", access_log=False
    )
    server = uvicorn.Server(configuration)
    task = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.started:
            return server, task
        await asyncio.sleep(0.1)
    raise SystemExit("the portal BFF never started")


def _npm_environment() -> dict[str, str]:
    """npm from the worktree must not see the parent session's virtualenv."""
    return {
        key: value
        for key, value in os.environ.items()
        if key != "VIRTUAL_ENV" and not key.startswith("MAEZO_PORTAL_E2E_")
    }


def run_playwright(arguments: list[str], artifacts: Path, dist: Path, bff_origin: str) -> int:
    runtime = artifacts / "runtime.json"
    runtime.write_text(
        json.dumps(
            {
                "authorization_code": STUB_AUTHORIZATION_CODE,
                "portal_origin": bff_origin,
                "idp_origin": IDP_ORIGIN,
                "written_at": datetime.now(UTC).isoformat(),
            },
            indent=2,
        )
        + "\n"
    )
    environment = {
        key: value for key, value in _npm_environment().items()
    }
    environment.update(
        {
            "MAEZO_PORTAL_E2E_RUNTIME": str(artifacts),
            "MAEZO_PORTAL_E2E_ARTIFACTS": str(artifacts),
            "MAEZO_PORTAL_E2E_DIST": str(dist),
            "MAEZO_PORTAL_E2E_ORIGIN": bff_origin,
            "npm_config_yes": "false",
        }
    )
    if Path("/Applications/Google Chrome.app").exists():
        environment["MAEZO_PORTAL_E2E_CHANNEL"] = "chrome"
    completed = subprocess.run(
        ["npx", "playwright", "test", "--config", "e2e/playwright.config.ts", *arguments],
        cwd=WEB_DIR,
        env=environment,
        check=False,
    )
    return completed.returncode


class StaticOrigin:
    """Local TLS-terminator stand-in: serves the bundle and forwards /api to the BFF.

    The production portal is deployed with the SPA separate from the BFF, behind a proxy
    that terminates TLS and fixes the Host the BFF enforces. This wrapper is the harness's
    version of that proxy: ONE local server answers the browser at `http://localhost:<port>`,
    serving the built bundle for SPA paths and delegating every `/api/` request to the real
    app with `Host: localhost` (the value `public_origin` vouches for). It owns no policy:
    every auth/session/cookie decision stays in the BFF.
    """

    def __init__(self, app, dist: Path, overlay: dict[str, Path] | None = None) -> None:
        self.app = app
        self.dist = dist
        self._overlay = overlay or {}
        self._spa = {"/", "/portal", "/portal/"}

    def _bundle(self, path: str) -> tuple[bytes, str] | None:
        overlaid = self._overlay.get(path)
        if overlaid is not None:
            return overlaid.read_bytes(), self._type(overlaid.name)
        candidate = (self.dist / path.lstrip("/")).resolve()
        if not candidate.is_relative_to(self.dist.resolve()):
            return None
        if candidate.is_file():
            return candidate.read_bytes(), self._type(candidate.name)
        if path in self._spa:
            return (self.dist / "index.html").read_bytes(), self._type("index.html")
        return None

    @staticmethod
    def _type(path: str) -> str:
        return {
            ".html": "text/html; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".svg": "image/svg+xml",
            ".ico": "image/x-icon",
        }.get(PurePosixPath(path).suffix, "application/octet-stream")

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        if path.startswith("/api/"):
            rewritten = dict(scope)
            # The deployment's proxy terminates TLS, so the browser's Origin is the public
            # origin. Here the browser carries the local port; normalize it the same way,
            # so the BFF's CSRF Origin check sees the value `public_origin` vouches for.
            rewritten["headers"] = [
                (
                    name,
                    b"localhost"
                    if name == b"host"
                    else PUBLIC_HOST_HEADER.encode()
                    if name == b"origin"
                    else value,
                )
                for name, value in scope["headers"]
            ]
            await self.app(rewritten, receive, send)
            return
        served = self._bundle(path)
        if served is None:
            await send({"type": "http.response.start", "status": 404, "headers": []})
            await send({"type": "http.response.body", "body": b"not found"})
            return
        body, media_type = served
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", media_type.encode())],
            }
        )
        await send({"type": "http.response.body", "body": body})


def session_leg(arguments: argparse.Namespace) -> int:
    dist = arguments.web_dist
    if arguments.build:
        completed = subprocess.run(
            ["npm", "run", "build"], cwd=WEB_DIR, check=False, env=_npm_environment()
        )
        if completed.returncode != 0:
            raise SystemExit("npm run build failed; the journey needs the real bundle")
    if not (dist / "index.html").is_file():
        raise SystemExit(f"{dist}/index.html missing — run `npm run build` in {WEB_DIR} first")
    artifacts = arguments.artifacts
    artifacts.mkdir(parents=True, exist_ok=True)
    identity = load_identity()
    settings = PortalSettings(
        tenant=identity.tenant,
        issuer=identity.issuer,
        cognito_origin=IDP_ORIGIN,
        client_id="human123",
        machine_client_id="machine123",
        client_purpose="dedicated-human-code-pkce",
        public_origin=PUBLIC_HOST_HEADER,
        mode="local-test",
    )
    store = seeded_store(identity)
    app = build_app(store, settings)
    port = arguments.port or free_port()
    browser_origin = f"http://localhost:{port}"

    async def drive() -> int:
        overlay = {"/assets/axe.min.js": WEB_DIR / "node_modules/axe-core/axe.min.js"}
        server, task = await serve(StaticOrigin(app, dist, overlay), port)
        try:
            print(f"[portal-journey] origin {browser_origin}; BFF Host check {PUBLIC_HOST_HEADER}")
            # The Playwright child must not block this loop: uvicorn serves from it.
            return await asyncio.to_thread(
                run_playwright, arguments.playwright_args, artifacts, dist, browser_origin
            )
        finally:
            server.should_exit = True
            with contextlib.suppress(asyncio.CancelledError):
                await task
            await store.close()

    code = asyncio.run(drive())
    print(f"[portal-journey] evidence in {artifacts} (exit {code})")
    return code


def journey_leg_refusal() -> int:
    required = (
        "MAEZO_D7_PACKAGE_FIXTURE (secured-fixture private root, deploy/cibseven/secured/ACCEPTANCE.md)",
        "MAEZO_HUMAN_RELAY_PRIVATE_DIR (human relay custody, tests/support/human_relay_live.py:91)",
        "MAEZO_NATIVE_V2_IT_FIXTURE (native v2 custody)",
        "MAEZO_ROOT_FIXTURES=1",
    )
    missing = [name for name in required if name.split(" ")[0] not in os.environ]
    print(
        "REFUSAL — the engine-committed journey (intake -> claim -> APROVAR -> outcome on the "
        "secured fixture) needs ROOT-supplied custody materials; this script never fakes an "
        "engine result and never silently degrades to a smaller claim."
    )
    for name in missing:
        print(f"  missing: {name}")
    print(
        "Run it in the ROOT-qualified runtime lane (the same lane that runs the "
        "`root_fixture` integration suites), then bind the real read/command/intake "
        "compositions in place of this refusal."
    )
    return JOURNEY_CUSTODY_REFUSAL


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--leg", choices=("session", "journey"), default="session")
    parser.add_argument("--artifacts", type=Path, default=None)
    parser.add_argument("--web-dist", type=Path, default=WEB_DIR / "dist")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--build", action="store_true", help="run `npm run build` before the journey")
    parser.add_argument("playwright_args", nargs="*", help="extra arguments passed to `playwright test`")
    arguments = parser.parse_args()
    arguments.artifacts = arguments.artifacts or Path(tempfile.gettempdir()) / (
        f"portal-journey-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    )
    if arguments.leg == "journey":
        return journey_leg_refusal()
    return session_leg(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
