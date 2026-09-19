#!/usr/bin/env python3
"""Export only the installed PHI route graphs; no production providers or I/O.

The exported document is the whole PHI surface: the communication routes and the
document byte route (`GET /api/v1/phi/documents/{ref}/content`, WP-J1-04). The two
applications are schema-only — their factories receive providers that raise, so building
the graph can never open a connection, a key set or a socket. Path or schema collisions
between the two applications are refused rather than merged silently.
"""

import argparse
import json
from pathlib import Path

from export_portal_openapi import _SchemaExportSettings

from maezo.gateway.communications.content import PhiCommunicationService
from maezo.portal.api.communication_phi import create_phi_communication_app
from maezo.portal.api.document_phi import PhiDocumentService, create_phi_document_app
from maezo.portal.api.session import HumanSessionResolver
from maezo.portal.api.store import LocalTestIdentityStore


def _settings() -> _SchemaExportSettings:
    return _SchemaExportSettings(
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


def _refuse_document(resolver: HumanSessionResolver) -> PhiDocumentService:
    raise AssertionError("Schema-only provider cannot serve document bytes")


def _refuse_communication(_: HumanSessionResolver) -> PhiCommunicationService:
    raise AssertionError("Schema-only provider cannot serve communication content")


def build_schema() -> dict[str, object]:
    settings = _settings()
    store = LocalTestIdentityStore(settings.tenant)
    communication = create_phi_communication_app(
        settings,
        identity_store=store,
        content_service_factory=_refuse_communication,
        read_only=False,
    )
    document = create_phi_document_app(
        settings,
        identity_store=store,
        document_service_factory=_refuse_document,
    )
    return _merge(communication.openapi(), document.openapi())


def _merge(*documents: dict[str, object]) -> dict[str, object]:
    merged: dict[str, object] = {}
    paths: dict[str, object] = {}
    schemas: dict[str, object] = {}
    for document in documents:
        for path, item in dict(document.get("paths", {})).items():  # type: ignore[arg-type]
            if path in paths and paths[path] != item:
                raise SystemExit(f"colliding PHI path in export: {path}")
            paths[path] = item
        components = dict(document.get("components", {}))  # type: ignore[arg-type]
        for name, schema in dict(components.get("schemas", {})).items():
            if name in schemas and schemas[name] != schema:
                raise SystemExit(f"colliding PHI schema in export: {name}")
            schemas[name] = schema
        merged.update({k: v for k, v in document.items() if k not in {"paths", "components"}})
    merged["paths"] = dict(sorted(paths.items()))
    merged["components"] = {"schemas": dict(sorted(schemas.items()))}
    return merged


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    output = parser.parse_args().output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(build_schema(), ensure_ascii=False, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
