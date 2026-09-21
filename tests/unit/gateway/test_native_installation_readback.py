"""Finite readback protocol tests. No actual PostgreSQL qualification is claimed."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Any

import pytest

from maezo.gateway import native_installation_readback as n

PREPARATION = b'{"finite":"prepared-owner-receipt"}'
PREPARATION_SHA = hashlib.sha256(PREPARATION).hexdigest()


def pins() -> n.NativeInstallationPins:
    return n.NativeInstallationPins(
        owner_login="migration_owner",
        owner_oid=100,
        native_schema_owner_oid=101,
        native_function_owner_oid=102,
        database_oid=200,
        act_schema="engine",
        act_schema_oid=201,
        native_manifest_sha256="a" * 64,
        catalogue_function_source_sha256="b" * 64,
        catalogue_sha256="c" * 64,
        preparation_id="00000000-0000-4000-8000-000000000001",
        preparation_receipt_sha256=PREPARATION_SHA,
        migration_receipt="finite-migration-receipt",
    )


class FiniteReadbackCursor:
    def __init__(self) -> None:
        p = pins()
        self.rows: dict[str, tuple[Any, ...]] = {
            n.SESSION_SQL: (
                p.owner_login,
                p.owner_login,
                100,
                200,
                160008,
                "repeatable read",
                "on",
                True,
                101,
            ),
            n.CATALOGUE_FUNCTION_SQL: (
                102,
                True,
                ["search_path=pg_catalog, pg_temp"],
                "s",
                "jsonb",
                "b" * 64,
            ),
            n.INSTALLATION_SQL: (
                "a" * 64,
                200,
                "engine",
                201,
                102,
                "c" * 64,
                p.migration_receipt,
                {"preparation_id": p.preparation_id, "receipt_sha256": PREPARATION_SHA},
                PREPARATION,
                PREPARATION,
                PREPARATION_SHA,
                201,
            ),
            n.CATALOGUE_SQL: ("c" * 64,),
        }
        self.pending: list[tuple[Any, ...]] = []
        self.calls: list[str] = []
        self.duplicate = False

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> None:
        self.calls.append(query)
        assert query.lstrip().startswith("SELECT")
        if query == n.INSTALLATION_SQL:
            assert params == (pins().preparation_id, pins().preparation_id)
        self.pending = [self.rows[query]] * (2 if self.duplicate else 1) if query in self.rows else []

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.pending.pop(0) if self.pending else None


def test_exact_readback_is_evidence_and_cannot_claim_native_admission() -> None:
    cursor = FiniteReadbackCursor()
    evidence = json.loads(n.read_installation(cursor, pins()))
    assert evidence["protocol"] == "maezo.native-v2-installation-readback.v1"
    assert evidence["native_principal_qualification"] is False
    assert evidence["runtime_admission_verified"] is False
    assert "native_receipt_bytes" not in evidence and "native_owner_operation_id" not in evidence
    assert cursor.calls == [n.SESSION_SQL, n.CATALOGUE_FUNCTION_SQL, n.INSTALLATION_SQL, n.CATALOGUE_SQL]


@pytest.mark.parametrize(
    "fault",
    [
        "role",
        "database",
        "postgres",
        "isolation",
        "readonly",
        "membership",
        "source",
        "owner",
        "missing",
        "duplicate",
        "birth",
        "catalog",
    ],
)
def test_readback_refuses_owner_source_and_receipt_drift(fault: str) -> None:
    cursor = FiniteReadbackCursor()
    if fault in {"role", "database", "postgres", "isolation", "readonly", "membership"}:
        index, replacement = {
            "role": (1, "assumed_role"),
            "database": (3, 999),
            "postgres": (4, 170000),
            "isolation": (5, "read committed"),
            "readonly": (6, "off"),
            "membership": (7, False),
        }[fault]
        row = list(cursor.rows[n.SESSION_SQL])
        row[index] = replacement
        cursor.rows[n.SESSION_SQL] = tuple(row)
    elif fault in {"source", "owner"}:
        row = list(cursor.rows[n.CATALOGUE_FUNCTION_SQL])
        row[5 if fault == "source" else 0] = "e" * 64
        cursor.rows[n.CATALOGUE_FUNCTION_SQL] = tuple(row)
    elif fault == "missing":
        del cursor.rows[n.INSTALLATION_SQL]
    elif fault == "duplicate":
        cursor.duplicate = True
    elif fault == "birth":
        row = list(cursor.rows[n.INSTALLATION_SQL])
        row[9] = b"another receipt"
        cursor.rows[n.INSTALLATION_SQL] = tuple(row)
    else:
        cursor.rows[n.CATALOGUE_SQL] = ("e" * 64,)
    with pytest.raises(n.ReadbackError, match="readback_unavailable"):
        n.read_installation(cursor, pins())
    if fault in {"source", "owner"}:
        assert n.CATALOGUE_SQL not in cursor.calls


@pytest.mark.parametrize(
    "field,value",
    [
        ("owner_oid", True),
        ("database_oid", 0),
        ("act_schema", "a;grant"),
        ("native_manifest_sha256", "missing"),
    ],
)
def test_pins_reject_malformed_identifiers_and_bounds(field: str, value: Any) -> None:
    with pytest.raises(n.ReadbackError):
        replace(pins(), **{field: value})
