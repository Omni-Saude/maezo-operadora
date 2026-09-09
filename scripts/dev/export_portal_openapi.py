#!/usr/bin/env python3
"""Export the portal OpenAPI document without production configuration or I/O."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import httpx

from maezo.portal.api.app import create_app
from maezo.portal.api.config import PortalSettings
from maezo.portal.api.store import LocalTestIdentityStore


def build_schema() -> dict[str, object]:
    """Build only the ASGI route graph; no lifespan, database, or HTTP request runs."""
    settings = PortalSettings(
        tenant="schema-export",
        issuer="https://cognito-idp.sa-east-1.amazonaws.com/sa-east-1_SchemaExport",
        cognito_origin="https://schema-export.auth.sa-east-1.amazoncognito.com",
        client_id="schemaexporthuman",
        machine_client_id="schemaexportmachine",
        client_purpose="dedicated-human-code-pkce",
        public_origin="https://portal.schema-export.invalid",
        mode="local-test",
    )

    def refuse_network(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"schema export attempted network access: {request.method}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(refuse_network), trust_env=False)
    try:
        app = create_app(
            settings,
            store=LocalTestIdentityStore(settings.tenant),
            oidc_client=client,
        )
        return app.openapi()
    finally:
        asyncio.run(client.aclose())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = json.dumps(build_schema(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
