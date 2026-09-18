"""SC1 synthetic signature controls; no native/database/owner activation claim."""

from base64 import b64encode
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from maezo.gateway.external_cases.models import Identity, Scope, digest, instant
from maezo.gateway.human.auth_profile import Actor
from maezo.gateway.human.read_profile import parse_model, wire
from maezo.gateway.staff_cases.authority import InstalledStaffAuthority, actor_digest, fingerprint
from maezo.gateway.staff_cases.models import (
    FIELDS,
    MembershipWitness,
    Proof,
    StaffCaseError,
    StaffPublication,
)
from maezo.portal.contracts.models import HumanPrincipal, MembershipBinding
from maezo.portal.engine.profile import ProfileError, canonicalize


def scenario():
    now = datetime(2026, 9, 10, 12, tzinfo=UTC)
    start, end = instant(now - timedelta(seconds=1)), instant(now + timedelta(seconds=30))
    root, issuer, identity_key = [Ed25519PrivateKey.generate() for _ in range(3)]
    scope = Scope(tenant="tenant", environment="test", engine_name="engine", database_incarnation="inc")
    principal = HumanPrincipal(
        schema_version=1,
        principal_ref="staff",
        issuer="https://issuer.test",
        subject="subject",
        tenant="tenant",
        membership_revision=7,
        memberships=(MembershipBinding(membership_ref="m", roles=("auditor",), groups=("med",)),),
        session_ref="session",
        authenticated_at=now,
        subject_bindings=(),
    )
    identity = Identity(
        upstream_resource_key="guide",
        case_ref="case_00000000001",
        process_instance_ref="instance",
        process_definition_id="definition",
        process_definition_key="SP-OP-AUTH-001",
        process_definition_version="3",
        process_definition_digest="a" * 64,
        kind="authorization",
    )

    def proof(key, purpose, statement):
        value = {
            "schema": "staff-case-proof.v1",
            "purpose": purpose,
            "algorithm": "Ed25519",
            "key_fingerprint": fingerprint(key.public_key()),
            "issued_at": start,
            "expires_at": end,
            "statement_digest": digest(statement),
        }
        return value | {"signature": b64encode(key.sign(canonicalize(value))).decode()}

    entries = []
    for key, role, source, purpose, projections, operations in (
        (issuer, "case_issuer", "policy", "staff_case_grant", list(FIELDS), ["detail"]),
        (identity_key, "identity_verifier", "membership-source", "membership_current", [], []),
    ):
        entries.append(
            {
                "entry_ref": role,
                "role": role,
                "source_namespace": role,
                "source_ref": source,
                "key_fingerprint": fingerprint(key.public_key()),
                "certificate_spki": None,
                "public_key": b64encode(
                    key.public_key().public_bytes(
                        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
                    )
                ).decode(),
                "login_role": role,
                "purposes": [purpose],
                "projections": projections,
                "operations": operations,
                "not_before": start,
                "valid_until": end,
            }
        )
    designation = {
        "schema": "staff-case-designation.v1",
        "scope": scope.wire(),
        "designation_ref": "designation",
        "designation_revision": "1",
        "expected_previous_revision": "0",
        "authority_ref": "owner",
        "authority_revision": "2",
        "entries": entries,
        "issued_at": start,
        "valid_until": end,
        "state": "active",
    }
    authority = InstalledStaffAuthority.verify(
        designation_bytes=canonicalize(designation),
        installation_proof=Proof.model_validate(proof(root, "installation", designation)),
        expected_digest=digest(designation),
        expected_scope=scope,
        root=root.public_key(),
        revoked_fingerprints=frozenset(),
        now=now,
    )
    witness = {
        "schema": "staff-case-membership-witness.v1",
        "scope": scope.wire(),
        "actor": wire(Actor.from_principal(principal, "staff")),
        "session_ref": "session",
        "principal_record_revision": "19",
        "principal_record_digest": "b" * 64,
        "source": {
            "publisher_ref": "identity-source",
            "source_ref": "membership-source",
            "source_revision": "8",
            "source_digest": "c" * 64,
            "receipt_ref": "receipt",
            "observed_at": start,
            "valid_until": end,
        },
        "observed_at": start,
        "valid_until": end,
    }
    witness["proof"] = proof(identity_key, "membership_current", witness)
    decisions = []
    for projection, fields in FIELDS.items():
        decision = {
            "decision_ref": projection,
            "policy_ref": "policy",
            "policy_revision": "4",
            "policy_digest": "d" * 64,
            "subject_identity_digest": actor_digest(principal),
            "membership_revision": "7",
            "resource_identity_digest": digest(identity.wire()),
            "operation": "detail",
            "projection": projection,
            "fields": sorted(fields),
            "receipt_ref": projection,
            "receipt_digest": "e" * 64,
            "observed_at": start,
            "valid_until": end,
            "state": "active",
        }
        decision["decision_proof"] = proof(issuer, "staff_case_grant", decision)
        decisions.append(decision)
    grant = {
        "grant_ref": "grant",
        "scope": scope.wire(),
        "case_ref": identity.case_ref,
        "identity_digest": digest(identity.wire()),
        "issuer": principal.issuer,
        "subject": principal.subject,
        "principal_ref": principal.principal_ref,
        "membership_revision": "7",
        "audience": "staff",
        "grant_revision": "1",
        "source_ref": "policy",
        "source_revision": "1",
        "decisions": decisions,
        "observed_at": start,
        "valid_until": end,
        "state": "active",
    }
    publication = {
        "schema": "staff-case-publication.v1",
        "scope": scope.wire(),
        "publication_id": "publication",
        "expected_source_revision": "0",
        "source_ref": "policy",
        "source_revision": "1",
        "kind": "case_grant",
        "membership_witness": witness,
        "payload": grant,
        "payload_digest": digest(grant),
        "observed_at": start,
        "valid_until": end,
    }
    publication["proof"] = proof(issuer, "staff_case_grant", publication)
    return authority, principal, identity, witness, publication, now, issuer, proof


