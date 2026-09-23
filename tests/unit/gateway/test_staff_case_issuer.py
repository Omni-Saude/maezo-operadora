"""T1.6 — fonte de publicacao de casos staff (`case_issuer`), com a decisao N3 do dono.

N3 (23/09/2026): cada caso e visivel SO ao grupo que a DMN `escalation_routing` escolheu para a
escalacao (`grupo_atendimento`: P1/red flag -> `plantao-clinico`, P3 administrativo ->
`atendimento-humano`, o resto -> `enfermagem-triagem`). Restricao da Onda 7: nenhum grant leva a
projecao `staff_current_task.v1`.

As publicacoes sao conferidas pelo verificador Python que espelha o Java
(`InstalledStaffAuthority.grant` / `.checkpoint`), com designacao, raiz e witness sinteticos
gerados aqui. Nada aqui fala com engine ou banco: os adaptadores tem teste proprio.
"""

from __future__ import annotations

from base64 import b64encode
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from maezo.gateway.external_cases.models import Identity, Scope, digest, instant
from maezo.gateway.human.auth_profile import Actor
from maezo.gateway.human.read_profile import parse_model, wire
from maezo.gateway.staff_cases.authority import InstalledStaffAuthority, fingerprint
from maezo.gateway.staff_cases.case_issuer import (
    DECISIONS,
    CaseIssuerError,
    GrantAction,
    IssuerState,
    MemoryLedger,
    PolicyHeadAction,
    RevokeGrantAction,
    RevokeScopeAction,
    RoutedEscalation,
    ScopeAction,
    StaffCaseIssuer,
    StaffCaseIssuerJob,
    StaffCasePolicy,
    StaffGrantee,
    plan,
    visible_cases,
)
from maezo.gateway.staff_cases.models import MembershipWitness, Proof, StaffCaseError, StaffPublication
from maezo.gateway.staff_cases.publisher import StaffSigner
from maezo.portal.api.records import MembershipRecord
from maezo.portal.contracts.models import HumanPrincipal, MembershipBinding
from maezo.portal.engine.profile import canonicalize, strict_loads

NOW = datetime(2026, 9, 23, 12, tzinfo=UTC)
SCOPE = Scope(tenant="amh", environment="dev", engine_name="default", database_incarnation="inc-1")
ISSUER = "https://cognito-idp.sa-east-1.amazonaws.com/sa-east-1_test"
SOURCE = "staff-cases-amh"
POLICY = "staff-escalation-policy"
MEMBERSHIP_SOURCE = "membership-amh"
GROUPS = {
    "plantao": "plantao-clinico",
    "atendimento": "atendimento-humano",
    "enfermagem": "enfermagem-triagem",
}


def identity(n: int) -> Identity:
    return Identity(
        upstream_resource_key=f"guide-{n}",
        case_ref=f"case_{n:012d}",
        process_instance_ref=f"auth-instance-{n}",
        process_definition_id="SP-OP-AUTH-001:3:def",
        process_definition_key="SP-OP-AUTH-001",
        process_definition_version="3",
        process_definition_digest="a" * 64,
        kind="authorization",
    )


def escalation(n: int, group: str, *, tenant: str = "amh", ref: str | None = None) -> RoutedEscalation:
    return RoutedEscalation(
        tenant=tenant, escalation_ref=ref or f"esc-instance-{n}", case=identity(n), grupo_atendimento=group
    )


def grantee(name: str, *groups: str, revision: int = 7, tenant: str = "amh") -> StaffGrantee:
    return StaffGrantee(
        tenant=tenant,
        principal_ref=f"principal-{name}",
        issuer=ISSUER,
        subject=f"subject-{name}",
        membership_revision=revision,
        groups=frozenset(groups),
    )


def principal(g: StaffGrantee) -> HumanPrincipal:
    return HumanPrincipal(
        schema_version=1,
        principal_ref=g.principal_ref,
        issuer=g.issuer,
        subject=g.subject,
        tenant=g.tenant,
        membership_revision=g.membership_revision,
        memberships=(
            MembershipBinding(membership_ref="m", roles=("staff",), groups=tuple(sorted(g.groups))),
        ),
        session_ref="session-" + g.principal_ref,
        authenticated_at=NOW,
        subject_bindings=(),
    )


