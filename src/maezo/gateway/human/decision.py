"""ADR0049 D3: complete human basis in PHI, closed classified decision in General.

The ports are trusted composition boundaries, not browser assertions of qualification.
This carrier is NOT the ACK-only human-command.v1 engine wire. Its consumer must qualify
catalog, PHI basis resolution, durable admission and engine enforcement before activation.
Contract trace: AUTH output basis; ESC outputs and PHI resume; PAGTO admissibility binding.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from maezo.portal.contracts.models import (
    AuthDecisionInputs,
    AuthJuntaInputs,
    AuthPendingInputs,
    EscalationDecisionInputs,
    FormKey,
    HumanPrincipal,
    OpaqueRef,
    PagtoAdmissibilityInputs,
    PositiveVersion,
    Revision,
    SchemaVersion,
    Sha256Digest,
    TaskDecision,
    TaskSnapshot,
)
from maezo.portal.engine.profile import canonicalize

from .models import AuthoritativeTask, Closed, CurrentTaskAuthority, PendingAdmission, Scope, Timed
from .projection import decimal_revision, intent_reference

ClassifiedReference = Annotated[
    str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,254}$")
]


def _canonical(value: dict[str, Any]) -> bytes:
    def exact(item: Any) -> Any:
        if type(item) is int:
            return decimal_revision(item)
        if type(item) is dict:
            return {key: exact(val) for key, val in item.items()}
        if type(item) in (list, tuple):
            return [exact(val) for val in item]
        return item

    return canonicalize(exact(value))


class DecisionTarget(Closed):
    schema_version: SchemaVersion
    command_id: OpaqueRef
    task_id: OpaqueRef
    process_definition_key: OpaqueRef
    process_definition_version: PositiveVersion
    process_definition_id: OpaqueRef
    process_definition_digest: Sha256Digest
    task_definition_key: OpaqueRef
    form_key: FormKey
    form_version: PositiveVersion
    form_digest: Sha256Digest
    expected_task_revision: Revision
    expected_evidence_revision: Revision
    expected_evidence_digest: Sha256Digest
    expected_membership_revision: Revision
    expected_authority_revision: Revision


class QualifiedDecisionBinding(Timed):
    """Authenticated source result; equality/freshness checks do not qualify its producer.

    binding_digest pins the reviewed classification, engine mapping and PHI consumer
    contract. The producer must refuse unresolved qualification, including DRAFT forms.
    No boolean declaration here can qualify a catalog or dissolve PHI boundaries.
    """

    scope: Scope
    principal: HumanPrincipal = Field(repr=False)
    task: AuthoritativeTask = Field(repr=False)
    authority: CurrentTaskAuthority = Field(repr=False)
    binding_digest: Sha256Digest
    evidence_ref: ClassifiedReference


class DecisionContext(Timed):
    """Separate mutation context; Q1 read projections retain their empty actions."""

    schema_version: SchemaVersion
    snapshot: TaskSnapshot = Field(repr=False)
    expected_membership_revision: Revision
    expected_authority_revision: Revision
    binding_digest: Sha256Digest


class AuthorizedDecision(Closed):
    """PHI-only custody request, including the complete unmodified validated inputs."""

    scope: Scope
    principal: HumanPrincipal = Field(repr=False)
    decision: TaskDecision = Field(repr=False)
    authority_revision: Revision
    binding_digest: Sha256Digest
    evidence_ref: ClassifiedReference

    @property
    def target(self) -> DecisionTarget:
        return DecisionTarget(
            **self.decision.model_dump(exclude={"inputs"}),
            expected_authority_revision=self.authority_revision,
        )

    @property
    def canonical(self) -> bytes:
        # Exclude mutable membership lists and observation times; retain authenticated
        # immutable identity and exact expected revision. Retry identity remains stable.
        return _canonical(
            {
                "scope": self.scope.model_dump(),
                "principal_ref": self.principal.principal_ref,
                "principal_issuer": self.principal.issuer,
                "principal_subject": self.principal.subject,
                "target": self.target.model_dump(),
                "binding_digest": self.binding_digest,
                "evidence_ref": self.evidence_ref,
                "inputs": self.decision.inputs.model_dump(),
            }
        )

    @property
    def request_digest(self) -> str:
        return hashlib.sha256(self.canonical).hexdigest()

    @property
    def content_digest(self) -> str:
        return hashlib.sha256(_canonical(self.decision.inputs.model_dump())).hexdigest()


class DecisionCustodyRecord(Timed):
    """Confirmed immutable PHI bytes, not a claim that a reference is clinical basis.

    valid_until limits current assurance; it is NOT retention or deletion policy.
    """

    scope: Scope
    principal_ref: OpaqueRef
    task_id: OpaqueRef
    command_id: OpaqueRef
    request_digest: Sha256Digest
    custody_ref: ClassifiedReference
    content_digest: Sha256Digest


class HumanBasis(Closed):
    custody_ref: ClassifiedReference
    content_digest: Sha256Digest


class AuthOutcome(Closed):
    kind: Literal["auth_decisao"]
    decisao_auditor: Literal["APROVAR", "NEGAR", "SOLICITAR_INFO", "JUNTA_MEDICA"]


class JuntaOutcome(Closed):
    kind: Literal["auth_junta"]
    decisao_auditor: Literal["APROVAR", "NEGAR"]


class PendingOutcome(Closed):
    kind: Literal["auth_pendencia"]
    decisao_pendencia: Literal["cancelar_guia", "conceder_prazo_extra", "seguir_analise"]


class EscalationOutcome(Closed):
    kind: Literal["escalation"]
    resultado: Literal["resolvido_humano", "devolvido_agente", "emergencia_acionada"]


class PagtoOutcome(Closed):
    kind: Literal["pagto_admissibilidade"]
    decisao_admissibilidade: Literal["PROSSEGUIR", "DEVOLVER"]


Outcome = Annotated[
    AuthOutcome | JuntaOutcome | PendingOutcome | EscalationOutcome | PagtoOutcome,
    Field(discriminator="kind"),
]


class ClassifiedDecision(Closed):
    """Closed General carrier. No raw clinical names, narratives or variables map.

    human_basis is a separate typed carrier. D5 and PHI consumers must bind and resolve
    its actual contents; never put its reference strings in required clinical fields.
    """

    schema_: Literal["human-classified-decision.v1"] = Field(alias="schema")
    scope: Scope
    principal_ref: OpaqueRef
    principal_issuer: str = Field(repr=False)
    principal_subject: OpaqueRef = Field(repr=False)
    target: DecisionTarget
    binding_digest: Sha256Digest
    evidence_ref: ClassifiedReference
    request_digest: Sha256Digest
    audit_intent_ref: OpaqueRef
    outcome: Outcome
    human_basis: HumanBasis

    @model_validator(mode="after")
    def _closed_projection(self) -> Self:
        if (
            self.target.form_key != self.outcome.kind
            or self.principal_ref == self.scope.workload_ref
            or self.audit_intent_ref
            != intent_reference(self.scope, self.target.task_id, self.target.command_id)
        ):
            raise ValueError("classified decision binding mismatch")
        return self

    @property
    def canonical(self) -> bytes:
        return _canonical(self.model_dump(by_alias=True))

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.canonical).hexdigest()


class PendingDecisionAdmission(PendingAdmission):
    payload_digest: Sha256Digest
    request_digest: Sha256Digest


def project_decision(request: AuthorizedDecision, record: DecisionCustodyRecord) -> ClassifiedDecision:
    """Local projection after confirmed PHI custody; never summarizes/removes human basis."""
    request = AuthorizedDecision.model_validate(request)
    record = DecisionCustodyRecord.model_validate(record)
    if (
        request.scope.tenant != request.principal.tenant
        or request.scope.workload_ref == request.principal.principal_ref
        or request.decision.expected_membership_revision != request.principal.membership_revision
        or record.scope != request.scope
        or record.principal_ref != request.principal.principal_ref
        or record.task_id != request.decision.task_id
        or record.command_id != request.decision.command_id
        or record.request_digest != request.request_digest
        or record.content_digest != request.content_digest
    ):
        raise ValueError("decision custody mismatch")
    inputs = request.decision.inputs
    outcome: Outcome
    if isinstance(inputs, AuthDecisionInputs):
        outcome = AuthOutcome(kind=inputs.kind, decisao_auditor=inputs.decisao_auditor)
    elif isinstance(inputs, AuthJuntaInputs):
        outcome = JuntaOutcome(kind=inputs.kind, decisao_auditor=inputs.decisao_auditor)
    elif isinstance(inputs, AuthPendingInputs):
        outcome = PendingOutcome(kind=inputs.kind, decisao_pendencia=inputs.decisao_pendencia)
    elif isinstance(inputs, EscalationDecisionInputs):
        outcome = EscalationOutcome(kind=inputs.kind, resultado=inputs.resultado)
    elif isinstance(inputs, PagtoAdmissibilityInputs):
        outcome = PagtoOutcome(kind=inputs.kind, decisao_admissibilidade=inputs.decisao_admissibilidade)
    else:
        raise ValueError("decision binding unavailable")
    return ClassifiedDecision(
        schema="human-classified-decision.v1",
        scope=request.scope,
        principal_ref=request.principal.principal_ref,
        principal_issuer=request.principal.issuer,
        principal_subject=request.principal.subject,
        target=request.target,
        binding_digest=request.binding_digest,
        evidence_ref=request.evidence_ref,
        request_digest=request.request_digest,
        audit_intent_ref=intent_reference(request.scope, record.task_id, record.command_id),
        outcome=outcome,
        human_basis=HumanBasis(custody_ref=record.custody_ref, content_digest=record.content_digest),
    )


class DecisionBindingSource(ABC):
    scope: Scope

    @abstractmethod
    async def qualify(
        self, principal: HumanPrincipal, task: AuthoritativeTask, authority: CurrentTaskAuthority
    ) -> QualifiedDecisionBinding:
        """Authenticate current catalog/PHI-consumer qualification for exact deployment pins."""
        raise NotImplementedError


class HumanDecisionCustody(ABC):
    scope: Scope

    @abstractmethod
    async def preserve(self, request: AuthorizedDecision) -> DecisionCustodyRecord:
        """Durably preserve full form in PHI; exact retries keep reference, conflicts refuse.

        Scope/task/command + request_digest must be unique. Uncertain commit raises;
        retry the same identity and recover existing bytes. No General dispatch here.
        """
        raise NotImplementedError


class DecisionAdmission(ABC):
    scope: Scope

    @abstractmethod
    async def admit(self, command: ClassifiedDecision, *, valid_until: datetime) -> PendingDecisionAdmission:
        """Commit immutable intent/outbox atomically before deadline, otherwise raise.

        Dedicated D6 adapter enforces digest idempotency and principal binding. Unknown
        commit raises safely and is reconciled by SAME command, never called rollback.
        It MUST NOT dispatch before durable intent; no engine network call in this port.
        """
        raise NotImplementedError


@dataclass(frozen=True)
class BoundDecisionPorts:
    binding: DecisionBindingSource
    custody: HumanDecisionCustody
    admission: DecisionAdmission