def test_independent_issued_grant_keeps_actual_actor_codec_and_full_witness():
    authority, principal, identity, witness, publication, now, _, _ = scenario()
    parsed = parse_model(StaffPublication, publication)
    result = authority.grant(
        parsed,
        principal,
        identity,
        parse_model(MembershipWitness, witness),
        now=now,
        native_revision="19",
        native_digest="b" * 64,
    )
    assert result.membership_digest == digest(witness)
    assert result.actor_digest == digest(wire(Actor.from_principal(principal, "staff")))
    with pytest.raises(ProfileError):
        digest(Actor.from_principal(principal, "staff").model_dump(mode="json"))
    assert result.fields["staff_current_task.v1"] == FIELDS["staff_current_task.v1"] - {"created_at"}
    assert result.valid_until == now + timedelta(seconds=30)


@pytest.mark.parametrize(
    "attack",
    [
        "tampered_policy",
        "wrong_role",
        "revoked",
        "native_revision",
        "native_digest",
        "identity",
        "expired",
        "narrow_identity",
    ],
)
def test_staff_grant_does_not_replace_independent_authority(attack):
    authority, principal, identity, witness, publication, now, issuer, proof = scenario()
    native_revision, native_digest = "19", "b" * 64
    if attack == "tampered_policy":
        publication["payload"]["decisions"][0]["receipt_digest"] = "f" * 64
    elif attack == "wrong_role":
        publication["proof"] = witness["proof"]
    elif attack == "revoked":
        authority = replace(authority, revoked_fingerprints=frozenset({fingerprint(issuer.public_key())}))
    elif attack == "native_revision":
        native_revision = "7"  # server membership revision is NOT the native principal revision
    elif attack == "native_digest":
        native_digest = "f" * 64
    elif attack == "identity":
        identity = identity.model_copy(update={"process_instance_ref": "other"})
    elif attack == "expired":
        now += timedelta(seconds=31)
    elif attack == "narrow_identity":
        d = next(d for d in publication["payload"]["decisions"] if d["projection"] == "staff_identity.v1")
        d["fields"] = ["case_ref"]
        d.pop("decision_proof")
        d["decision_proof"] = proof(issuer, "staff_case_grant", d)
        publication["payload_digest"] = digest(publication["payload"])
        publication.pop("proof")
        publication["proof"] = proof(issuer, "staff_case_grant", publication)
    with pytest.raises(StaffCaseError):
        authority.grant(
            parse_model(StaffPublication, publication),
            principal,
            identity,
            parse_model(MembershipWitness, witness),
            now=now,
            native_revision=native_revision,
            native_digest=native_digest,
        )