@dataclass
class World:
    authority: InstalledStaffAuthority
    issuer_private: Ed25519PrivateKey
    witness_private: Ed25519PrivateKey
    signer: StaffSigner
    policy: StaffCasePolicy

    def witness(self, g: StaffGrantee) -> MembershipWitness:
        start, end = instant(NOW - timedelta(seconds=1)), instant(NOW + timedelta(hours=1))
        value: dict[str, Any] = {
            "schema": "staff-case-membership-witness.v1",
            "scope": SCOPE.wire(),
            "actor": wire(Actor.from_principal(principal(g), "staff")),
            "session_ref": "session-" + g.principal_ref,
            "principal_record_revision": "19",
            "principal_record_digest": "b" * 64,
            "source": {
                "publisher_ref": "identity-source",
                "source_ref": MEMBERSHIP_SOURCE,
                "source_revision": "8",
                "source_digest": "c" * 64,
                "receipt_ref": "receipt",
                "observed_at": start,
                "valid_until": end,
            },
            "observed_at": start,
            "valid_until": end,
        }
        unsigned = {
            "schema": "staff-case-proof.v1",
            "purpose": "membership_current",
            "algorithm": "Ed25519",
            "key_fingerprint": fingerprint(self.witness_private.public_key()),
            "issued_at": start,
            "expires_at": end,
            "statement_digest": digest(value),
        }
        value["proof"] = unsigned | {
            "signature": b64encode(self.witness_private.sign(canonicalize(unsigned))).decode()
        }
        return parse_model(MembershipWitness, value)

    def issuer(self) -> StaffCaseIssuer:
        return StaffCaseIssuer(signer=self.signer, scope=SCOPE, source_ref=SOURCE, policy=self.policy)


