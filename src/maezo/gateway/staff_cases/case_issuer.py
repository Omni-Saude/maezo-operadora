"""T1.6 — a fonte de publicacao de casos staff (`case_issuer`).

Plano `docs/plans/portal-autoridade-nativa-dev.md`, Onda 1, T1.6. Decisao N3 do dono (23/09/2026):

    um caso e visivel SO ao grupo que a DMN `escalation_routing` escolheu para a escalacao.

Este modulo NAO roteia. O grupo e o `grupo_atendimento` que a DMN ja decidiu e que o engine gravou
como `candidateGroups` de `UT_TratarEscalonamento` (BPMN SP-OP-ESCALATION-001); ele chega aqui
como dado e e comparado por IGUALDADE EXATA com os grupos da membership revisada do principal
(`portal_memberships`). Nenhuma regra de prioridade/severidade e reimplementada em Python
(AGENTS.md regra 5; ADR-0012).

Camadas:

* dominio puro: `visible_cases` (a regra N3), `StaffGrantee.from_membership`, `plan` (o que
  publicar dado o estado ja publicado) e `apply` (o estado depois de um recibo);
* montagem assinada: `StaffCaseIssuer` produz `staff-case-publication.v1` de `policy_head`,
  `case_grant`, `scope_chunk`, `scope_checkpoint` e `revoke`, assinadas pela chave `case_issuer`
  da designacao instalada;
* orquestracao: `StaffCaseIssuerJob.run_once`, com portas para escalacoes, grantees, witness,
  ledger e transporte (`StaffNativeClient.publish`).

As decisoes fixas sao a constante fechada `DECISIONS`. H3 (D-N) remove a restricao da Onda 7: cada
tarefa CORRENTE da instancia do caso ganha a sua decisao `detail`/`staff_current_task.v1`
(`TASK_DECISION`), com `resource_identity_digest` = digest do `StaffCurrentTaskResource` exato que
`StaffCaseReadCommand.collectDetail` recalcula da linha nativa. O engine so mostra a tarefa se
ela tambem tiver recurso Q2 publicado (H2, `task_publication_source.py`); sem ele a tarefa e
omitida, nunca inventada. A tarefa entra na evidencia do grant: tarefa nova ou concluida reemite.

As tres portas de producao (decisao D-H do plano) moram em `case_issuer_sources.py`:
`AuthClaimAnchor` (D-H.1), `IssuerWitness` (D-H.2) e `PostgresIssuerLedger` (D-H.3). O
`policy_ref` e deterministico por designacao (D-H.6, `policy_ref_for`).
"""

from __future__ import annotations

import hashlib
import inspect
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Any, Literal, Protocol

from maezo.gateway.external_cases.models import Identity, Scope, digest, instant, now_utc, timestamp
from maezo.gateway.human.auth_profile import Actor
from maezo.gateway.human.read_profile import digest as read_digest
from maezo.gateway.human.read_profile import parse_model, wire
from maezo.portal.api.records import MembershipRecord
from maezo.portal.contracts.models import HumanPrincipal, MembershipBinding, SubjectBinding
from maezo.portal.engine.profile import canonicalize, strict_loads

from .authority import fingerprint
from .models import FIELDS, MembershipWitness, StaffCaseError, StaffPublication
from .publisher import StaffSigner

#: As unicas decisoes que o emissor assina. `detail` precisa das duas projecoes completas e `list`
#: do resumo (`InstalledStaffAuthority.grant`, `required`). Tarefa corrente: Onda 8.
DECISIONS: tuple[tuple[Literal["detail", "list"], str], ...] = (
    ("detail", "staff_identity.v1"),
    ("detail", "staff_summary.v1"),
    ("detail", "staff_escalation.v1"),
    ("list", "staff_summary.v1"),
    ("list", "staff_escalation.v1"),
)
#: H3: uma decisao por tarefa corrente do caso (`created_at` incluso: o engine exige o campo nela).
TASK_DECISION: tuple[Literal["detail"], str] = ("detail", "staff_current_task.v1")
ROUTING_DECISION = "escalation_routing"
ROUTING_OUTPUT = "grupo_atendimento"
MAX_CHUNK = 256
MAX_CASE_TASKS = 200


class CaseIssuerError(RuntimeError):
    """Recusa de montagem/plano. Nunca carrega subject, issuer nem payload."""

    def __init__(self, reason: str) -> None:
        super().__init__("staff_case_issuer_" + reason)


#: D-H.6: `policy_ref = "staff-escalation-routing@d{designation_revision}"`.
POLICY_PREFIX = "staff-escalation-routing@d"


def policy_ref_for(designation_revision: int | str) -> str:
    value = str(designation_revision)
    if not value.isdigit() or value != str(int(value)) or int(value) < 1:
        raise CaseIssuerError("invalid_designation_revision")
    return POLICY_PREFIX + value


# ---------------------------------------------------------------------------- dominio puro


@dataclass(frozen=True, order=True)
class CaseTask:
    """Uma tarefa VIVA da instancia do caso (`ACT_RU_TASK` com `PROC_INST_ID_` do caso)."""

    task_id: str
    task_definition_key: str

    def __post_init__(self) -> None:
        if not self.task_id or not self.task_definition_key:
            raise CaseIssuerError("invalid_case_task")


