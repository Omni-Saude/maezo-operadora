"""T1.6 — as portas de producao do emissor (decisao D-H) e a composicao do `__main__`.

* D-H.6: `policy_ref` deterministico por designacao;
* D-H.3: o estado emitido sobrevive ao ledger byte a byte, e um ledger adulterado recusa;
* D-H.2: a designacao com DUAS entradas `identity_verifier` e aceita, e o witness do emissor so
  assina pela entrada propria (`case-issuer-witness`), com `session_ref = case-issuer-run:{id}`;
* D-H.1: escalacao fora de `ESC-{t}-sla-auth-{guia}` nao e ancorada (e nem chega ao banco);
* D-H.5: composicao sem tenant, com tenant divergente ou campo desconhecido sai com 2.

Banco e engine reais: `tests/integration/gateway/test_staff_case_issuer_ports_live.py`.
"""

from __future__ import annotations

import json
from base64 import b64encode
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from maezo.gateway.external_cases.models import digest, instant
from maezo.gateway.staff_cases.authority import InstalledStaffAuthority, fingerprint
from maezo.gateway.staff_cases.case_issuer import (
    CaseIssuerError,
    MemoryLedger,
    decode_state,
    encode_state,
    policy_ref_for,
)
from maezo.gateway.staff_cases.case_issuer_runtime import ENV, IssuerCompositionError, load_composition, main
from maezo.gateway.staff_cases.case_issuer_sources import AuthClaimAnchor, IssuerWitness, LiveEscalation
from maezo.gateway.staff_cases.models import Proof
from maezo.gateway.staff_cases.publisher import StaffSigner, StaffWitnessSource
from maezo.portal.contracts.models import MembershipBinding
from maezo.portal.engine.profile import canonicalize
from tests.unit.gateway.test_staff_case_issuer import (
    ESCALATIONS,
    MEMBERSHIP_SOURCE,
    NOW,
    SCOPE,
    A,
    B,
    C,
    run,
    world,
)

# ---------------------------------------------------------------- D-H.6


def test_policy_ref_is_the_designation_revision() -> None:
    assert policy_ref_for(1) == "staff-escalation-routing@d1"
    assert policy_ref_for("14") == "staff-escalation-routing@d14"
    for bad in (0, "01", "-1", "1a", ""):
        with pytest.raises(CaseIssuerError):
            policy_ref_for(bad)


# ---------------------------------------------------------------- D-H.3 (codec)


async def test_issued_state_round_trips_through_the_ledger_codec() -> None:
    w = world()
    ledger = MemoryLedger()
    job, sent = run(w, ESCALATIONS, (A, B, C), ledger)
    await job.run_once()
    state = ledger.load()
    assert sent and state.grants and state.checkpoints
    raw = encode_state(state)
    back = decode_state(raw, None)
    assert back == state and encode_state(back) == raw


@pytest.mark.parametrize(
    "tamper",
    [
        lambda d: d | {"extra": "1"},
        lambda d: d | {"source_revision": ""},
        lambda d: d | {"source_revision": "03"},
        lambda d: d | {"schema": "staff-case-issuer-state.v2"},
        lambda d: d | {"grants": d["grants"] + d["grants"]},
    ],
)
async def test_tampered_ledger_state_is_refused(tamper: Any) -> None:
    w = world()
    ledger = MemoryLedger()
    job, _ = run(w, ESCALATIONS, (A,), ledger)
    await job.run_once()
    doc = json.loads(encode_state(ledger.load()))
    with pytest.raises(CaseIssuerError, match="ledger_state_invalid"):
        decode_state(canonicalize(tamper(doc)), None)
    with pytest.raises(CaseIssuerError, match="ledger_state_invalid"):
        decode_state(encode_state(ledger.load()) + b" ", None)


# ---------------------------------------------------------------- D-H.2 (witness sem sessao)


