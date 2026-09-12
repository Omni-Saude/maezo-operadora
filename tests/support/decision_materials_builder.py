"""Builder for a COMPLETE, valid decision material bundle used by the WP-J1-06 tests.

The sibling of `materials_builder.py` (same package, moved here by #384's convention
for shared builders), for the second material plane: an installation
root pin, a pinned `BindingDatabase`, a PHI deployment designation and the AES-GCM
vault keys. It builds deployment material only — no provider and no port; the tests
exercise the production providers in `src/`, never a stand-in for one.

Every secret here is freshly generated in-process and never leaves it.
"""

from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

from maezo.gateway.human.decision_binding import RELATIONS, BindingDatabase
from maezo.gateway.human.decision_binding_qualification import canonical, sha
from maezo.gateway.human.decision_materials import (
    FILES,
    PUBLIC_FILES,
    DecisionMaterials,
    DecisionPublicManifest,
    verify_decision_materials,
)
from maezo.gateway.human.read_profile import parse_model

from .materials_builder import ENVIRONMENT, TENANT, WORKLOAD, _certificate

INSTALLATION = "installation-j1-06"
VERSION_ID = "j1decision" + "0" * 26
PHI_HOST = "phi.decision.invalid"
BINDING_HOST = "binding.decision.invalid"
ACTIVE_KEY = "phi-decision-current"
RETIRED_KEY = "phi-decision-retired"


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _scope() -> dict[str, str]:
    return {"tenant": TENANT, "environment": ENVIRONMENT, "workload_ref": WORKLOAD}


def binding_database(*, scope: dict[str, str] | None = None, **overrides: object) -> BindingDatabase:
    """The pinned native relations of the decision binding database."""
    payload: dict[str, object] = dict(
        scope=scope or _scope(),
        installation_id=INSTALLATION,
        engine_name="engine-cibseven-1",
        database_incarnation="incarnation-1",
        resource_publisher_fingerprint="1" * 64,
        host=BINDING_HOST,
        port=5432,
        database="maezo_binding",
        database_oid=16384,
        schema_name="maezo_human",
        schema_oid=16385,
        owner_role="binding_owner",
        installer_role="binding_installer",
        reader_role="binding_reader",
        engine_role="binding_engine",
        installer_certificate_digest="2" * 64,
        reader_certificate_digest="3" * 64,
        relations=tuple(
            {"name": name, "oid": 20000 + index, "owner": "binding_owner"}
            for index, name in enumerate(RELATIONS)
        ),
    )
    payload.update(overrides)
    return BindingDatabase.model_validate(payload)


def build_bundle(
    *,
    directory: Path,
    overrides: dict | None = None,
    database: BindingDatabase | None = None,
    scope: dict[str, str] | None = None,
) -> tuple[DecisionPublicManifest, dict[str, bytes]]:
    """Write the decision material files under `directory`; return (manifest, bytes)."""
    scope = scope or _scope()
    root_key = Ed25519PrivateKey.generate()
    public = root_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    pinned = database if database is not None else binding_database(scope=scope)

    binding_certificate, binding_private, _ = _certificate("decision-binding-client")
    phi_certificate, phi_private, phi_client_spki = _certificate("decision-phi-client")

    observed_at = datetime.now(UTC) - timedelta(minutes=5)
    issued_at = datetime.now(UTC) - timedelta(minutes=4)
    valid_until = datetime.now(UTC) + timedelta(hours=6)
    phi_until = datetime.now(UTC) + timedelta(hours=12)
    root_until = datetime.now(UTC) + timedelta(hours=24)

    active_material = bytes(range(96, 128))
    retired_material = bytes(range(128, 160))

    root_document = {
        "scope": scope,
        "installation_id": INSTALLATION,
        "key_id": "decision-root-1",
        "public_key": base64.b64encode(public).decode("ascii"),
        "installation_digest": sha(canonical(pinned)),
        "freeze_contract_digest": "4" * 64,
        "freeze_installed_authority_ref": "freeze-authority-1",
        "observed_at": _iso(observed_at),
        "valid_until": _iso(root_until),
    }
    vault_document = {
        "schema": "portal-human-decision-vault.v1",
        "not_before": _iso(observed_at),
        "not_after": _iso(phi_until),
        "keys": {
            ACTIVE_KEY: base64.b64encode(active_material).decode("ascii"),
            RETIRED_KEY: base64.b64encode(retired_material).decode("ascii"),
        },
    }
    files: dict[str, bytes] = {
        "installation-root.json": _canonical_bytes(root_document),
        "vault-keys.json": _canonical_bytes(vault_document),
        "binding-ca.pem": binding_certificate,
        "binding-client-certificate.pem": binding_certificate,
        "binding-client-key.pem": binding_private,
        "phi-ca.pem": phi_certificate,
        "phi-client-certificate.pem": phi_certificate,
        "phi-client-key.pem": phi_private,
    }
    assert set(files) == FILES, sorted(set(files) ^ FILES)

    payload: dict[str, object] = {
        "schema": "portal-human-decision-material.v1",
        "material_version_id": VERSION_ID,
        "scope": scope,
        "issuer": "maezo-deployment-root",
        "issued_at": _iso(issued_at),
        "valid_until": _iso(valid_until),
        "root_key_fingerprint": hashlib.sha256(public).hexdigest(),
        "binding_database": _wire(pinned),
        "phi": {
            "host": PHI_HOST,
            "port": "5432",
            "database": "maezo_phi",
            "writer_role": "phi_decision_writer",
            "owner_role": "phi_decision_owner",
            "client_certificate_sha256": phi_client_spki,
            "active_key_id": ACTIVE_KEY,
            "readable_key_ids": [ACTIVE_KEY, RETIRED_KEY],
            "valid_until": _iso(phi_until),
            "evidence_digest": "5" * 64,
        },
        "vault_keys": [
            {
                "key_id": ACTIVE_KEY,
                "material_sha256": hashlib.sha256(active_material).hexdigest(),
                "current": True,
            },
            {
                "key_id": RETIRED_KEY,
                "material_sha256": hashlib.sha256(retired_material).hexdigest(),
                "current": False,
            },
        ],
        "binding_timeout_seconds": "10",
        "phi_timeout_seconds": "10",
        "files": {
            **{name: hashlib.sha256(files[name]).hexdigest() for name in PUBLIC_FILES},
            **{name: None for name in FILES - PUBLIC_FILES},
        },
    }
    for key, value in (overrides or {}).items():
        payload[key] = value

    directory.mkdir(parents=True, exist_ok=True)
    for name, raw in files.items():
        (directory / name).write_bytes(raw)
    return parse_model(DecisionPublicManifest, payload), files


def build_decision_materials(
    directory: Path,
    *,
    overrides: dict | None = None,
    database: BindingDatabase | None = None,
    scope: dict[str, str] | None = None,
) -> DecisionMaterials:
    manifest, files = build_bundle(directory=directory, overrides=overrides, database=database, scope=scope)
    return verify_decision_materials(manifest, files, now=datetime.now(UTC), directory=str(directory))


def _wire(value: object) -> object:
    from maezo.gateway.human.read_profile import wire

    return wire(value)


def _canonical_bytes(document: dict[str, object]) -> bytes:
    from maezo.portal.engine.profile import canonicalize

    return canonicalize(document)


def private_pem(key: Ed25519PrivateKey) -> bytes:
    return key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