@dataclass(frozen=True)
class RoutedEscalation:
    """Uma escalacao VIVA e o grupo que a DMN escolheu para ela.

    `case` e a identidade do caso staff que a escalacao nomeia. Resolver essa identidade e a
    porta bloqueada (ver docstring do modulo); aqui ela ja chega resolvida.
    """

    tenant: str
    escalation_ref: str
    case: Identity
    grupo_atendimento: str
    #: H3: as tarefas correntes da instancia do CASO (nao da escalacao), ordenadas por id.
    case_tasks: tuple[CaseTask, ...] = ()

    def __post_init__(self) -> None:
        # 256 decisoes por grant no engine (`StaffCaseModels.shape("grant")`), 5 delas fixas.
        if tuple(sorted(set(self.case_tasks))) != self.case_tasks or len(self.case_tasks) > MAX_CASE_TASKS:
            raise CaseIssuerError("invalid_case_tasks")
        if not self.grupo_atendimento or self.grupo_atendimento != self.grupo_atendimento.strip():
            raise CaseIssuerError("invalid_group")
        if not self.escalation_ref or self.case.kind != "authorization":
            raise CaseIssuerError("invalid_escalation")


@dataclass(frozen=True)
class StaffGrantee:
    tenant: str
    principal_ref: str
    issuer: str = field(repr=False)
    subject: str = field(repr=False)
    membership_revision: int
    groups: frozenset[str]
    # O witness nativo compara a membership inteira (`NativeMembershipSource.observe`): o emissor
    # guarda os bindings do registro para montar o principal sem sessao (D-H.2).
    memberships: tuple[MembershipBinding, ...] = field(default=(), compare=False, repr=False)
    subject_bindings: tuple[SubjectBinding, ...] = field(default=(), compare=False, repr=False)

    @classmethod
    def from_membership(cls, record: MembershipRecord, *, tenant: str, now: datetime) -> StaffGrantee | None:
        """So membership staff, do tenant, nao revogada e com revisao vigente vira grantee."""
        if (
            record.tenant != tenant
            or record.audience != "staff"
            or record.revoked
            or not now < record.reviewed_until
        ):
            return None
        groups = frozenset(group for binding in record.memberships for group in binding.groups)
        return cls(
            tenant,
            record.principal_ref,
            record.issuer,
            record.subject,
            record.revision,
            groups,
            tuple(record.memberships),
            tuple(record.subject_bindings),
        )

    def principal(self, *, session_ref: str, authenticated_at: datetime) -> HumanPrincipal:
        """O principal que o witness do emissor observa. Nao e sessao humana: `session_ref` e o da
        rodada (`case-issuer-run:{run_id}`), que o engine nao compara a uma sessao no caminho de
        publicacao (D-H.2)."""
        if not self.memberships:
            raise CaseIssuerError("grantee_without_membership")
        return HumanPrincipal(
            schema_version=1,
            principal_ref=self.principal_ref,
            issuer=self.issuer,
            subject=self.subject,
            tenant=self.tenant,
            membership_revision=self.membership_revision,
            memberships=self.memberships,
            session_ref=session_ref,
            authenticated_at=authenticated_at,
            subject_bindings=self.subject_bindings,
        )

    def actor(self) -> Actor:
        return Actor(
            principal_ref=self.principal_ref,
            issuer=self.issuer,
            subject=self.subject,
            membership_revision=self.membership_revision,
            audience="staff",
        )

    def actor_digest(self) -> str:
        # O mesmo codec do verificador (`authority.actor_digest`): revisao sem inteiro JSON.
        return read_digest(self.actor())


def visible_cases(
    escalations: Iterable[RoutedEscalation], grantee: StaffGrantee
) -> dict[str, tuple[RoutedEscalation, ...]]:
    """A regra N3. Igualdade exata de string: sem prefixo, sem caixa, sem espacos."""
    visible: dict[str, list[RoutedEscalation]] = {}
    for escalation in escalations:
        if escalation.tenant == grantee.tenant and escalation.grupo_atendimento in grantee.groups:
            visible.setdefault(escalation.case.case_ref, []).append(escalation)
    return {
        case_ref: tuple(sorted(found, key=lambda e: e.escalation_ref))
        for case_ref, found in sorted(visible.items())
    }


@dataclass(frozen=True)
class StaffCasePolicy:
    """A politica publicada (`staff_policy_head`). O digest amarra a regra N3 ao head."""

    policy_ref: str
    policy_revision: int

    def document(self) -> dict[str, Any]:
        return dict(
            schema="staff-case-policy.v1",
            policy_ref=self.policy_ref,
            rule="member_of_routed_group",
            decision_source=dict(decision=ROUTING_DECISION, output=ROUTING_OUTPUT),
            match="exact",
            audience="staff",
            decisions=[
                dict(operation=operation, projection=projection, fields=sorted(FIELDS[projection]))
                for operation, projection in (*DECISIONS, TASK_DECISION)
            ],
        )

    @property
    def digest(self) -> str:
        return digest(self.document())


