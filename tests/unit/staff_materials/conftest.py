"""Fixtures sinteticas da T1.3. A raiz de TESTE nasce aqui, no proprio teste (regra D-F).

Nenhum destes fixtures roda `approver root-keygen` nem le chave de fora: a raiz e um
`Ed25519PrivateKey.generate()` em memoria, que morre com o teste.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tools.staff_materials import approver
from tools.staff_materials.assemble import assemble, load_input
from tools.staff_materials.assemble import bundle_bytes as tool_bundle_bytes
from tools.staff_materials.generate import Generated, generate
from tools.staff_materials.spec import load_spec

from maezo.gateway.external_cases.models import digest, instant
from maezo.gateway.staff_cases.production_config import PortalProductionSettings
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


def assemble_input(now: datetime) -> dict[str, Any]:
    return dict(
        schema="staff-materials-assemble.v1",
        material_version_id="11111111-2222-3333-4444-555555555555",
        issuer="https://cognito-idp.sa-east-1.amazonaws.com/sa-east-1_test",
        issued_at=instant(now - timedelta(minutes=1)),
        valid_until=instant(now + timedelta(days=12)),
        native_configuration_digest="c" * 64,
        native_maximum_seconds="5",
        native_schema="maezo_native",
        engine_schema="cibseven",
        session_lock_function_pin=dict(
            oid="12", owner="portal_external_identity_reader", definition_sha256="e" * 64
        ),
        native_relation_pins={
            n: dict(oid=str(i), owner="maezo_native_schema_owner")
            for i, n in enumerate(("mzo_portal_read_membership", "mzo_human_principal"), start=20)
        },
        revocation=dict(
            source_ref="revocations",
            revision="1",
            observed_at=instant(now - timedelta(minutes=1)),
            valid_until=instant(now + timedelta(days=1)),
            revoked_fingerprints=[],
        ),
    )


def approver_files(generated: Generated, root: Ed25519PrivateKey, now: datetime) -> dict[str, bytes]:
    """O que o APROVADOR entrega. A raiz de teste assina aqui; a ferramenta so recebe a publica."""
    designation_raw = (generated.directory / "portal" / "designation.json").read_bytes()
    _, shown, _ = approver.review_designation(designation_raw)
    return {
        "installation-proof.json": approver.sign_designation(
            designation_raw, root, confirm_digest=shown, expires_at=now + timedelta(days=12), now=now
        ),
        "installation-root.der": root.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        ),
    }


def assemble_v2(generated: Generated, now: datetime, *, root: Ed25519PrivateKey | None = None) -> Assembled:
    """Monta o pacote v2 com o `assemble` REAL da ferramenta (`tools.staff_materials.assemble`)."""
    root = Ed25519PrivateKey.generate() if root is None else root
    portal = {path.name: path.read_bytes() for path in (generated.directory / "portal").iterdir()}
    manifest, files = assemble(
        portal,
        approver_files(generated, root, now),
        generated.summary,
        load_spec(spec_bytes(spec_value(now))),
        load_input(canonicalize(assemble_input(now))),
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
        staff_native_schema=m["native_schema"],
        staff_engine_schema=m["engine_schema"],
        staff_read_key_sha256=m["read_key_fingerprint"],
        staff_witness_key_sha256=m["witness_key_fingerprint"],
        staff_maximum_seconds=5,
    )


def bundle_bytes(assembled: Assembled) -> bytes:
    return tool_bundle_bytes(assembled.manifest, assembled.files)


def settings(pins: dict[str, Any]) -> PortalProductionSettings:
    return PortalProductionSettings(**pins)
