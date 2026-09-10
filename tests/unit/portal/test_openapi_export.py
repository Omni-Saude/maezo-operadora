"""Regression fences for deterministic, deployment-isolated portal schema export."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, cast

from fastapi import FastAPI
from pydantic_settings import EnvSettingsSource, SecretsSettingsSource

from maezo.portal.api.config import PortalSettings

ROOT = Path(__file__).parents[3]
EXPORTER = ROOT / "scripts/dev/export_portal_openapi.py"
EXPECTED_PATHS = {
    "/api/v1/portal/auth/callback",
    "/api/v1/portal/auth/login",
    "/api/v1/portal/auth/logout",
    "/api/v1/portal/cases",
    "/api/v1/portal/cases/{case_ref}",
    "/api/v1/portal/cases/{case_ref}/communications",
    "/api/v1/portal/cases/{case_ref}/document-requests",
    "/api/v1/portal/cases/{case_ref}/document-requests/{request_ref}/responses",
    "/api/v1/portal/cases/{case_ref}/document-uploads",
    "/api/v1/portal/cases/{case_ref}/documents",
    "/api/v1/portal/cases/{case_ref}/history",
    "/api/v1/portal/commands/{command_id}",
    "/api/v1/portal/commands/{command_id}/receipt",
    "/api/v1/portal/document-uploads/{upload_ref}/complete",
    "/api/v1/portal/documents/{document_ref}/content",
    "/api/v1/portal/intake-recovery",
    "/api/v1/portal/intake-recovery/commands/{command_id}",
    "/api/v1/portal/intakes/auth",
    "/api/v1/portal/intakes/{intake_ref}",
    "/api/v1/portal/intakes/{intake_ref}/document-uploads",
    "/api/v1/portal/session",
    "/api/v1/portal/tasks",
    "/api/v1/portal/tasks/{task_id}",
    "/api/v1/portal/tasks/{task_id}/assignment-candidates",
    "/api/v1/portal/tasks/{task_id}/assignment-context",
    "/api/v1/portal/tasks/{task_id}/assignments",
    "/api/v1/portal/tasks/{task_id}/decision-context",
    "/api/v1/portal/tasks/{task_id}/decisions",
}


def _load_exporter() -> ModuleType:
    spec = importlib.util.spec_from_file_location("portal_openapi_export", EXPORTER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_export_uses_validated_explicit_settings_without_external_sources(
    monkeypatch: Any,
) -> None:
    module = _load_exporter()
    original_create_app = module.create_app
    observed: dict[str, object] = {}

    monkeypatch.setenv(
        "MAEZO_PORTAL_DATABASE_URL",
        "postgresql://synthetic:synthetic@localhost/synthetic",
    )
    monkeypatch.setenv("MAEZO_PORTAL_SESSION_SECONDS", "0")
    monkeypatch.setenv("MAEZO_PORTAL_TRANSACTION_SECONDS", "0")

    def external_source_forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("schema export constructed an external settings source")

    monkeypatch.setattr(EnvSettingsSource, "__init__", external_source_forbidden)
    monkeypatch.setattr(SecretsSettingsSource, "__init__", external_source_forbidden)

    def inspect_settings(settings: PortalSettings, **kwargs: Any) -> FastAPI:
        observed.update(
            database_url=settings.database_url,
            session_seconds=settings.session_seconds,
            transaction_seconds=settings.transaction_seconds,
        )
        return cast(FastAPI, original_create_app(settings, **kwargs))

    monkeypatch.setattr(module, "create_app", inspect_settings)
    schema = module.build_schema()

    assert set(schema["paths"]) == EXPECTED_PATHS
    assert observed == {
        "database_url": None,
        "session_seconds": 1800,
        "transaction_seconds": 300,
    }