def evidence_digest(case: Identity, escalations: Sequence[RoutedEscalation]) -> str:
    """O que a decisao cita como recibo: o fato de roteamento, sem PHI nem texto livre."""
    return digest(
        dict(
            schema="staff-case-routing-evidence.v1",
            case_ref=case.case_ref,
            identity_digest=digest(case.wire()),
            decision=ROUTING_DECISION,
            routes=[dict(escalation_ref=e.escalation_ref, group=e.grupo_atendimento) for e in escalations],
            # H3: as tarefas correntes fazem parte do fato citado; mudou a tarefa, muda o grant.
            tasks=[
                dict(task_id=t.task_id, task_definition_key=t.task_definition_key)
                for t in case_tasks(escalations)
            ],
        )
    )


def case_tasks(escalations: Sequence[RoutedEscalation]) -> tuple[CaseTask, ...]:
    """As tarefas correntes do caso; escalacoes do MESMO caso tem de concordar (uma leitura so)."""
    found = {e.case_tasks for e in escalations}
    if len(found) > 1:
        raise CaseIssuerError("inconsistent_case_tasks")
    return next(iter(found), ())


def task_resource_digest(scope: Scope, case: Identity, task: CaseTask) -> str:
    """O `StaffCurrentTaskResource` que `collectDetail` recalcula da linha nativa (mesmo JCS)."""
    return digest(
        dict(
            scope=scope.wire(),
            case_ref=case.case_ref,
            process_instance_id=case.process_instance_ref,
            task_id=task.task_id,
            task_definition_key=task.task_definition_key,
        )
    )


def _ref(prefix: str, *parts: str) -> str:
    return prefix + ":" + hashlib.sha256(canonicalize(list(parts))).hexdigest()[:40]


def grant_ref_for(tenant: str, principal_ref: str, case_ref: str) -> str:
    return _ref("grant", tenant, principal_ref, case_ref)


def checkpoint_ref_for(tenant: str, principal_ref: str) -> str:
    return _ref("scope", tenant, principal_ref)


def intent_digest(
    *,
    principal_ref: str,
    membership_revision: int,
    identity: str,
    policy_revision: int,
    policy: str,
    evidence: str,
) -> str:
    return digest(
        dict(
            principal_ref=principal_ref,
            membership_revision=str(membership_revision),
            identity_digest=identity,
            policy_revision=str(policy_revision),
            policy_digest=policy,
            evidence_digest=evidence,
        )
    )


# ---------------------------------------------------------------------------- estado e plano


@dataclass(frozen=True)
class PolicyState:
    policy_revision: int
    head_revision: int
    policy_digest: str
    valid_until: datetime


@dataclass(frozen=True)
class IssuedGrant:
    grant_ref: str
    grant_revision: int
    case_ref: str
    identity_digest: str
    principal_ref: str
    membership_revision: int
    intent_digest: str
    grant_digest: str
    source_revision: int
    valid_until: datetime
    state: Literal["active", "revoked"]


@dataclass(frozen=True)
class CheckpointState:
    checkpoint_ref: str
    generation: int
    digest: str
    membership_revision: int
    policy_revision: int
    grants_key: tuple[tuple[str, str, int], ...]
    valid_until: datetime
    active: bool


@dataclass(frozen=True)
class IssuerState:
    source_revision: int
    policy: PolicyState | None
    grants: Mapping[str, IssuedGrant]
    checkpoints: Mapping[str, CheckpointState]
    pending: bytes | None = None

    @classmethod
    def empty(cls) -> IssuerState:
        return cls(0, None, MappingProxyType({}), MappingProxyType({}), None)


@dataclass(frozen=True)
class PolicyHeadAction:
    policy_revision: int
    head_revision: int
    expected_head_revision: int


@dataclass(frozen=True)
class GrantAction:
    grantee: StaffGrantee
    case: Identity
    evidence: tuple[RoutedEscalation, ...]
    grant_ref: str
    grant_revision: int


@dataclass(frozen=True)
class RevokeGrantAction:
    grant_ref: str
    expected_revision: int
    principal_ref: str


@dataclass(frozen=True)
class ScopeAction:
    grantee: StaffGrantee


@dataclass(frozen=True)
class RevokeScopeAction:
    principal_ref: str
    checkpoint_ref: str
    expected_generation: int


Action = PolicyHeadAction | GrantAction | RevokeGrantAction | ScopeAction | RevokeScopeAction


@dataclass(frozen=True)
class Plan:
    actions: tuple[Action, ...]
    policy_revision: int