def _entry(key: Ed25519PrivateKey, entry_ref: str, login: str) -> dict[str, Any]:
    start, end = instant(NOW - timedelta(minutes=1)), instant(NOW + timedelta(days=13))
    return {
        "entry_ref": entry_ref,
        "role": "identity_verifier",
        "source_namespace": "portal-identity",
        "source_ref": MEMBERSHIP_SOURCE,
        "key_fingerprint": fingerprint(key.public_key()),
        "certificate_spki": None,
        "public_key": b64encode(
            key.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            )
        ).decode(),
        "login_role": login,
        "purposes": ["membership_current"],
        "projections": [],
        "operations": [],
        "not_before": start,
        "valid_until": end,
    }


def _two_witness_authority() -> tuple[InstalledStaffAuthority, Ed25519PrivateKey, Ed25519PrivateKey]:
    root, portal, issuer = (Ed25519PrivateKey.generate() for _ in range(3))
    start, end = instant(NOW - timedelta(minutes=1)), instant(NOW + timedelta(days=13))
    designation = {
        "schema": "staff-case-designation.v1",
        "scope": SCOPE.wire(),
        "designation_ref": "designation",
        "designation_revision": "1",
        "expected_previous_revision": "0",
        "authority_ref": "approver",
        "authority_revision": "1",
        "entries": [
            _entry(portal, "portal-witness", "maezo_native_witness"),
            _entry(issuer, "case-issuer-witness", "maezo_native_issuer_witness"),
        ],
        "issued_at": start,
        "valid_until": end,
        "state": "active",
    }
    proof = {
        "schema": "staff-case-proof.v1",
        "purpose": "installation",
        "algorithm": "Ed25519",
        "key_fingerprint": fingerprint(root.public_key()),
        "issued_at": start,
        "expires_at": end,
        "statement_digest": digest(designation),
    }
    proof["signature"] = b64encode(root.sign(canonicalize(proof))).decode()
    authority = InstalledStaffAuthority.verify(
        designation_bytes=canonicalize(designation),
        installation_proof=Proof.model_validate(proof),
        expected_digest=digest(designation),
        expected_scope=SCOPE,
        root=root.public_key(),
        revoked_fingerprints=frozenset(),
        now=NOW,
    )
    return authority, portal, issuer


class FakeMembership:
    """O `NativeMembershipSource` real e exercitado em PG 17 (IT); aqui so a sua saida."""

    scope = SCOPE

    def __init__(self) -> None:
        self.principals: list[Any] = []

    async def observe(self, principal: Any) -> tuple[Any, dict[str, Any], Any]:
        from maezo.gateway.human.read_profile import SourceProvenance

        self.principals.append(principal)
        source = SourceProvenance(
            publisher_ref="identity-source",
            source_ref=MEMBERSHIP_SOURCE,
            source_revision=8,
            source_digest="c" * 64,
            receipt_ref="receipt",
            observed_at=NOW - timedelta(seconds=1),
            valid_until=NOW + timedelta(hours=1),
        )
        return source, {"revision": "19"}, NOW + timedelta(hours=1)


def _grantee_with_membership() -> Any:
    from dataclasses import replace

    return replace(
        A,
        memberships=(
            MembershipBinding(membership_ref="m", roles=("staff",), groups=tuple(sorted(A.groups))),
        ),
    )


async def test_designation_with_two_identity_verifiers_is_accepted_and_the_issuer_uses_its_own() -> None:
    authority, portal, issuer = _two_witness_authority()
    assert sorted(e.entry_ref for e in authority.entries.values()) == [
        "case-issuer-witness",
        "portal-witness",
    ]
    membership = FakeMembership()
    signer = StaffSigner(authority, issuer, "identity_verifier", clock=lambda: NOW)
    witness = IssuerWitness(
        StaffWitnessSource(membership, signer),  # type: ignore[arg-type]
        run_id="run-0123456789",
        clock=lambda: NOW,
    )
    grantee = _grantee_with_membership()
    observed = await witness(grantee)
    assert observed.session_ref == "case-issuer-run:run-0123456789"
    assert observed.proof.key_fingerprint == fingerprint(issuer.public_key())
    (principal,) = membership.principals
    assert principal.memberships == grantee.memberships and principal.session_ref == observed.session_ref
    # O verificador aceita o witness do emissor pela entrada PROPRIA (procurada por fingerprint).
    until = authority.membership(
        observed, principal, now=NOW, native_revision="19", native_digest=digest({"revision": "19"})
    )
    assert until > NOW


