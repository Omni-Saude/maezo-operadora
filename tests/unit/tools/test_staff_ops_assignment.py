"""tools/staff_ops/assignment: entradas fechadas do plano de atribuicao (Onda 8).

A prova de ponta a ponta (engine real, ACK nativo, portal com o plano humano) e o passo `assignment`
+ `portal-human` do harness `deploy/c1-local`; aqui ficam as recusas que nao precisam de banco.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from tools.staff_ops import assignment
from tools.staff_ops.common import OpsError

from maezo.portal.engine.profile import canonicalize


def _key() -> tuple[bytes, str]:
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    fp = hashlib.sha256(
        key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    ).hexdigest()
    return pem, fp


def _plan(**changes: Any) -> dict[str, Any]:
    pem, fp = _key()
    value: dict[str, Any] = dict(
        schema=assignment.ACTIVATE_SCHEMA,
        admin_dsn="postgresql+asyncpg://portal_assignment_admin_amh:x@db:5432/maezo",
        admin_login="portal_assignment_admin_amh",
        source_key_pem_b64=base64.b64encode(pem).decode(),
        source_key_id="amh-dev-assignment-source-1",
        source_fingerprint=fp,
        owner_ref="owner",
        source_ref="staff-assignment:amh:dev",
        owner_receipt=dict(artifact_ref="review", digest="c" * 64),
        valid_days=7,
    )
    value.update(changes)
    return value


def test_plan_parses_and_the_key_matches_its_fingerprint() -> None:
    document = _plan()
    plan, dsn, pem = assignment.parse_plan(document)
    assert dsn.startswith("postgresql+asyncpg://") and plan.admin_login == "portal_assignment_admin_amh"
    assert assignment.source_key(pem, plan.source_fingerprint) is not None
    with pytest.raises(OpsError):
        assignment.source_key(pem, "0" * 64)  # chave trocada no segredo


@pytest.mark.parametrize(
    "changes",
    [
        dict(schema="outro.v1"),
        dict(valid_days=15),
        dict(valid_days=True),
        dict(admin_dsn="postgresql://sem-driver"),
        dict(owner_receipt=dict(artifact_ref="r", digest="nao-hex")),
        dict(source_fingerprint="z" * 64),
        dict(extra="x"),
    ],
)
def test_plan_refuses(changes: dict[str, Any]) -> None:
    with pytest.raises(OpsError):
        assignment.parse_plan(_plan(**changes))


def test_installation_is_read_from_the_exact_trust_file() -> None:
    trust = dict(
        schema="human-assignment-trust.v1",
        tenant="amh",
        environment="dev",
        engine_name="default",
        database_incarnation="inc-1",
        deployment_receipt=dict(artifact_ref="receipt", digest="a" * 64),
        valid_until="1791417600",
    )
    raw = canonicalize(trust)
    spec = assignment.installation_from_trust(raw, runtime_role="cibseven_app")
    assert spec.configuration_digest == hashlib.sha256(raw).hexdigest()
    assert (spec.tenant, spec.valid_until, spec.runtime_role) == ("amh", 1791417600, "cibseven_app")
    with pytest.raises(OpsError):
        assignment.installation_from_trust(raw + b" ", runtime_role="cibseven_app")  # fora de JCS
    with pytest.raises(OpsError):
        assignment.installation_from_trust(canonicalize(dict(trust, schema="x")), runtime_role="cibseven_app")


def test_installation_block_of_the_rows_secret() -> None:
    block = dict(
        schema=assignment.INSTALL_SCHEMA,
        environment="dev",
        engine_name="default",
        database_incarnation="inc-1",
        configuration_digest="d" * 64,
        deployment_receipt=dict(artifact_ref="receipt", digest="a" * 64),
        valid_until="1791417600",
    )
    spec = assignment.parse_installation(block, tenant="amh", runtime_role="cibseven_app")
    assert spec.configuration_digest == "d" * 64
    for bad in (
        dict(block, valid_until=1791417600),
        dict(block, configuration_digest="x"),
        dict(block, more=1),
    ):
        with pytest.raises(OpsError):
            assignment.parse_installation(bad, tenant="amh", runtime_role="cibseven_app")


def test_dispatch_knows_the_command(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.staff_ops.__main__ import main as dispatch

    monkeypatch.delenv("STAFF_ASSIGNMENT_SECRET_ARN", raising=False)
    assert dispatch(["assignment-activate"]) == 1  # recusa (sem ARN), nao "comando desconhecido" (64)