def test_created_at_requires_distinct_actual_task_resource_decision():
    authority, principal, identity, witness, publication, now, issuer, proof = scenario()
    resource = {
        "scope": publication["scope"],
        "case_ref": identity.case_ref,
        "process_instance_id": identity.process_instance_ref,
        "task_id": "task",
        "task_definition_key": "UT_AvaliarPendencia",
    }
    d = dict(publication["payload"]["decisions"][2])
    d.update(
        decision_ref="task-created",
        projection="staff_current_task.v1",
        fields=["created_at"],
        resource_identity_digest=digest(resource),
    )
    d.pop("decision_proof")
    d["decision_proof"] = proof(issuer, "staff_case_grant", d)
    publication["payload"]["decisions"].append(d)
    publication["payload_digest"] = digest(publication["payload"])
    publication.pop("proof")
    publication["proof"] = proof(issuer, "staff_case_grant", publication)
    result = authority.grant(
        parse_model(StaffPublication, publication),
        principal,
        identity,
        parse_model(MembershipWitness, witness),
        now=now,
        native_revision="19",
        native_digest="b" * 64,
    )
    assert "created_at" not in result.fields["staff_current_task.v1"]
    assert len(result.task_decisions) == 1
    assert result.task_decisions[0].resource_identity_digest == digest(resource)
    assert digest(resource | {"task_id": "another"}) != result.task_decisions[0].resource_identity_digest


def _detail_observation(identity, witness, now, *, state="active", outcome=None):
    from maezo.gateway.human.read_profile import utc

    until = utc(now + timedelta(seconds=5))
    projection = {
        "schema": "portal-staff-case-detail.v1",
        "case": {
            "case_ref": identity.case_ref,
            "kind": "authorization",
            "state": state,
            "record_revision": "9",
            "state_observed_at": utc(now),
        },
        "identity": identity.wire(),
        "active_tasks": [],
        "next_task_cursor": None,
        "tasks_complete": True,
        "freshness": {
            "observed_at": utc(now),
            "source_observed_at": utc(now),
            "valid_until": until,
            "refresh_after_seconds": "10",
        },
        "outcome": outcome,
    }
    pins = [
        {"kind": kind, "ref": ref, "revision": "1", "digest": "a" * 64, "valid_until": until}
        for kind, ref in (
            ("designation", "designation"),
            ("identity", identity.case_ref),
            ("membership", "staff"),
            ("case_grant", "grant"),
            ("native_case", identity.case_ref),
            ("policy_head", "policy"),
        )
    ]
    fake_proof = dict(witness["proof"], purpose="native_result")
    return {
        "schema": "staff-case-observation.v1",
        "request_digest": "a" * 64,
        "projection": projection,
        "continuity_ref": "continuity",
        "continuity_digest": "b" * 64,
        "source_pins": pins,
        "observed_at": utc(now),
        "valid_until": until,
        "proof": fake_proof,
    }