def test_issuer_witness_refuses_the_portal_witness_key() -> None:
    authority, portal, _ = _two_witness_authority()
    signer = StaffSigner(authority, portal, "identity_verifier", clock=lambda: NOW)
    with pytest.raises(CaseIssuerError, match="not_the_issuer_witness"):
        IssuerWitness(StaffWitnessSource(FakeMembership(), signer), run_id="run-0123456789")  # type: ignore[arg-type]


def test_grantee_without_membership_bindings_cannot_be_witnessed() -> None:
    with pytest.raises(CaseIssuerError, match="grantee_without_membership"):
        A.principal(session_ref="case-issuer-run:x", authenticated_at=NOW)


# ---------------------------------------------------------------- D-H.1 (so sla-auth)


class NoDatabase:
    def connect(self) -> Any:
        raise AssertionError("a ancora nao deve ler o banco para esta escalacao")


def _live(key: str, tenant: str = "amh") -> LiveEscalation:
    return LiveEscalation(
        tenant=tenant,
        escalation_ref="esc-1",
        business_key=key,
        task_id="t",
        grupo_atendimento="plantao-clinico",
    )


@pytest.mark.parametrize(
    ("key", "tenant", "reason"),
    [
        ("ESC-amh-conversa-123", "amh", "not_sla_auth"),
        ("ESC-outra-sla-auth-123", "amh", "not_sla_auth"),
        ("ESC-amh-sla-auth-", "amh", "invalid_guide"),
        ("ESC-amh-sla-auth-12 3", "amh", "invalid_guide"),
        ("ESC-amh-sla-auth-../x", "amh", "invalid_guide"),
        ("ESC-amh-sla-auth-123", "outra", "foreign_tenant"),
    ],
)
async def test_only_sla_auth_escalations_of_the_tenant_are_anchored(
    key: str, tenant: str, reason: str
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError("a ancora nao deve chamar o engine para esta escalacao")

    async with httpx.AsyncClient(transport=httpx.MockTransport(refuse), base_url="http://engine") as client:
        anchor = AuthClaimAnchor(client, NoDatabase(), tenant="amh", native_schema="maezo_native")  # type: ignore[arg-type]
        assert await anchor(_live(key, tenant)) is None
        assert anchor.last_reason == reason


@pytest.mark.parametrize("found", [[], [{"id": "i1"}, {"id": "i2"}]])
async def test_zero_or_many_auth_instances_are_unanchored(found: list[dict[str, str]]) -> None:
    seen: list[httpx.Request] = []

    def engine(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=found)

    async with httpx.AsyncClient(transport=httpx.MockTransport(engine), base_url="http://engine") as client:
        anchor = AuthClaimAnchor(client, NoDatabase(), tenant="amh", native_schema="maezo_native")  # type: ignore[arg-type]
        assert await anchor(_live("ESC-amh-sla-auth-G-77")) is None
    (request,) = seen
    assert request.url.params["businessKey"] == "AUTH-amh-G-77"
    assert request.url.params["processDefinitionKey"] == "SP-OP-AUTH-001"
    assert request.url.params["tenantIdIn"] == "amh"


async def test_engine_failure_is_not_unanchored_it_fails_the_round() -> None:
    def engine(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(engine), base_url="http://engine") as client:
        anchor = AuthClaimAnchor(client, NoDatabase(), tenant="amh", native_schema="maezo_native")  # type: ignore[arg-type]
        with pytest.raises(httpx.HTTPStatusError):
            await anchor(_live("ESC-amh-sla-auth-G-77"))


# ---------------------------------------------------------------- D-H.5 (composicao fail-closed)

SIBLINGS = (
    "designation.json",
    "installation-proof.json",
    "root.der",
    "case-issuer-key.pem",
    "witness-key.pem",
    "issuer-dsn.txt",
    "witness-dsn.txt",
    "native-ca.pem",
    "client-cert.pem",
    "client-key.pem",
    "importer-key.pem",
)


def composition(**changes: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema": "staff-case-issuer-composition.v1",
        "tenant": "amh",
        "environment": "dev",
        "engine_name": "default",
        "database_incarnation": "inc-1",
        "native_schema": "maezo_native",
        "membership_schema": "amh",
        "designation_file": "designation.json",
        "installation_proof_file": "installation-proof.json",
        "root_public_key_file": "root.der",
        "designation_digest": "a" * 64,
        "revoked_fingerprints": [],
        "case_issuer_key_file": "case-issuer-key.pem",
        "witness_key_file": "witness-key.pem",
        "issuer_dsn_file": "issuer-dsn.txt",
        "witness_dsn_file": "witness-dsn.txt",
        "relation_pins": {
            "mzo_portal_read_membership": {"oid": 10, "owner": "maezo_native_schema_owner"},
            "mzo_human_principal": {"oid": 11, "owner": "maezo_native_schema_owner"},
        },
        "engine_rest_url": "http://cibseven:8080/engine-rest",
        "native": {
            "origin": "https://engine.internal",
            "ca_file": "native-ca.pem",
            "client_certificate_file": "client-cert.pem",
            "client_key_file": "client-key.pem",
            "importer_key_file": "importer-key.pem",
            "server_spki_sha256": "b" * 64,
            "configuration_digest": "c" * 64,
        },
        "seconds": 5,
    }
    value.update(changes)
    return value


def _write(tmp_path: Path, value: dict[str, Any], *, skip: str | None = None) -> dict[str, str]:
    for name in SIBLINGS:
        if name != skip:
            (tmp_path / name).write_bytes(b"x")
    path = tmp_path / "composition.json"
    path.write_bytes(json.dumps(value).encode())
    return {ENV: str(path)}


def test_valid_composition_loads_with_the_explicit_tenant(tmp_path: Path) -> None:
    loaded = load_composition(_write(tmp_path, composition()))
    assert loaded.composition.tenant == "amh" and loaded.composition.scope.tenant == "amh"


@pytest.mark.parametrize(
    "value",
    [
        {k: v for k, v in composition().items() if k != "tenant"},
        composition(tenant=""),
        composition(membership_schema="outra"),
        composition(extra="1"),
        composition(native_schema="public"),
        composition(designation_file="../designation.json"),
        composition(seconds=0),
        composition(relation_pins={"mzo_portal_read_membership": {"oid": 10, "owner": "x"}}),
    ],
)
def test_composition_refusals(tmp_path: Path, value: dict[str, Any]) -> None:
    with pytest.raises(IssuerCompositionError):
        load_composition(_write(tmp_path, value))


def test_missing_sibling_or_missing_env_is_refused(tmp_path: Path) -> None:
    with pytest.raises(IssuerCompositionError):
        load_composition(_write(tmp_path, composition(), skip="witness-key.pem"))
    with pytest.raises(IssuerCompositionError):
        load_composition({})
    with pytest.raises(IssuerCompositionError):
        load_composition({ENV: "relative.json"})


def test_main_exits_2_without_leaking_configuration(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = _write(tmp_path, composition(tenant="segredo-nao-logado", membership_schema="outra"))
    assert main(env) == 2
    out = capsys.readouterr().out
    assert json.loads(out) == {"event": "staff_case_issuer_refused", "reason": "composition"}
    assert "segredo" not in out and str(tmp_path) not in out


def test_main_refuses_invalid_materials_with_2_before_touching_any_backend(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arquivos irmaos presentes mas invalidos (chave/designacao lixo): recusa de composicao, sem rede.
    assert main(_write(tmp_path, composition())) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "composition"