def world(*, issuer_projections: list[str] | None = None) -> World:
    start, end = instant(NOW - timedelta(minutes=1)), instant(NOW + timedelta(days=13))
    root, issuer_private, witness_private = (Ed25519PrivateKey.generate() for _ in range(3))

    def entry(
        key: Ed25519PrivateKey, role: str, namespace: str, source: str, purposes: list[str], **caps: Any
    ):
        return {
            "entry_ref": role,
            "role": role,
            "source_namespace": namespace,
            "source_ref": source,
            "key_fingerprint": fingerprint(key.public_key()),
            "certificate_spki": None,
            "public_key": b64encode(
                key.public_key().public_bytes(
                    serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
                )
            ).decode(),
            "login_role": role,
            "purposes": purposes,
            "projections": caps.get("projections", []),
            "operations": caps.get("operations", []),
            "not_before": start,
            "valid_until": end,
        }

    designation = {
        "schema": "staff-case-designation.v1",
        "scope": SCOPE.wire(),
        "designation_ref": "designation",
        "designation_revision": "1",
        "expected_previous_revision": "0",
        "authority_ref": "approver",
        "authority_revision": "1",
        "entries": [
            entry(
                issuer_private,
                "case_issuer",
                POLICY,
                SOURCE,
                ["staff_case_grant", "staff_policy_head", "scope_complete"],
                projections=issuer_projections or ["staff_identity.v1", "staff_summary.v1"],
                operations=["detail", "list"],
            ),
            entry(
                witness_private,
                "identity_verifier",
                "portal-identity",
                MEMBERSHIP_SOURCE,
                ["membership_current"],
            ),
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
    signer = StaffSigner(authority, issuer_private, "case_issuer", clock=lambda: NOW)
    return World(
        authority,
        issuer_private,
        witness_private,
        signer,
        StaffCasePolicy(policy_ref=POLICY, policy_revision=1),
    )


def verify_grant(
    w: World, publication: StaffPublication, g: StaffGrantee, case: Identity, operation: str = "detail"
):
    return w.authority.grant(
        publication,
        principal(g),
        case,
        w.witness(g),
        now=NOW,
        native_revision="19",
        native_digest="b" * 64,
        operation=operation,
    )


# ---------------------------------------------------------------- N3: o dominio puro


A = grantee("a", GROUPS["plantao"])
B = grantee("b", GROUPS["enfermagem"])
C = grantee("c", GROUPS["atendimento"])
ESCALATIONS = (
    escalation(1, GROUPS["plantao"]),  # P1 / red flag grave
    escalation(2, GROUPS["enfermagem"]),  # red flag nao grave / intencao clinica
    escalation(3, GROUPS["atendimento"]),  # P3 administrativo
)


def test_n3_each_case_is_visible_only_to_the_group_the_dmn_chose() -> None:
    assert set(visible_cases(ESCALATIONS, A)) == {identity(1).case_ref}
    assert set(visible_cases(ESCALATIONS, B)) == {identity(2).case_ref}
    assert set(visible_cases(ESCALATIONS, C)) == {identity(3).case_ref}
    both = grantee("d", GROUPS["plantao"], GROUPS["atendimento"])
    assert set(visible_cases(ESCALATIONS, both)) == {identity(1).case_ref, identity(3).case_ref}
    assert visible_cases(ESCALATIONS, grantee("e", "supervisao-atendimento")) == {}


@pytest.mark.parametrize(
    "near_miss", ["Plantao-Clinico", "plantao-clinico ", "plantao", "plantao-clinico-2", ""]
)
def test_group_match_is_exact_never_prefix_or_case_folded(near_miss: str) -> None:
    assert visible_cases(ESCALATIONS, grantee("x", near_miss)) == {}


def test_other_tenant_escalations_never_reach_a_grantee() -> None:
    foreign = (escalation(9, GROUPS["plantao"], tenant="outra"),)
    assert visible_cases(foreign, A) == {}
    with pytest.raises(CaseIssuerError):
        plan(
            escalations=foreign,
            grantees=(A,),
            state=IssuerState.empty(),
            policy=world().policy,
            scope=SCOPE,
            now=NOW,
        )


def test_two_live_escalations_of_one_case_make_it_visible_to_each_chosen_group() -> None:
    twice = (escalation(5, GROUPS["plantao"], ref="esc-a"), escalation(5, GROUPS["enfermagem"], ref="esc-b"))
    assert set(visible_cases(twice, A)) == set(visible_cases(twice, B)) == {identity(5).case_ref}
    assert visible_cases(twice, C) == {}


def _record(**changes: Any) -> MembershipRecord:
    value: dict[str, Any] = dict(
        tenant="amh",
        issuer=ISSUER,
        subject="subject-a",
        principal_ref="principal-a",
        revision=7,
        audience="staff",
        memberships=(MembershipBinding(membership_ref="m", roles=("staff",), groups=(GROUPS["plantao"],)),),
        subject_bindings=(),
        reviewed_until=NOW + timedelta(days=1),
        revoked=False,
    )
    value.update(changes)
    return MembershipRecord(**value)


def test_grantee_comes_only_from_a_current_staff_membership_of_the_tenant() -> None:
    assert StaffGrantee.from_membership(_record(), tenant="amh", now=NOW) == A
    assert StaffGrantee.from_membership(_record(revoked=True), tenant="amh", now=NOW) is None
    assert StaffGrantee.from_membership(_record(reviewed_until=NOW), tenant="amh", now=NOW) is None
    assert StaffGrantee.from_membership(_record(tenant="outra"), tenant="amh", now=NOW) is None
    beneficiary = _record(
        audience="beneficiary",
        subject_bindings=({"kind": "beneficiary", "resource_ref": "ben-1"},),
    )
    assert StaffGrantee.from_membership(beneficiary, tenant="amh", now=NOW) is None


# ---------------------------------------------------------------- o plano de publicacao


def test_first_plan_publishes_policy_then_grants_then_one_checkpoint_per_grantee() -> None:
    actions = plan(
        escalations=ESCALATIONS,
        grantees=(A, B, C),
        state=IssuerState.empty(),
        policy=world().policy,
        scope=SCOPE,
        now=NOW,
    ).actions
    assert isinstance(actions[0], PolicyHeadAction)
    grants = [a for a in actions if isinstance(a, GrantAction)]
    assert {(a.grantee.principal_ref, a.case.case_ref) for a in grants} == {
        (A.principal_ref, identity(1).case_ref),
        (B.principal_ref, identity(2).case_ref),
        (C.principal_ref, identity(3).case_ref),
    }
    scopes = [a for a in actions if isinstance(a, ScopeAction)]
    assert [a.grantee.principal_ref for a in scopes] == sorted(g.principal_ref for g in (A, B, C))
    # Ordem: politica -> grants -> checkpoints (o engine exige a politica ativa antes do grant).
    kinds = [type(a).__name__ for a in actions]
    assert kinds == sorted(
        kinds, key=["PolicyHeadAction", "GrantAction", "RevokeGrantAction", "ScopeAction"].index
    )


# ---------------------------------------------------------------- publicacoes assinadas


def run(
    w: World,
    escalations: tuple[RoutedEscalation, ...],
    grantees: tuple[StaffGrantee, ...],
    ledger: MemoryLedger,
):
    sent: list[StaffPublication] = []

    async def publish(raw: bytes) -> dict[str, Any]:
        publication = parse_model(StaffPublication, strict_loads(raw))
        sent.append(publication)
        return {"source_revision": publication.source_revision, "publication_id": publication.publication_id}

    job = StaffCaseIssuerJob(
        issuer=w.issuer(),
        escalations=lambda: escalations,
        grantees=lambda: grantees,
        witness=w.witness,
        ledger=ledger,
        publish=publish,
        clock=lambda: NOW,
    )
    return job, sent


async def test_isolation_grant_verifies_for_its_group_member_and_is_denied_to_another_group() -> None:
    w = world()
    ledger = MemoryLedger()
    job, sent = run(w, ESCALATIONS, (A, B, C), ledger)
    await job.run_once()
    grants = {(p.payload.principal_ref, p.payload.case_ref): p for p in sent if p.kind == "case_grant"}
    assert set(grants) == {
        (A.principal_ref, identity(1).case_ref),
        (B.principal_ref, identity(2).case_ref),
        (C.principal_ref, identity(3).case_ref),
    }
    a_case1 = grants[(A.principal_ref, identity(1).case_ref)]
    verified = verify_grant(w, a_case1, A, identity(1))
    assert set(verified.fields) == {"staff_summary.v1", "staff_identity.v1"}
    # B (enfermagem) apresentando o grant de A: o grant nomeia OUTRO principal -> negado.
    with pytest.raises(StaffCaseError):
        verify_grant(w, a_case1, B, identity(1))
    # Nenhum grant do caso 1 existe para B nem para C.
    assert not any(case == identity(1).case_ref and who != A.principal_ref for who, case in grants)


async def test_no_grant_carries_the_current_task_projection_and_detail_answers_without_tasks() -> None:
    w = world()
    job, sent = run(w, ESCALATIONS, (A,), MemoryLedger())
    await job.run_once()
    grant = next(p for p in sent if p.kind == "case_grant")
    projections = {d.projection for d in grant.payload.decisions}
    assert "staff_current_task.v1" not in projections
    assert {(d.operation, d.projection) for d in grant.payload.decisions} == set(DECISIONS)
    detail = verify_grant(w, grant, A, identity(1))
    assert "staff_current_task.v1" not in detail.fields and detail.task_decisions == ()
    listed = verify_grant(w, grant, A, identity(1), operation="list")
    assert set(listed.fields) == {"staff_summary.v1"}
    assert all(p not in canonicalize(grant.wire()).decode() for p in ("staff_current_task", "task_id"))


def test_decisions_are_a_closed_constant_without_current_task() -> None:
    assert DECISIONS == (
        ("detail", "staff_identity.v1"),
        ("detail", "staff_summary.v1"),
        ("list", "staff_summary.v1"),
    )


async def test_checkpoint_lists_exactly_the_grantee_cases_and_verifies() -> None:
    w = world()
    both = grantee("d", GROUPS["plantao"], GROUPS["atendimento"])
    job, sent = run(w, ESCALATIONS, (both, B), MemoryLedger())
    await job.run_once()
    checkpoints = {p.payload.principal_ref: p for p in sent if p.kind == "scope_checkpoint"}
    chunks = [p for p in sent if p.kind == "scope_chunk"]
    d_checkpoint = checkpoints[both.principal_ref]
    checkpoint, _ = w.authority.checkpoint(
        d_checkpoint, principal(both), w.witness(both), now=NOW, native_revision="19", native_digest="b" * 64
    )
    d_chunks = [c for c in chunks if c.payload.checkpoint_ref == checkpoint.checkpoint_ref]
    listed = sorted(e.case_ref for c in d_chunks for e in c.payload.entries)
    assert listed == sorted([identity(1).case_ref, identity(3).case_ref])
    assert int(checkpoint.total_entries) == 2 and checkpoint.coverage == "complete"
    # O checkpoint de D nao serve para B.
    with pytest.raises(StaffCaseError):
        w.authority.checkpoint(
            d_checkpoint, principal(B), w.witness(B), now=NOW, native_revision="19", native_digest="b" * 64
        )


async def test_grantee_with_no_case_gets_an_empty_complete_checkpoint() -> None:
    w = world()
    lonely = grantee("z", "supervisao-atendimento")
    job, sent = run(w, ESCALATIONS, (lonely,), MemoryLedger())
    await job.run_once()
    assert not [p for p in sent if p.kind == "case_grant"]
    (checkpoint,) = [p for p in sent if p.kind == "scope_checkpoint"]
    assert checkpoint.payload.total_entries == "0" and checkpoint.payload.chunks == ()


async def test_source_revisions_are_a_gapless_cas_sequence_and_publication_ids_are_unique() -> None:
    w = world()
    job, sent = run(w, ESCALATIONS, (A, B, C), MemoryLedger())
    await job.run_once()
    assert [int(p.source_revision) for p in sent] == list(range(1, len(sent) + 1))
    assert [int(p.expected_source_revision) for p in sent] == list(range(0, len(sent)))
    assert len({p.publication_id for p in sent}) == len(sent)
    assert all(p.source_ref == SOURCE and p.scope == SCOPE for p in sent)
    assert sent[0].kind == "policy_head"


async def test_second_run_without_change_publishes_nothing() -> None:
    w = world()
    ledger = MemoryLedger()
    job, sent = run(w, ESCALATIONS, (A, B, C), ledger)
    await job.run_once()
    first = len(sent)
    await job.run_once()
    assert len(sent) == first


async def test_closed_escalation_revokes_the_grant_and_republishes_the_checkpoint() -> None:
    w = world()
    ledger = MemoryLedger()
    job, sent = run(w, ESCALATIONS, (A, B), ledger)
    await job.run_once()
    before = len(sent)
    job2, sent2 = run(w, ESCALATIONS[1:], (A, B), ledger)  # a escalacao do caso 1 fechou
    await job2.run_once()
    revokes = [p for p in sent2 if p.kind == "revoke"]
    assert len(revokes) == 1 and revokes[0].payload.target_kind == "case_grant"
    a_checkpoints = [
        p for p in sent2 if p.kind == "scope_checkpoint" and p.payload.principal_ref == A.principal_ref
    ]
    assert len(a_checkpoints) == 1 and a_checkpoints[0].payload.total_entries == "0"
    assert int(a_checkpoints[0].payload.generation) == 2 and a_checkpoints[0].payload.predecessor_digest
    # B nao mudou: nada novo para B.
    assert not [p for p in sent2 if getattr(p.payload, "principal_ref", None) == B.principal_ref]
    assert int(sent2[0].expected_source_revision) == before


async def test_group_change_moves_visibility_and_membership_bump_reissues_the_grant() -> None:
    w = world()
    ledger = MemoryLedger()
    job, _ = run(w, ESCALATIONS, (A,), ledger)
    await job.run_once()
    moved = grantee("a", GROUPS["enfermagem"], revision=8)
    job2, sent2 = run(w, ESCALATIONS, (moved,), ledger)
    await job2.run_once()
    kinds = [(p.kind, getattr(p.payload, "case_ref", None)) for p in sent2]
    assert ("revoke", None) in kinds
    new = [p for p in sent2 if p.kind == "case_grant"]
    assert [p.payload.case_ref for p in new] == [identity(2).case_ref]
    assert new[0].payload.membership_revision == "8"
    verify_grant(w, new[0], moved, identity(2))
    same_groups = grantee("a", GROUPS["enfermagem"], revision=9)
    job3, sent3 = run(w, ESCALATIONS, (same_groups,), ledger)
    await job3.run_once()
    reissued = [p for p in sent3 if p.kind == "case_grant"]
    assert len(reissued) == 1 and reissued[0].payload.grant_ref == new[0].payload.grant_ref
    assert int(reissued[0].payload.grant_revision) == int(new[0].payload.grant_revision) + 1


async def test_removed_grantee_loses_grants_and_checkpoint() -> None:
    w = world()
    ledger = MemoryLedger()
    job, _ = run(w, ESCALATIONS, (A, B), ledger)
    await job.run_once()
    job2, sent2 = run(w, ESCALATIONS, (B,), ledger)
    await job2.run_once()
    targets = {(p.payload.target_kind, p.payload.target_ref) for p in sent2 if p.kind == "revoke"}
    assert {kind for kind, _ in targets} == {"case_grant", "scope_checkpoint"}
    state = ledger.load()
    assert not [
        g for g in state.grants.values() if g.principal_ref == A.principal_ref and g.state == "active"
    ]
    assert A.principal_ref not in {p for p, c in state.checkpoints.items() if c.active}


def test_plan_after_revocations_is_stable() -> None:
    state = IssuerState.empty()
    assert all(
        not isinstance(a, RevokeGrantAction | RevokeScopeAction)
        for a in plan(
            escalations=ESCALATIONS, grantees=(A,), state=state, policy=world().policy, scope=SCOPE, now=NOW
        ).actions
    )


async def test_failed_publication_stops_the_run_and_is_resent_byte_identical() -> None:
    w = world()
    ledger = MemoryLedger()
    attempts: list[bytes] = []
    fail = {"left": 1}

    async def flaky(raw: bytes) -> dict[str, Any]:
        attempts.append(raw)
        if len(attempts) == 2 and fail["left"]:
            fail["left"] -= 1
            raise StaffCaseError("uncertain")
        publication = parse_model(StaffPublication, strict_loads(raw))
        return {"source_revision": publication.source_revision, "publication_id": publication.publication_id}

    job = StaffCaseIssuerJob(
        issuer=w.issuer(),
        escalations=lambda: ESCALATIONS[:1],
        grantees=lambda: (A,),
        witness=w.witness,
        ledger=ledger,
        publish=flaky,
        clock=lambda: NOW,
    )
    with pytest.raises(StaffCaseError):
        await job.run_once()
    assert ledger.load().pending == attempts[1]
    await job.run_once()
    # A primeira tentativa da proxima rodada e o MESMO pedido, byte a byte (recuperacao do recibo).
    assert attempts[2] == attempts[1]
    assert ledger.load().pending is None


async def test_expired_designation_key_refuses_to_sign() -> None:
    w = world()
    late = replace(w.signer, clock=lambda: NOW + timedelta(days=14))
    issuer = StaffCaseIssuer(signer=late, scope=SCOPE, source_ref=SOURCE, policy=w.policy)
    job = StaffCaseIssuerJob(
        issuer=issuer,
        escalations=lambda: ESCALATIONS,
        grantees=lambda: (A,),
        witness=w.witness,
        ledger=MemoryLedger(),
        publish=lambda raw: None,  # type: ignore[arg-type,return-value]
        clock=lambda: NOW + timedelta(days=14),
    )
    with pytest.raises(StaffCaseError):
        await job.run_once()


def test_issuer_refuses_a_signer_that_is_not_the_designated_case_issuer() -> None:
    w = world()
    wrong = StaffSigner(w.authority, w.witness_private, "identity_verifier", clock=lambda: NOW)
    with pytest.raises(CaseIssuerError):
        StaffCaseIssuer(signer=wrong, scope=SCOPE, source_ref=SOURCE, policy=w.policy)
    with pytest.raises(CaseIssuerError):
        StaffCaseIssuer(signer=w.signer, scope=SCOPE, source_ref="outra-fonte", policy=w.policy)
    with pytest.raises(CaseIssuerError):
        StaffCaseIssuer(
            signer=w.signer,
            scope=SCOPE,
            source_ref=SOURCE,
            policy=StaffCasePolicy(policy_ref="outra", policy_revision=1),
        )


def test_policy_document_names_the_dmn_and_excludes_current_task() -> None:
    document = world().policy.document()
    assert document["decision_source"] == {"decision": "escalation_routing", "output": "grupo_atendimento"}
    assert "staff_current_task.v1" not in canonicalize(document).decode()
    assert world().policy.digest == digest(document)