@pytest.mark.parametrize(
    "attack",
    [
        "wrong_case",
        "page_incomplete",
        "over_limit",
        "missing_policy",
        "duplicate_pin",
        "missing_task_pin",
        "expired",
    ],
)
def test_exact_case_observation_refuses_unqualified_projection(attack):
    from copy import deepcopy

    from maezo.gateway.staff_cases.service import observation

    _, _, identity, witness, _, now, _, _ = scenario()
    value = deepcopy(_detail_observation(identity, witness, now))
    case = "case_00000000002" if attack == "wrong_case" else identity.case_ref
    if attack == "page_incomplete":
        value["projection"].update(tasks_complete=False, next_task_cursor="cursor")
    if attack == "over_limit" or attack == "missing_task_pin":
        value["projection"]["active_tasks"] = [
            {
                "task_id": "task",
                "task_definition_key": "UT_Test",
                "task_revision": "2",
                "created_at": value["observed_at"],
                "due_at": None,
                "assignee_ref": None,
            }
        ]
    if attack == "missing_policy":
        value["source_pins"] = [p for p in value["source_pins"] if p["kind"] != "policy_head"]
    if attack == "duplicate_pin":
        value["source_pins"].append(value["source_pins"][0])
    if attack == "expired":
        now += timedelta(seconds=6)
    with pytest.raises((StaffCaseError, ProfileError)):
        observation(
            value, request_digest="a" * 64, case_ref=case, limit=0 if attack == "over_limit" else 25, now=now
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", ["accepted", "changed_receipt", "expired_during_session_release", "cursor", "beneficiary"]
)
async def test_service_freezes_original_and_checks_after_all_awaited_release(mode):
    from contextlib import asynccontextmanager
    from copy import deepcopy
    from types import SimpleNamespace

    from maezo.gateway.staff_cases.service import StaffCaseService

    _, principal, identity, witness, _, now, _, _ = scenario()
    clock = [now]
    original = _detail_observation(identity, witness, now)
    calls = []

    class Resolver:
        async def resolve(self, secret):
            return SimpleNamespace(
                principal=principal,
                record=SimpleNamespace(expires_at=now + timedelta(seconds=20)),
                membership=SimpleNamespace(
                    audience="beneficiary" if mode == "beneficiary" else "staff",
                    reviewed_until=now + timedelta(seconds=20),
                ),
            )

    class Witnesses:
        async def observe(self, expected, until):
            assert expected == principal
            return parse_model(MembershipWitness, witness)

    class Native:
        async def read(self, **request):
            calls.append(request)
            value = deepcopy(original)
            if request["operation"] == "finalize":
                assert request["query"]["frozen_projection_digest"] == digest(original["projection"])
                assert request["witness"] == parse_model(MembershipWitness, witness)
                if mode == "changed_receipt":
                    value["continuity_digest"] = "f" * 64
            return value, "a" * 64

    class Lease:
        clock = staticmethod(lambda: clock[0])

        @asynccontextmanager
        async def acquire(self, secret, expected):
            assert expected == principal
            yield SimpleNamespace(valid_until=now + timedelta(seconds=20), current=lambda now: None)
            if mode == "expired_during_session_release":
                clock[0] += timedelta(seconds=6)

    service = object.__new__(StaffCaseService)
    service.resolver, service.witnesses, service.native, service.lease = (
        Resolver(),
        Witnesses(),
        Native(),
        Lease(),
    )
    if mode == "accepted":
        import json

        raw = await service.read("s" * 43, case_ref=identity.case_ref)
        expected_public = deepcopy(original["projection"])
        expected_public["freshness"]["refresh_after_seconds"] = 10
        assert json.loads(raw) == expected_public
        assert type(json.loads(raw)["freshness"]["refresh_after_seconds"]) is int
        assert original["projection"]["freshness"]["refresh_after_seconds"] == "10"
        assert [c["operation"] for c in calls] == ["detail", "finalize"]
    else:
        with pytest.raises(StaffCaseError):
            await service.read(
                "s" * 43, case_ref=identity.case_ref, task_cursor="cursor" if mode == "cursor" else None
            )
        if mode in {"cursor", "beneficiary"}:
            assert not calls


@pytest.mark.parametrize("cadence", ["11", 10, 10.0, True])
def test_native_cadence_refuses_values_outside_exact_signed_grammar(cadence):
    from maezo.gateway.staff_cases.service import observation

    _, _, identity, witness, _, now, _, _ = scenario()
    value = _detail_observation(identity, witness, now)
    value["projection"]["freshness"]["refresh_after_seconds"] = cadence
    with pytest.raises((StaffCaseError, ProfileError)):
        observation(value, request_digest="a" * 64, case_ref=identity.case_ref, limit=25, now=now)


