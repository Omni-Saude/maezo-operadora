"""Fence for the PHI OpenAPI export: the whole PHI surface, schema-only, deterministic."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).parents[3]
EXPORTER = ROOT / "scripts/dev/export_phi_openapi.py"

#: The complete PHI surface: the communication byte routes and WP-J1-04's document byte
#: route. The General portal's own OpenAPI (`test_openapi_export.py`) must never contain
#: any of these, and this export must never contain a General `/api/v1/portal` path.
EXPECTED_PATHS = {
    "/api/v1/phi/cases/{case_ref}/communication-content",
    "/api/v1/phi/cases/{case_ref}/communications/{communication_ref}/content",
    "/api/v1/phi/documents/{document_ref}/content",
    "/api/v1/phi/documents/{upload_ref}/content",
}


def _load_exporter() -> ModuleType:
    # The exporter imports its sibling `export_portal_openapi` the way `python
    # scripts/dev/export_phi_openapi.py` runs it, so that directory joins the path for
    # the duration of the load only.
    sys.path.insert(0, str(EXPORTER.parent))
    try:
        spec = importlib.util.spec_from_file_location("phi_openapi_export", EXPORTER)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(EXPORTER.parent))
    return module


def test_phi_export_lists_the_whole_phi_surface_and_nothing_else() -> None:
    module = _load_exporter()
    schema = module.build_schema()

    assert set(schema["paths"]) == EXPECTED_PATHS
    # Every operation is an authenticated, error-declaring operation: no anonymous PHI.
    for item in schema["paths"].values():
        for method, operation in item.items():  # type: ignore[union-attr]
            if method not in {"get", "post", "put"}:
                continue
            assert "401" in operation["responses"], (method, operation)  # type: ignore[index]
            assert "403" in operation["responses"], (method, operation)  # type: ignore[index]


def test_phi_export_is_deterministic_and_serializable() -> None:
    module = _load_exporter()
    first, second = module.build_schema(), module.build_schema()
    assert first == second
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
