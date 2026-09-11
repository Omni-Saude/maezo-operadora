#!/usr/bin/env python3
"""Export only the installed PHI GET route graph; no production providers or I/O."""

import argparse
import json
from pathlib import Path

from export_portal_openapi import _SchemaExportSettings

from maezo.gateway.communications.content import PhiCommunicationService
from maezo.portal.api.communication_phi import create_phi_communication_app
from maezo.portal.api.session import HumanSessionResolver
from maezo.portal.api.store import LocalTestIdentityStore


def build_schema() -> dict[str, object]:
    settings = _SchemaExportSettings(
        tenant="schema-export",
        issuer="https://cognito-idp.sa-east-1.amazonaws.com/sa-east-1_SchemaExport",
        cognito_origin="https://schema-export.auth.sa-east-1.amazoncognito.com",
        client_id="schemaexporthuman",
        machine_client_id="schemaexportmachine",
        client_purpose="dedicated-human-code-pkce",
        public_origin="https://portal.schema-export.invalid",
        database_url=None,
        mode="local-test",
        session_seconds=1800,
        transaction_seconds=300,
    )

    def unavailable(_: HumanSessionResolver) -> PhiCommunicationService:
        raise AssertionError("Schema-only provider cannot serve content")

    return create_phi_communication_app(
        settings,
        identity_store=LocalTestIdentityStore(settings.tenant),
        content_service_factory=unavailable,
        read_only=True,
    ).openapi()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    output = parser.parse_args().output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(build_schema(), ensure_ascii=False, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