_STAFF_APROVADA = {
    "phase": "decisao_executada",
    "desfecho": "aprovada_auditor",
    "authorization_ref": "c" * 64,
}


def test_staff_detail_projects_the_engine_outcome_of_an_ended_case():
    from maezo.gateway.staff_cases.service import observation

    _, _, identity, witness, _, now, _, _ = scenario()
    value = _detail_observation(identity, witness, now, state="ended", outcome=_STAFF_APROVADA)
    result = observation(value, request_digest="a" * 64, case_ref=identity.case_ref, limit=25, now=now)
    assert result.projection.outcome is not None
    assert result.projection.outcome.desfecho == "aprovada_auditor"
    assert result.projection.outcome.phase == "decisao_executada"
    assert result.projection.outcome.authorization_ref == "c" * 64


@pytest.mark.parametrize(
    ("state", "outcome"),
    [
        # Fecha por ausencia: encerrado sem registro de decisao nao vira projecao.
        ("ended", None),
        # Desfecho sem encerramento seria inferencia de estado do motor.
        ("active", _STAFF_APROVADA),
        # Vocabulario fechado: so os cinco `event_desfecho` do BPMN SP-OP-AUTH-001.
        ("ended", {**_STAFF_APROVADA, "desfecho": "aprovada_por_omissao"}),
        # BPMN :546 nao declara `numero_autorizacao` para a negativa.
        ("ended", {**_STAFF_APROVADA, "desfecho": "negada_auditor"}),
        # Referencia opaca: o composto TISS nao satisfaz o formato hexadecimal.
        ("ended", {**_STAFF_APROVADA, "authorization_ref": "AUTH-tenant-12345-abcdef12"}),
        # Nenhum campo clinico atravessa a projecao de leitura do colaborador.
        ("ended", {**_STAFF_APROVADA, "justificativa_clinica": "PRIVATE_CANARY"}),
    ],
)
def test_staff_detail_outcome_fails_closed_against_inconsistent_engine_records(state, outcome):
    from maezo.gateway.staff_cases.service import observation

    _, _, identity, witness, _, now, _, _ = scenario()
    value = _detail_observation(identity, witness, now, state=state, outcome=outcome)
    with pytest.raises((StaffCaseError, ProfileError)):
        observation(value, request_digest="a" * 64, case_ref=identity.case_ref, limit=25, now=now)


def _page_observation(identity, witness, now):
    observed, valid = instant(now), instant(now + timedelta(seconds=5))

    def pin(kind, ref, revision):
        return {
            "kind": kind,
            "ref": ref,
            "revision": revision,
            "digest": "b" * 64,
            "valid_until": valid,
        }

    return {
        "schema": "staff-case-observation.v1",
        "request_digest": "a" * 64,
        "projection": {
            "schema": "portal-staff-case-page.v1",
            "items": [
                {
                    "case_ref": identity.case_ref,
                    "kind": "authorization",
                    "state": "active",
                    "record_revision": "7",
                    "state_observed_at": observed,
                }
            ],
            "next_cursor": "cursor-opaque",
            "freshness": {
                "observed_at": observed,
                "source_observed_at": observed,
                "valid_until": valid,
                "refresh_after_seconds": "10",
            },
        },
        "continuity_ref": "continuity",
        "continuity_digest": "c" * 64,
        "source_pins": [
            pin("designation", "designation", "1"),
            pin("membership", "staff", "7"),
            pin("checkpoint", "checkpoint", "4"),
            pin("identity", identity.case_ref, "3"),
            pin("case_grant", identity.case_ref, "1"),
            pin("native_case", identity.case_ref, "7"),
            pin("source_key", "d" * 64, "1"),
        ],
        "observed_at": observed,
        "valid_until": valid,
        "proof": {
            "schema": "staff-case-proof.v1",
            "purpose": "native_result",
            "algorithm": "Ed25519",
            "key_fingerprint": "d" * 64,
            "issued_at": observed,
            "expires_at": valid,
            "statement_digest": "e" * 64,
            "signature": "AA==",
        },
    }