def plan(
    *,
    escalations: Iterable[RoutedEscalation],
    grantees: Iterable[StaffGrantee],
    state: IssuerState,
    policy: StaffCasePolicy,
    scope: Scope,
    now: datetime,
    renew_before: timedelta = timedelta(hours=24),
) -> Plan:
    """O que publicar para que o engine reflita EXATAMENTE a regra N3 sobre o estado atual."""
    live = tuple(escalations)
    people = tuple(sorted(grantees, key=lambda g: g.principal_ref))
    # Uma fonte que devolve outro tenant e um defeito da fonte, nao um caso a filtrar em silencio.
    if any(e.tenant != scope.tenant for e in live) or any(g.tenant != scope.tenant for g in people):
        raise CaseIssuerError("foreign_tenant")
    if len({g.principal_ref for g in people}) != len(people):
        raise CaseIssuerError("duplicate_grantee")
    actions: list[Action] = []
    current = state.policy
    renew = (
        current is None or current.policy_digest != policy.digest or current.valid_until - now < renew_before
    )
    if current is None:
        revision = policy.policy_revision
        actions.append(PolicyHeadAction(revision, 1, 0))
    elif renew or current.policy_revision < policy.policy_revision:
        # active -> active exige policy_revision maior (`StaffCaseStore.publishPolicy`); o engine
        # desliga todos os grants dependentes e o plano os reemite abaixo.
        revision = max(policy.policy_revision, current.policy_revision + 1)
        actions.append(PolicyHeadAction(revision, current.head_revision + 1, current.head_revision))
    else:
        revision = current.policy_revision

    desired: dict[str, int] = {}
    touched: set[str] = set()
    for grantee in people:
        for case_ref, evidence in visible_cases(live, grantee).items():
            case = evidence[0].case
            if any(e.case != case for e in evidence):
                raise CaseIssuerError("inconsistent_case_identity")
            ref = grant_ref_for(scope.tenant, grantee.principal_ref, case_ref)
            wanted = intent_digest(
                principal_ref=grantee.principal_ref,
                membership_revision=grantee.membership_revision,
                identity=digest(case.wire()),
                policy_revision=revision,
                policy=policy.digest,
                evidence=evidence_digest(case, evidence),
            )
            issued = state.grants.get(ref)
            if (
                issued is not None
                and issued.state == "active"
                and issued.intent_digest == wanted
                and issued.valid_until - now >= renew_before
            ):
                desired[ref] = issued.grant_revision
                continue
            next_revision = 1 if issued is None else issued.grant_revision + 1
            desired[ref] = next_revision
            touched.add(grantee.principal_ref)
            actions.append(GrantAction(grantee, case, evidence, ref, next_revision))
    for ref, issued in sorted(state.grants.items()):
        if issued.state == "active" and ref not in desired:
            touched.add(issued.principal_ref)
            actions.append(RevokeGrantAction(ref, issued.grant_revision, issued.principal_ref))

    people_refs = {g.principal_ref for g in people}
    tail: list[Action] = []
    for grantee in people:
        checkpoint = state.checkpoints.get(grantee.principal_ref)
        wanted_key = tuple(
            sorted(
                (ref_case, ref, desired[ref])
                for ref, ref_case in (
                    (grant_ref_for(scope.tenant, grantee.principal_ref, c), c)
                    for c in visible_cases(live, grantee)
                )
            )
        )
        if (
            checkpoint is not None
            and checkpoint.active
            and checkpoint.membership_revision != grantee.membership_revision
        ):
            # A sucessao do engine e por revisao de membership: a antiga nao tem sucessor, entao
            # e retirada explicitamente antes da nova nascer (sem predecessor).
            tail.append(
                RevokeScopeAction(grantee.principal_ref, checkpoint.checkpoint_ref, checkpoint.generation)
            )
        if (
            checkpoint is None
            or not checkpoint.active
            or grantee.principal_ref in touched
            or checkpoint.membership_revision != grantee.membership_revision
            or checkpoint.policy_revision != revision
            or checkpoint.grants_key != wanted_key
            or checkpoint.valid_until - now < renew_before
        ):
            tail.append(ScopeAction(grantee))
    for principal_ref, checkpoint in sorted(state.checkpoints.items()):
        if checkpoint.active and principal_ref not in people_refs:
            tail.append(RevokeScopeAction(principal_ref, checkpoint.checkpoint_ref, checkpoint.generation))
    return Plan(tuple(actions + tail), revision)


def apply(state: IssuerState, publication: StaffPublication) -> IssuerState:
    """O estado do emissor depois que o engine devolveu recibo desta publicacao."""
    source_revision = int(publication.source_revision)
    if source_revision != state.source_revision + 1:
        raise CaseIssuerError("source_revision_gap")
    grants, checkpoints = dict(state.grants), dict(state.checkpoints)
    policy = state.policy
    payload: Any = publication.payload
    if publication.kind == "policy_head":
        policy = PolicyState(
            int(payload.policy_revision),
            int(payload.head_revision),
            payload.policy_digest,
            timestamp(payload.valid_until),
        )
    elif publication.kind == "case_grant":
        decision = payload.decisions[0]
        grants[payload.grant_ref] = IssuedGrant(
            grant_ref=payload.grant_ref,
            grant_revision=int(payload.grant_revision),
            case_ref=payload.case_ref,
            identity_digest=payload.identity_digest,
            principal_ref=payload.principal_ref,
            membership_revision=int(payload.membership_revision),
            intent_digest=intent_digest(
                principal_ref=payload.principal_ref,
                membership_revision=int(payload.membership_revision),
                identity=payload.identity_digest,
                policy_revision=int(decision.policy_revision),
                policy=decision.policy_digest,
                evidence=decision.receipt_digest,
            ),
            grant_digest=digest(payload.wire()),
            source_revision=source_revision,
            valid_until=timestamp(payload.valid_until),
            state="active",
        )
    elif publication.kind == "revoke" and payload.target_kind == "case_grant":
        old = grants[payload.target_ref]
        grants[payload.target_ref] = replace(old, state="revoked", grant_revision=old.grant_revision + 1)
    elif publication.kind == "revoke":
        principal = next(p for p, c in checkpoints.items() if c.checkpoint_ref == payload.target_ref)
        checkpoints[principal] = replace(checkpoints[principal], active=False)
    elif publication.kind == "scope_checkpoint":
        key = tuple(
            sorted(
                (g.case_ref, g.grant_ref, g.grant_revision)
                for g in grants.values()
                if g.principal_ref == payload.principal_ref and g.state == "active"
            )
        )
        checkpoints[payload.principal_ref] = CheckpointState(
            checkpoint_ref=payload.checkpoint_ref,
            generation=int(payload.generation),
            digest=digest(payload.wire()),
            membership_revision=int(payload.membership_revision),
            policy_revision=int(payload.policy_scope_revision),
            grants_key=key,
            valid_until=timestamp(payload.valid_until),
            active=True,
        )
    return IssuerState(
        source_revision, policy, MappingProxyType(grants), MappingProxyType(checkpoints), state.pending
    )


