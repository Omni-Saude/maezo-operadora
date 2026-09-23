"""Fixtures sinteticas da T1.3. A raiz de TESTE nasce aqui, no proprio teste (regra D-F).

Nenhum destes fixtures roda `approver root-keygen` nem le chave de fora: a raiz e um
`Ed25519PrivateKey.generate()` em memoria, que morre com o teste.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tools.staff_materials import approver
from tools.staff_materials.generate import Generated, generate
from tools.staff_materials.spec import load_spec

from maezo.gateway.external_cases.models import digest, instant
from maezo.gateway.staff_cases.authority import fingerprint
from maezo.gateway.staff_cases.production_config import PRIVATE_FILES, PortalProductionSettings
from maezo.portal.engine.profile import canonicalize


def spec_value(now: datetime, **changes: Any) -> dict[str, Any]:
    value: dict[str, Any] = dict(
        schema="staff-materials-spec.v1",
        scope=dict(tenant="amh", environment="dev", engine_name="default", database_incarnation="inc-test-1"),
        native_hostname="engine-native.maezo-operadora-dev.internal",
        certificate_not_after=instant(now + timedelta(days=30)),
        designation=dict(
            designation_ref="staff-designation",
            designation_revision="1",
            expected_previous_revision="0",
            authority_ref="leonardo",
            authority_revision="1",
            not_before=instant(now - timedelta(minutes=1)),
            valid_until=instant(now + timedelta(days=13)),
        ),
        read_requester=dict(
            login_role="portal_staff_read", source_namespace="portal", source_ref="portal-read"
        ),
        identity_verifier=dict(
            login_role="portal_native_witness",
            source_namespace="portal-identity",
            source_ref="membership-amh",
        ),
        native_result=dict(login_role="cibseven_app", source_namespace="engine", source_ref="engine-result"),
        case_issuer=dict(
            login_role="staff_case_issuer",
            source_namespace="staff-escalation-policy",
            source_ref="staff-cases-amh",
        ),
        publication_importer=dict(
            login_role="staff_publication_importer",
            source_namespace="staff-import",
            source_ref="staff-cases-amh",
        ),
        session_lock_connection=dict(
            host="identity.cluster.sa-east-1.rds.amazonaws.com",
            port="5432",
            database="maezo",
            login="portal_session_lock_amh",
        ),
        native_witness_connection=dict(
            host="identity.cluster.sa-east-1.rds.amazonaws.com",
            port="5432",
            database="maezo",
            login="portal_native_witness",
        ),
    )
    value.update(changes)
    return value


def spec_bytes(value: dict[str, Any]) -> bytes:
    return canonicalize(value)


@pytest.fixture
def now() -> datetime:
    return datetime.now(UTC)


@pytest.fixture
def generated(tmp_path: Path, now: datetime) -> Generated:
    return generate(load_spec(spec_bytes(spec_value(now))), tmp_path / "materials")


@dataclass
class Assembled:
    generated: Generated
    root: Ed25519PrivateKey
    files: dict[str, bytes]
    manifest: dict[str, Any]


def assemble_v2(generated: Generated, now: datetime, *, root: Ed25519PrivateKey | None = None) -> Assembled:
    """Monta um pacote v2 NO TESTE, so para exercitar o loader.

    O `assemble` da ferramenta e v2 e espera a T1.8/Python; este monta o que o loader de HOJE
    aceita, a partir da saida real do `generate` e de uma assinatura real do aprovador.
    """
    root = Ed25519PrivateKey.generate() if root is None else root
    portal = generated.directory / "portal"
    files = {path.name: path.read_bytes() for path in portal.iterdir()}
    designation_raw = files["designation.json"]
    _, shown, _ = approver.review_designation(designation_raw)
    files["installation-proof.json"] = approver.sign_designation(
        designation_raw, root, confirm_digest=shown, expires_at=now + timedelta(days=12), now=now
    )
    files["installation-root.der"] = root.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    summary = generated.summary
    scope = summary["scope"]
    spec = spec_value(now)
    manifest: dict[str, Any] = dict(
        schema="portal-staff-material.v2",
        material_version_id="11111111-2222-3333-4444-555555555555",
        scope=scope,
        issuer="https://cognito-idp.sa-east-1.amazonaws.com/sa-east-1_test",
        issued_at=instant(now - timedelta(minutes=1)),
        valid_until=instant(now + timedelta(days=12)),
        root_key_fingerprint=fingerprint(root.public_key()),
        designation_digest=summary["designation_sha256"],
        native_configuration_digest="c" * 64,
        native_maximum_seconds="5",
        read_key_fingerprint=summary["key_fingerprints"]["read_requester"],
        witness_key_fingerprint=summary["key_fingerprints"]["identity_verifier"],
        native_origin="https://" + summary["native_hostname"],
        native_server_spki_sha256=summary["native_server_spki_sha256"],
        native_schema="maezo_native",
        session_lock_connection=dict(
            spec["session_lock_connection"],
            tls_server_name=spec["session_lock_connection"]["host"],
            ca_file="session-lock-ca.pem",
            function_pin=dict(oid="12", owner="portal_external_identity_reader", definition_sha256="e" * 64),
        ),
        native_witness_connection=dict(
            spec["native_witness_connection"],
            tls_server_name=spec["native_witness_connection"]["host"],
            ca_file="native-witness-ca.pem",
            function_pin=None,
        ),
        native_relation_pins={
            n: dict(oid=str(i), owner="maezo_native_schema_owner")
            for i, n in enumerate(("mzo_portal_read_membership", "mzo_human_principal"), start=20)
        },
        revocation_snapshot=dict(
            scope=scope,
            designation_digest=summary["designation_sha256"],
            source_ref="revocations",
            revision="1",
            observed_at=instant(now - timedelta(minutes=1)),
            valid_until=instant(now + timedelta(days=1)),
            revoked_fingerprints=[],
        ),
        files={n: None if n in PRIVATE_FILES else hashlib.sha256(v).hexdigest() for n, v in files.items()},
    )
    return Assembled(generated, root, files, manifest)


def pins_for(assembled: Assembled) -> dict[str, Any]:
    m = assembled.manifest
    return dict(
        capabilities="identity,staff_cases",
        tenant=m["scope"]["tenant"],
        issuer=m["issuer"],
        staff_material_directory="/run/maezo-staff-materials/current",
        staff_material_version_id=m["material_version_id"],
        staff_public_manifest_sha256=digest(m),
        staff_root_key_sha256=m["root_key_fingerprint"],
        staff_designation_sha256=m["designation_digest"],
        staff_native_configuration_sha256=m["native_configuration_digest"],
        staff_scope=m["scope"],
        staff_native_origin=m["native_origin"],
        staff_native_server_spki_sha256=m["native_server_spki_sha256"],
        staff_read_key_sha256=m["read_key_fingerprint"],
        staff_witness_key_sha256=m["witness_key_fingerprint"],
        staff_maximum_seconds=5,
    )


def bundle_bytes(assembled: Assembled) -> bytes:
    return canonicalize(
        dict(
            schema="portal-staff-secret-bundle.v1",
            material_version_id=assembled.manifest["material_version_id"],
            public_manifest=assembled.manifest,
            files={n: base64.b64encode(v).decode() for n, v in assembled.files.items()},
        )
    )


def settings(pins: dict[str, Any]) -> PortalProductionSettings:
    return PortalProductionSettings(**pins)
