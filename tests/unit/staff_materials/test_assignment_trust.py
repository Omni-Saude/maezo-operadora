"""`assignment-plane trust`: o `human-assignment-trust.v1` que o engine carrega (Onda 8).

Os valores sao sinteticos; o que se prova e o contrato com `AssignmentTrust.java` (chaves exatas,
numeros em texto, publisher = a chave `human-authority` do trust humano) e as recusas que o engine
faria no boot, antes de o arquivo chegar a ele.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from tools.staff_materials.assignment_trust import SPEC_SCHEMA, build_trust, new_source_key
from tools.staff_materials.secure_io import MaterialError

from maezo.portal.engine.profile import canonicalize


def _spki() -> str:
    raw = (
        Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    )
    return base64.b64encode(raw).decode("ascii")


def _fp(spki: str) -> str:
    return hashlib.sha256(base64.b64decode(spki)).hexdigest()


def _inputs() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, str]]:
    spec = dict(
        schema=SPEC_SCHEMA,
        tenant="amh",
        environment="dev",
        engine_name="default",
        database_incarnation="inc-1",
        deployment_receipt=dict(artifact_ref="receipt", digest="a" * 64),
        validity_policy=dict(artifact_ref="validity", digest="b" * 64),
        owner_ref="owner",
        source_ref="staff-assignment:amh:dev",
        not_before="2026-09-25T00:00:00.000000Z",
        not_after="2026-10-08T00:00:00.000000Z",
    )
    command, authority = _spki(), _spki()
    human_trust = dict(
        schema="human-trust.v1",
        tenant="amh",
        audience="aud",
        engine_name="default",
        max_lifetime_seconds="60",
        enable_synthetic_fixture=False,
        keys=[
            dict(
                id="cmd",
                purpose="human-command",
                workload="portal-command",
                peer_spki_sha256="1" * 64,
                public_key_spki_base64=command,
                not_before="1",
                not_after="2",
            ),
            dict(
                id="auth",
                purpose="human-authority",
                workload="portal",
                peer_spki_sha256="2" * 64,
                public_key_spki_base64=authority,
                not_before="1",
                not_after="2",
            ),
        ],
    )
    read = _spki()
    human_keys = dict(
        scope=dict(tenant="amh", environment="dev", workload_ref="portal"),
        engine_name="default",
        database_incarnation="inc-1",
        keys={
            "human-assignment-read": dict(
                key_id="amh-dev-assignment-20260924",
                workload_ref="portal-assignment",
                public_key_spki_base64=read,
                fingerprint=_fp(read),
            )
        },
        peers=dict(read="3" * 64, command="1" * 64),
    )
    _, source = new_source_key("source-1")
    return spec, human_trust, human_keys, source


def test_trust_matches_the_engine_contract() -> None:
    spec, human_trust, human_keys, source = _inputs()
    raw, digest = build_trust(spec, human_trust, human_keys, source)
    value = json.loads(raw)
    assert canonicalize(value) == raw and digest == hashlib.sha256(raw).hexdigest()
    assert set(value) == {
        "schema",
        "tenant",
        "environment",
        "engine_name",
        "database_incarnation",
        "deployment_receipt",
        "validity_policy",
        "valid_until",
        "source",
        "publisher",
        "approved_policy_pins",
        "approved_binding_pins",
        "read_keys",
        "receipt_disclosure_source",
        "approved_receipt_policy_pins",
    }
    assert value["publisher"] == dict(
        workload_ref="portal", key_fingerprint=_fp(human_trust["keys"][1]["public_key_spki_base64"])
    )
    assert value["read_keys"][0]["id"] == "amh-dev-assignment-20260924"
    assert value["read_keys"][0]["peer_spki_sha256"] == "3" * 64
    assert value["source"]["key_id"] == "source-1" and value["valid_until"] == str(1791417600)
    # Primeira geracao sem policy/binding: claim/release/reassign continuam recusados pelo engine.
    assert value["approved_policy_pins"] == value["approved_binding_pins"] == []
    assert value["receipt_disclosure_source"] is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s, h, k, src: k["peers"].update(read="2" * 64),  # peer do human-authority
        lambda s, h, k, src: k["keys"]["human-assignment-read"].update(workload_ref="portal"),
        lambda s, h, k, src: src.update(
            public_key_spki_base64=h["keys"][0]["public_key_spki_base64"],
            fingerprint=_fp(h["keys"][0]["public_key_spki_base64"]),
        ),
        lambda s, h, k, src: s.update(tenant="outro"),
        lambda s, h, k, src: h["keys"].pop(),  # sem human-authority
        lambda s, h, k, src: src.update(fingerprint="0" * 64),
        lambda s, h, k, src: s.update(not_after=s["not_before"]),
    ],
)
def test_trust_refuses_what_the_engine_would_refuse(mutate: Any) -> None:
    spec, human_trust, human_keys, source = _inputs()
    mutate(spec, human_trust, human_keys, source)
    with pytest.raises(MaterialError):
        build_trust(spec, human_trust, human_keys, source)