# ---------------------------------------------------------------------------- montagem assinada


class StaffCaseIssuer:
    """Monta e assina as publicacoes com a chave `case_issuer` da designacao instalada."""

    def __init__(self, *, signer: StaffSigner, scope: Scope, source_ref: str, policy: StaffCasePolicy):
        entry = signer.authority.entries.get(fingerprint(signer.key.public_key()))
        projections = {projection for _, projection in (*DECISIONS, TASK_DECISION)}
        if (
            signer.role != "case_issuer"
            or entry is None
            or entry.role != "case_issuer"
            or signer.authority.designation.scope != scope
            or entry.source_ref != source_ref
            # O engine resolve o emissor da decisao por `namespace = policy_ref`.
            or entry.source_namespace != policy.policy_ref
            or not projections <= set(entry.projections)
            or not {"detail", "list"} <= set(entry.operations)
        ):
            raise CaseIssuerError("not_the_designated_case_issuer")
        self.signer, self.scope, self.source_ref, self.policy = signer, scope, source_ref, policy
        self.key_fingerprint = entry.key_fingerprint

    def _signed(
        self, value: dict[str, Any], purpose: str, until: datetime, name: str = "proof"
    ) -> dict[str, Any]:
        result = dict(value)
        result[name] = wire(self.signer.proof(value, purpose, until))
        return result

    def _until(self, purpose: str, ceiling: datetime | None = None) -> datetime:
        until = self.signer.until(purpose)
        return until if ceiling is None else min(until, ceiling)

    def _publication(
        self,
        *,
        kind: str,
        payload: dict[str, Any],
        purpose: str,
        source_revision: int,
        witness: MembershipWitness | None,
        until: datetime,
    ) -> StaffPublication:
        now = self.signer.guard(purpose)
        value: dict[str, Any] = dict(
            schema="staff-case-publication.v1",
            scope=self.scope.wire(),
            publication_id=f"{self.source_ref}@{source_revision}",
            expected_source_revision=str(source_revision - 1),
            source_ref=self.source_ref,
            source_revision=str(source_revision),
            kind=kind,
            membership_witness=None if witness is None else wire(witness),
            payload=payload,
            payload_digest=digest(payload),
            observed_at=instant(now),
            valid_until=instant(until),
        )
        return parse_model(StaffPublication, self._signed(value, purpose, until))

    def policy_head(self, action: PolicyHeadAction, *, source_revision: int) -> StaffPublication:
        purpose = "staff_policy_head"
        now, until = self.signer.guard(purpose), self._until(purpose)
        head = dict(
            schema="staff-policy-head.v1",
            scope=self.scope.wire(),
            policy_ref=self.policy.policy_ref,
            policy_revision=str(action.policy_revision),
            policy_digest=self.policy.digest,
            head_revision=str(action.head_revision),
            expected_head_revision=str(action.expected_head_revision),
            state="active",
            decision_issuer_key_fingerprint=self.key_fingerprint,
            decision_purpose="staff_case_grant",
            source_ref=self.source_ref,
            source_revision=str(source_revision),
            observed_at=instant(now),
            valid_until=instant(until),
        )
        return self._publication(
            kind="policy_head",
            payload=self._signed(head, purpose, until),
            purpose=purpose,
            source_revision=source_revision,
            witness=None,
            until=until,
        )

    def grant(
        self,
        action: GrantAction,
        *,
        witness: MembershipWitness,
        policy: PolicyState,
        source_revision: int,
    ) -> StaffPublication:
        purpose = "staff_case_grant"
        grantee = action.grantee
        if witness.actor != grantee.actor() or witness.scope != self.scope:
            raise CaseIssuerError("witness_of_another_principal")
        now, until = self.signer.guard(purpose), self._until(purpose, policy.valid_until)
        identity = digest(action.case.wire())
        receipt = evidence_digest(action.case, action.evidence)
        decisions = []
        for operation, projection in DECISIONS:
            decision = dict(
                decision_ref=f"{operation}/{projection}",
                policy_ref=self.policy.policy_ref,
                policy_revision=str(policy.policy_revision),
                policy_digest=policy.policy_digest,
                subject_identity_digest=grantee.actor_digest(),
                membership_revision=str(grantee.membership_revision),
                resource_identity_digest=identity,
                operation=operation,
                projection=projection,
                fields=sorted(FIELDS[projection]),
                receipt_ref=_ref("routing", self.scope.tenant, action.case.case_ref),
                receipt_digest=receipt,
                observed_at=instant(now),
                valid_until=instant(until),
                state="active",
            )
            decisions.append(self._signed(decision, purpose, until, "decision_proof"))
        operation, projection = TASK_DECISION
        for task in case_tasks(action.evidence):
            decision = dict(
                decision_ref=f"{operation}/{projection}/{task.task_id}",
                policy_ref=self.policy.policy_ref,
                policy_revision=str(policy.policy_revision),
                policy_digest=policy.policy_digest,
                subject_identity_digest=grantee.actor_digest(),
                membership_revision=str(grantee.membership_revision),
                resource_identity_digest=task_resource_digest(self.scope, action.case, task),
                operation=operation,
                projection=projection,
                fields=sorted(FIELDS[projection]),
                receipt_ref=_ref("routing", self.scope.tenant, action.case.case_ref),
                receipt_digest=receipt,
                observed_at=instant(now),
                valid_until=instant(until),
                state="active",
            )
            decisions.append(self._signed(decision, purpose, until, "decision_proof"))
        payload = dict(
            grant_ref=action.grant_ref,
            scope=self.scope.wire(),
            case_ref=action.case.case_ref,
            identity_digest=identity,
            issuer=grantee.issuer,
            subject=grantee.subject,
            principal_ref=grantee.principal_ref,
            membership_revision=str(grantee.membership_revision),
            audience="staff",
            grant_revision=str(action.grant_revision),
            source_ref=self.source_ref,
            source_revision=str(source_revision),
            decisions=decisions,
            observed_at=instant(now),
            valid_until=instant(until),
            state="active",
        )
        return self._publication(
            kind="case_grant",
            payload=payload,
            purpose=purpose,
            source_revision=source_revision,
            witness=witness,
            until=until,
        )

    def revoke(
        self,
        *,
        target_kind: Literal["case_grant", "scope_checkpoint"],
        target_ref: str,
        expected: int,
        source_revision: int,
    ) -> StaffPublication:
        purpose = "scope_complete" if target_kind == "scope_checkpoint" else "staff_case_grant"
        payload = dict(target_kind=target_kind, target_ref=target_ref, expected_revision=str(expected))
        return self._publication(
            kind="revoke",
            payload=payload,
            purpose=purpose,
            source_revision=source_revision,
            witness=None,
            until=self._until(purpose),
        )

    def scope_publications(
        self,
        grantee: StaffGrantee,
        *,
        witness: MembershipWitness,
        grants: Sequence[IssuedGrant],
        policy: PolicyState,
        previous: CheckpointState | None,
        source_revision: int,
    ) -> list[StaffPublication]:
        """Chunks (ate 256 entradas cada) e o checkpoint `complete` que os pina, nessa ordem."""
        purpose = "scope_complete"
        if witness.actor != grantee.actor() or witness.scope != self.scope:
            raise CaseIssuerError("witness_of_another_principal")
        now, until = self.signer.guard(purpose), self._until(purpose, policy.valid_until)
        checkpoint_ref = checkpoint_ref_for(self.scope.tenant, grantee.principal_ref)
        generation = 1 if previous is None else previous.generation + 1
        successor = (
            previous is not None
            and previous.active
            and previous.membership_revision == grantee.membership_revision
        )
        entries = sorted(
            (
                dict(
                    case_ref=g.case_ref,
                    identity_digest=g.identity_digest,
                    grant_ref=g.grant_ref,
                    grant_revision=str(g.grant_revision),
                    grant_digest=g.grant_digest,
                    source_ref=self.source_ref,
                    source_revision=str(g.source_revision),
                )
                for g in grants
            ),
            key=lambda e: (e["case_ref"], e["grant_ref"]),
        )
        if len({e["case_ref"] for e in entries}) != len(entries):
            raise CaseIssuerError("duplicate_case_in_scope")
        publications: list[StaffPublication] = []
        pins = []
        for index in range(0, len(entries), MAX_CHUNK):
            chunk = dict(
                checkpoint_ref=checkpoint_ref,
                generation=str(generation),
                chunk_index=str(index // MAX_CHUNK),
                entries=entries[index : index + MAX_CHUNK],
            )
            pins.append(
                dict(
                    chunk_index=chunk["chunk_index"],
                    chunk_digest=digest(chunk),
                    entry_count=str(len(chunk["entries"])),
                )
            )
            publications.append(
                self._publication(
                    kind="scope_chunk",
                    payload=chunk,
                    purpose=purpose,
                    source_revision=source_revision + len(publications),
                    witness=witness,
                    until=until,
                )
            )
        checkpoint = dict(
            scope=self.scope.wire(),
            checkpoint_ref=checkpoint_ref,
            generation=str(generation),
            predecessor_digest=previous.digest if successor and previous is not None else None,
            principal_identity_digest=grantee.actor_digest(),
            issuer=grantee.issuer,
            subject=grantee.subject,
            principal_ref=grantee.principal_ref,
            membership_revision=str(grantee.membership_revision),
            kind="authorization",
            operations=["list"],
            chunks=pins,
            total_entries=str(len(entries)),
            policy_scope_ref=self.policy.policy_ref,
            policy_scope_revision=str(policy.policy_revision),
            policy_scope_digest=policy.policy_digest,
            observed_at=instant(now),
            valid_until=instant(until),
            coverage="complete",
        )
        publications.append(
            self._publication(
                kind="scope_checkpoint",
                payload=self._signed(checkpoint, purpose, until),
                purpose=purpose,
                source_revision=source_revision + len(publications),
                witness=witness,
                until=until,
            )
        )
        return publications


# ---------------------------------------------------------------------------- portas e job


class IssuerLedger(Protocol):
    """Estado duravel do emissor (`MemoryLedger` ou `PostgresIssuerLedger`, sync ou async)."""

    def load(self) -> IssuerState | Awaitable[IssuerState]: ...

    def begin(self, raw: bytes) -> None | Awaitable[None]: ...

    def commit(self, publication: StaffPublication) -> IssuerState | Awaitable[IssuerState]: ...


async def _maybe(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def encode_state(state: IssuerState) -> bytes:
    """Estado emitido (sem o pedido pendente, que tem coluna propria) em JSON canonico."""
    policy = state.policy
    return canonicalize(
        dict(
            schema="staff-case-issuer-state.v1",
            source_revision=str(state.source_revision),
            policy=None
            if policy is None
            else dict(
                policy_revision=str(policy.policy_revision),
                head_revision=str(policy.head_revision),
                policy_digest=policy.policy_digest,
                valid_until=instant(policy.valid_until),
            ),
            grants=[
                dict(
                    grant_ref=g.grant_ref,
                    grant_revision=str(g.grant_revision),
                    case_ref=g.case_ref,
                    identity_digest=g.identity_digest,
                    principal_ref=g.principal_ref,
                    membership_revision=str(g.membership_revision),
                    intent_digest=g.intent_digest,
                    grant_digest=g.grant_digest,
                    source_revision=str(g.source_revision),
                    valid_until=instant(g.valid_until),
                    state=g.state,
                )
                for _, g in sorted(state.grants.items())
            ],
            checkpoints=[
                dict(
                    principal_ref=p,
                    checkpoint_ref=c.checkpoint_ref,
                    generation=str(c.generation),
                    digest=c.digest,
                    membership_revision=str(c.membership_revision),
                    policy_revision=str(c.policy_revision),
                    grants_key=[[a, b, str(n)] for a, b, n in c.grants_key],
                    valid_until=instant(c.valid_until),
                    active=c.active,
                )
                for p, c in sorted(state.checkpoints.items())
            ],
        )
    )


_GRANT_KEYS = frozenset(
    [
        "grant_ref",
        "grant_revision",
        "case_ref",
        "identity_digest",
        "principal_ref",
        "membership_revision",
        "intent_digest",
        "grant_digest",
        "source_revision",
        "valid_until",
        "state",
    ]
)
_CHECKPOINT_KEYS = frozenset(
    [
        "principal_ref",
        "checkpoint_ref",
        "generation",
        "digest",
        "membership_revision",
        "policy_revision",
        "grants_key",
        "valid_until",
        "active",
    ]
)


def _natural(value: object) -> int:
    if type(value) is not str or not value.isdigit() or value != str(int(value)):
        raise ValueError
    return int(value)


def decode_state(raw: bytes, pending: bytes | None) -> IssuerState:
    """O inverso exato de `encode_state`; qualquer desvio recusa (ledger incoerente nao vira plano)."""
    try:
        doc = strict_loads(raw)
        if (
            canonicalize(doc) != raw
            or set(doc) != {"schema", "source_revision", "policy", "grants", "checkpoints"}
            or doc["schema"] != "staff-case-issuer-state.v1"
        ):
            raise ValueError
        n = _natural
        policy = None
        if doc["policy"] is not None:
            p = doc["policy"]
            if set(p) != {"policy_revision", "head_revision", "policy_digest", "valid_until"}:
                raise ValueError
            policy = PolicyState(
                n(p["policy_revision"]),
                n(p["head_revision"]),
                p["policy_digest"],
                timestamp(p["valid_until"]),
            )
        grants: dict[str, IssuedGrant] = {}
        for g in doc["grants"]:
            if set(g) != _GRANT_KEYS or g["state"] not in ("active", "revoked") or g["grant_ref"] in grants:
                raise ValueError
            grants[g["grant_ref"]] = IssuedGrant(
                grant_ref=g["grant_ref"],
                grant_revision=n(g["grant_revision"]),
                case_ref=g["case_ref"],
                identity_digest=g["identity_digest"],
                principal_ref=g["principal_ref"],
                membership_revision=n(g["membership_revision"]),
                intent_digest=g["intent_digest"],
                grant_digest=g["grant_digest"],
                source_revision=n(g["source_revision"]),
                valid_until=timestamp(g["valid_until"]),
                state=g["state"],
            )
        checkpoints: dict[str, CheckpointState] = {}
        for c in doc["checkpoints"]:
            if (
                set(c) != _CHECKPOINT_KEYS
                or type(c["active"]) is not bool
                or c["principal_ref"] in checkpoints
            ):
                raise ValueError
            checkpoints[c["principal_ref"]] = CheckpointState(
                checkpoint_ref=c["checkpoint_ref"],
                generation=n(c["generation"]),
                digest=c["digest"],
                membership_revision=n(c["membership_revision"]),
                policy_revision=n(c["policy_revision"]),
                grants_key=tuple((str(a), str(b), n(r)) for a, b, r in c["grants_key"]),
                valid_until=timestamp(c["valid_until"]),
                active=c["active"],
            )
        return IssuerState(
            n(doc["source_revision"]),
            policy,
            MappingProxyType(grants),
            MappingProxyType(checkpoints),
            pending,
        )
    except Exception:
        raise CaseIssuerError("ledger_state_invalid") from None


class MemoryLedger:
    """Ledger em memoria: so para testes e para o modo sem efeito. Nao e duravel."""

    def __init__(self) -> None:
        self._state = IssuerState.empty()

    def load(self) -> IssuerState:
        return self._state

    def begin(self, raw: bytes) -> None:
        if self._state.pending is not None and self._state.pending != raw:
            raise CaseIssuerError("pending_publication")
        self._state = replace(self._state, pending=raw)

    def commit(self, publication: StaffPublication) -> IssuerState:
        self._state = replace(apply(self._state, publication), pending=None)
        return self._state


Publish = Callable[[bytes], Awaitable[Mapping[str, Any]]]
Source = Callable[[], Iterable[Any] | Awaitable[Iterable[Any]]]


async def _collect(source: Source) -> tuple[Any, ...]:
    value = source()
    if inspect.isawaitable(value):
        value = await value
    return tuple(value)


@dataclass(frozen=True)
class RunReport:
    published: int
    grants: int
    revokes: int
    checkpoints: int


class StaffCaseIssuerJob:
    """Uma rodada: le as fontes, planeja e publica em sequencia CAS. Para no primeiro erro."""

    def __init__(
        self,
        *,
        issuer: StaffCaseIssuer,
        escalations: Source,
        grantees: Source,
        witness: Callable[[StaffGrantee], MembershipWitness | Awaitable[MembershipWitness]],
        ledger: IssuerLedger,
        publish: Publish,
        clock: Callable[[], datetime] = now_utc,
    ):
        self.issuer, self.escalations, self.grantees = issuer, escalations, grantees
        self.witness, self.ledger, self.publish, self.clock = witness, ledger, publish, clock

    async def _witness(self, grantee: StaffGrantee) -> MembershipWitness:
        value = self.witness(grantee)
        if inspect.isawaitable(value):
            value = await value
        return value

    async def _send(self, publication: StaffPublication) -> IssuerState:
        raw = canonicalize(publication.wire())
        await _maybe(self.ledger.begin(raw))
        receipt = await self.publish(raw)
        if (
            receipt.get("publication_id") != publication.publication_id
            or receipt.get("source_revision") != publication.source_revision
        ):
            raise StaffCaseError("uncertain")
        state: IssuerState = await _maybe(self.ledger.commit(publication))
        return state

    async def run_once(self) -> RunReport:
        state = await _maybe(self.ledger.load())
        if state.pending is not None:
            # Recuperacao: o MESMO pedido, byte a byte. O engine devolve o recibo tecnico se ja
            # tinha efetivado e nao reaplica admissao (`StaffCasePublicationCommand`).
            pending = parse_model(StaffPublication, strict_loads(state.pending))
            receipt = await self.publish(state.pending)
            if receipt.get("publication_id") != pending.publication_id:
                raise StaffCaseError("uncertain")
            state = await _maybe(self.ledger.commit(pending))
        escalations = await _collect(self.escalations)
        grantees = await _collect(self.grantees)
        result = plan(
            escalations=escalations,
            grantees=grantees,
            state=state,
            policy=self.issuer.policy,
            scope=self.issuer.scope,
            now=self.clock(),
        )
        counts = {"grants": 0, "revokes": 0, "checkpoints": 0, "published": 0}
        for action in result.actions:
            state = await _maybe(self.ledger.load())
            next_revision = state.source_revision + 1
            publications: list[StaffPublication]
            if isinstance(action, PolicyHeadAction):
                publications = [self.issuer.policy_head(action, source_revision=next_revision)]
            elif isinstance(action, GrantAction):
                if state.policy is None:
                    raise CaseIssuerError("policy_not_published")
                publications = [
                    self.issuer.grant(
                        action,
                        witness=await self._witness(action.grantee),
                        policy=state.policy,
                        source_revision=next_revision,
                    )
                ]
                counts["grants"] += 1
            elif isinstance(action, RevokeGrantAction):
                publications = [
                    self.issuer.revoke(
                        target_kind="case_grant",
                        target_ref=action.grant_ref,
                        expected=action.expected_revision,
                        source_revision=next_revision,
                    )
                ]
                counts["revokes"] += 1
            elif isinstance(action, RevokeScopeAction):
                publications = [
                    self.issuer.revoke(
                        target_kind="scope_checkpoint",
                        target_ref=action.checkpoint_ref,
                        expected=action.expected_generation,
                        source_revision=next_revision,
                    )
                ]
                counts["revokes"] += 1
            else:
                if state.policy is None:
                    raise CaseIssuerError("policy_not_published")
                grants = [
                    g
                    for g in state.grants.values()
                    if g.principal_ref == action.grantee.principal_ref and g.state == "active"
                ]
                publications = self.issuer.scope_publications(
                    action.grantee,
                    witness=await self._witness(action.grantee),
                    grants=grants,
                    policy=state.policy,
                    previous=state.checkpoints.get(action.grantee.principal_ref),
                    source_revision=next_revision,
                )
                counts["checkpoints"] += 1
            for publication in publications:
                await self._send(publication)
                counts["published"] += 1
        return RunReport(**counts)