@pytest.mark.asyncio
async def test_list_service_preserves_native_cursor_and_numeric_public_freshness():
    from contextlib import asynccontextmanager
    from copy import deepcopy
    from types import SimpleNamespace

    from maezo.gateway.staff_cases.service import StaffCaseService

    _, principal, identity, witness, _, now, _, _ = scenario()
    original = _page_observation(identity, witness, now)
    calls = []

    class Resolver:
        async def resolve(self, secret):
            return SimpleNamespace(
                principal=principal,
                record=SimpleNamespace(expires_at=now + timedelta(seconds=20)),
                membership=SimpleNamespace(audience="staff", reviewed_until=now + timedelta(seconds=20)),
            )

    class Native:
        async def read(self, **request):
            calls.append(request)
            return deepcopy(original), "a" * 64

    class Witnesses:
        async def observe(self, expected, until):
            assert expected == principal
            return parse_model(MembershipWitness, witness)

    class Lease:
        clock = staticmethod(lambda: now)

        @asynccontextmanager
        async def acquire(self, secret, expected):
            yield SimpleNamespace(valid_until=now + timedelta(seconds=20), current=lambda value: None)

    service = object.__new__(StaffCaseService)
    service.resolver, service.witnesses, service.native, service.lease = (
        Resolver(),
        Witnesses(),
        Native(),
        Lease(),
    )
    raw = await service.list("s" * 43, limit="25")
    assert __import__("json").loads(raw)["freshness"]["refresh_after_seconds"] == 10
    assert [call["operation"] for call in calls] == ["list", "finalize"]
    assert calls[0]["query"] == {"kind": "authorization", "limit": "25", "cursor": None}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,effect,expected", [(404, False, "denied"), (409, False, "conflict"), (404, True, "uncertain")]
)
async def test_authenticated_native_read_refusal_and_possible_publication_effect(status, effect, expected):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from cryptography import x509
    from cryptography.x509.oid import NameOID

    from maezo.gateway.staff_cases.publisher import StaffNativeClient

    _, _, _, _, _, now, key, _ = scenario()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthetic-native")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now - timedelta(seconds=1))
        .not_valid_after(now + timedelta(seconds=30))
        .sign(key, algorithm=None)
    )
    der = cert.public_bytes(serialization.Encoding.DER)
    closed = []
    response = SimpleNamespace(
        status_code=status,
        headers={},
        extensions={
            "network_stream": SimpleNamespace(
                get_extra_info=lambda name: SimpleNamespace(getpeercert=lambda binary_form: der)
            )
        },
    )

    class Http:
        @asynccontextmanager
        async def stream(self, *args, **kwargs):
            try:
                yield response
            finally:
                closed.append(True)

    client = object.__new__(StaffNativeClient)
    client.closed = False
    client.seconds = 1
    client.origin = "https://native.test"
    client.server_pin = fingerprint(key.public_key())
    client.signer = SimpleNamespace(guard=lambda purpose: now, clock=lambda: now)
    client.http = Http()
    with pytest.raises(StaffCaseError) as refused:
        await client._send(
            "staff-case-detail",
            {"closed": True},
            "staff-case-read.v1",
            now + timedelta(seconds=1),
            effect=effect,
        )
    assert refused.value.code == expected
    assert closed == [True]


@pytest.mark.asyncio
async def test_service_preserves_expired_session_authentication_error():
    from maezo.gateway.staff_cases.service import StaffCaseService
    from maezo.portal.api.auth import AuthenticationError

    _, _, identity, _, _, _, _, _ = scenario()

    class Resolver:
        async def resolve(self, secret):
            raise AuthenticationError()

    service = object.__new__(StaffCaseService)
    service.resolver = Resolver()
    with pytest.raises(AuthenticationError):
        await service.read("s" * 43, case_ref=identity.case_ref)
