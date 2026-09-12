"""Closed W6A source/checkpoint/publication records; no numeric JSON or inferred grants.

Authority: independently reviewed external-case-checkpoint-contract-repair, §§1–16.
These records validate shape only. Crypto, installed currentness and native facts
remain mandatory. Q2's closed models and callback API are deliberately untouched.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from maezo.portal.engine.profile import canonicalize, strict_loads

Ref = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,254}$")]
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
CaseRef = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{16,128}$")]
Revision = Annotated[str, StringConstraints(pattern=r"^(0|[1-9][0-9]*)$")]
Kind = Literal["authorization", "reimbursement", "account"]
Audience = Literal["beneficiary", "provider"]
FIELDS = frozenset({"case_ref", "kind", "state", "record_revision", "state_observed_at"})
# BPMN SP-OP-AUTH-001: apenas estes dois desfechos declaram `numero_autorizacao` em
# `event_payload_vars` (:244 ST_PublishAprovadaAuto, :444 ST_PublishAprovadaAuditor).
ISSUING_DESFECHOS = frozenset({"aprovada_automatica", "aprovada_auditor"})
KINDS = {
    "beneficiary": frozenset({"authorization", "reimbursement"}),
    "provider": frozenset({"authorization", "account"}),
}
MAX = 65536


class ExternalCaseError(ValueError):
    """Safe classification; private inputs never become outward exception text."""

    def __init__(
        self, code: Literal["invalid", "conflict", "unavailable", "denied", "uncertain"] = "unavailable"
    ):
        self.code = (
            code if code in {"invalid", "conflict", "unavailable", "denied", "uncertain"} else "unavailable"
        )
        super().__init__("external_case_" + self.code)


def now_utc() -> datetime:
    return datetime.now(UTC)


def timestamp(value: str) -> datetime:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z", value):
        raise ExternalCaseError("invalid")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    except ValueError:
        raise ExternalCaseError("invalid") from None


def instant(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def revision(value: str) -> int:
    if type(value) is not str or not re.fullmatch(r"0|[1-9][0-9]*", value) or len(value) > 19:
        raise ExternalCaseError("invalid")
    number = int(value)
    if number > 2**63 - 1:
        raise ExternalCaseError("invalid")
    return number


def digest(value: object) -> str:
    return hashlib.sha256(canonicalize(value)).hexdigest()


class Closed(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid", populate_by_name=True)

    @model_validator(mode="after")
    def primitive_limits(self) -> Self:
        for name in type(self).model_fields:
            value = getattr(self, name)
            if value is None:
                continue
            if name.endswith(("revision", "generation", "epoch", "count")):
                revision(value)
            if name.endswith(("_at", "_until")):
                timestamp(value)
        return self

    def wire(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)

    def canonical(self) -> bytes:
        return canonicalize(self.wire())


def parse[T: Closed](cls: type[T], raw: bytes) -> T:
    try:
        obj = strict_loads(raw)
        if canonicalize(obj) != raw:
            raise ExternalCaseError("invalid")
        parsed = cls.model_validate_json(raw)
        # Python construction aliases/defaults never widen the closed external profile.
        if parsed.canonical() != raw:
            raise ExternalCaseError("invalid")
        return parsed
    except Exception:
        pass
    # Discard provider validation text, chained context and notes before raising outward.
    raise ExternalCaseError("invalid")


class Scope(Closed):
    tenant: Ref
    environment: Ref
    engine_name: Ref
    database_incarnation: Ref

    def params(self) -> dict[str, str]:
        return self.model_dump()


class SubjectBinding(Closed):
    kind: Audience
    resource_ref: Ref


class Identity(Closed):
    upstream_resource_key: Ref
    case_ref: CaseRef
    process_instance_ref: Ref
    process_definition_id: Ref
    process_definition_key: Ref
    process_definition_version: Revision
    process_definition_digest: Digest
    kind: Kind


class DisclosureGrant(Closed):
    grant_ref: Ref
    identity: Identity
    owner: SubjectBinding
    issuer: Ref
    subject: Ref
    principal_ref: Ref
    membership_revision: Revision
    audience: Audience
    operations: tuple[Literal["list", "detail"], ...]
    projection_id: Literal["portal-external-case-summary.v1"]
    fields: tuple[Literal["case_ref", "kind", "state", "record_revision", "state_observed_at"], ...]
    consent_scopes: tuple[Ref, ...]
    grant_revision: Revision
    source_revision: Revision
    decision_receipt_ref: Ref
    decision_digest: Digest
    signer_fingerprint: Digest
    valid_until: str
    state: Literal["active", "revoked"]

    @model_validator(mode="after")
    def distinct(self) -> Self:
        if (
            len(set(self.operations)) != len(self.operations)
            or len(set(self.fields)) != len(self.fields)
            or len(set(self.consent_scopes)) != len(self.consent_scopes)
            or self.owner.kind != self.audience
            or self.identity.kind not in KINDS[self.audience]
        ):
            raise ExternalCaseError("invalid")
        return self


class SourceStatement(Closed):
    schema_: Literal["portal-external-case-source.v1"] = Field(alias="schema")
    scope: Scope
    source_ref: Ref
    source_namespace: Ref
    source_revision: Revision
    identity: Identity
    owners: tuple[SubjectBinding, ...]
    disclosure_grants: tuple[DisclosureGrant, ...]
    ownership_receipt_ref: Ref
    ownership_receipt_digest: Digest
    ownership_signer_fingerprint: Digest
    observed_at: str
    valid_until: str
    state: Literal["active", "revoked"]

    @model_validator(mode="after")
    def identities(self) -> Self:
        if timestamp(self.observed_at) >= timestamp(self.valid_until):
            raise ExternalCaseError("invalid")
        if not self.owners or len({b.canonical() for b in self.owners}) != len(self.owners):
            raise ExternalCaseError("invalid")
        if len({g.grant_ref for g in self.disclosure_grants}) != len(self.disclosure_grants):
            raise ExternalCaseError("invalid")
        for grant in self.disclosure_grants:
            if (
                grant.identity != self.identity
                or grant.owner not in self.owners
                or grant.source_revision != self.source_revision
                or timestamp(grant.valid_until) > timestamp(self.valid_until)
            ):
                raise ExternalCaseError("invalid")
        if self.state == "revoked" and any(g.state == "active" for g in self.disclosure_grants):
            raise ExternalCaseError("invalid")
        return self


class Proof(Closed):
    schema_: Literal["portal-external-assertion.v1"] = Field(alias="schema")
    purpose: Literal["ownership", "disclosure", "completeness"]
    algorithm: Literal["Ed25519"]
    key_fingerprint: Digest
    issued_at: str
    expires_at: str
    digest: Digest
    signature: str

    def signed_bytes(self) -> bytes:
        return canonicalize({k: v for k, v in self.wire().items() if k != "signature"})


class SourcePacket(Closed):
    schema_: Literal["portal-external-source-packet.v1"] = Field(alias="schema")
    statement: SourceStatement
    ownership_proof: Proof
    disclosure_proofs: tuple[Proof, ...]


class CheckpointEntry(Closed):
    namespace: Ref
    source_ref: Ref
    source_revision: Revision
    source_digest: Digest
    upstream_resource_key: Ref
    case_ref: CaseRef
    state: Literal["active", "revoked"]


class NamespacePosition(Closed):
    namespace: Ref
    upstream_position: Ref


class CheckpointStatement(Closed):
    schema_: Literal["portal-external-scope-checkpoint.v1"] = Field(alias="schema")
    scope: Scope
    designation_digest: Digest
    checkpoint_ref: Ref
    epoch: Revision
    predecessor_checkpoint_digest: Digest | None
    upstream_position: Ref
    observed_at: str
    valid_until: str
    namespace_positions: tuple[NamespacePosition, ...]
    heads: tuple[CheckpointEntry, ...]
    heads_count: Revision
    heads_digest: Digest

    @model_validator(mode="after")
    def exact_set(self) -> Self:
        refs = [h.source_ref for h in self.heads]
        namespaces = [p.namespace for p in self.namespace_positions]
        if (
            refs != sorted(set(refs))
            or namespaces != sorted(set(namespaces))
            or len({h.case_ref for h in self.heads}) != len(self.heads)
            or revision(self.heads_count) != len(self.heads)
            or digest([h.wire() for h in self.heads]) != self.heads_digest
            or timestamp(self.observed_at) >= timestamp(self.valid_until)
        ):
            raise ExternalCaseError("invalid")
        return self


class CheckpointPacket(Closed):
    schema_: Literal["portal-external-checkpoint-packet.v1"] = Field(alias="schema")
    statement: CheckpointStatement
    completeness_proof: Proof


class SourceIngressReceipt(Closed):
    schema_: Literal["portal-external-source-ingress-receipt.v1"] = Field(alias="schema")
    scope: Scope
    ingress_id: Ref
    request_digest: Digest
    source_ref: Ref
    source_revision: Revision
    source_digest: Digest
    upstream_receipts_digest: Digest
    source_generation: Revision
    committed_at: str


class CheckpointIngressReceipt(Closed):
    schema_: Literal["portal-external-checkpoint-ingress-receipt.v1"] = Field(alias="schema")
    scope: Scope
    ingress_id: Ref
    request_digest: Digest
    checkpoint_ref: Ref
    epoch: Revision
    checkpoint_digest: Digest
    designation_digest: Digest
    committed_at: str


def bind_ingress(
    packet: SourcePacket | CheckpointPacket, receipt: SourceIngressReceipt | CheckpointIngressReceipt
) -> None:
    if isinstance(packet, SourcePacket) != isinstance(receipt, SourceIngressReceipt):
        raise ExternalCaseError("invalid")
    if packet.statement.scope != receipt.scope or receipt.request_digest != digest(packet.wire()):
        raise ExternalCaseError("invalid")
    fields = (
        ("source_ref", "source_revision")
        if isinstance(packet, SourcePacket)
        else ("checkpoint_ref", "epoch", "designation_digest")
    )
    if any(getattr(receipt, field) != getattr(packet.statement, field) for field in fields):
        raise ExternalCaseError("invalid")
    packet_digest = hashlib.sha256(packet.canonical()).hexdigest()
    if isinstance(receipt, SourceIngressReceipt):
        assert isinstance(packet, SourcePacket)
        statement = packet.statement
        upstream = digest(
            {
                "ownership": statement.ownership_receipt_digest,
                "disclosure": [grant.decision_digest for grant in statement.disclosure_grants],
            }
        )
        if receipt.source_digest != packet_digest or receipt.upstream_receipts_digest != upstream:
            raise ExternalCaseError("invalid")
    elif receipt.checkpoint_digest != packet_digest:
        raise ExternalCaseError("invalid")


class PublicationRequest(Closed):
    schema_: Literal["portal-external-publication-request.v1"] = Field(alias="schema")
    scope: Scope
    publication_id: Ref
    requester_fingerprint: Digest
    captured_provenance_digest: Digest
    kind: Literal["case", "checkpoint"]
    packet: SourcePacket | CheckpointPacket
    ingress_receipt: SourceIngressReceipt | CheckpointIngressReceipt

    @model_validator(mode="after")
    def profile(self) -> Self:
        receipt = self.ingress_receipt
        if (self.kind == "case") != isinstance(self.packet, SourcePacket):
            raise ExternalCaseError("invalid")
        if (self.kind == "case") != isinstance(receipt, SourceIngressReceipt):
            raise ExternalCaseError("invalid")
        if (
            self.scope != self.packet.statement.scope
            or self.scope != receipt.scope
            or receipt.request_digest != hashlib.sha256(self.packet.canonical()).hexdigest()
            or self.captured_provenance_digest != digest(receipt.wire())
        ):
            raise ExternalCaseError("invalid")
        bind_ingress(self.packet, receipt)
        return self


class CasePublicationReceipt(Closed):
    schema_: Literal["portal-external-case-publication-receipt.v1"] = Field(alias="schema")
    kind: Literal["case"]
    scope: Scope
    publication_id: Ref
    request_digest: Digest
    requester_fingerprint: Digest
    source_ref: Ref
    source_revision: Revision
    source_digest: Digest
    source_generation: Revision
    upstream_receipts_digest: Digest
    projection_digest: Digest
    coverage_digest: Digest
    coverage_count: Revision
    authority_revision: Revision
    designation_digest: Digest
    committed_at: str


class CheckpointPublicationReceipt(Closed):
    schema_: Literal["portal-external-checkpoint-publication-receipt.v1"] = Field(alias="schema")
    kind: Literal["checkpoint"]
    scope: Scope
    publication_id: Ref
    request_digest: Digest
    requester_fingerprint: Digest
    checkpoint_ref: Ref
    epoch: Revision
    checkpoint_digest: Digest
    designation_digest: Digest
    heads_digest: Digest
    heads_count: Revision
    accepted_generation: Revision
    published_generation: Revision
    authority_revision: Revision
    committed_at: str

    @model_validator(mode="after")
    def barrier(self) -> Self:
        if self.accepted_generation != self.published_generation:
            raise ExternalCaseError("invalid")
        return self


PublicationReceipt = CasePublicationReceipt | CheckpointPublicationReceipt


class CaseSummary(Closed):
    case_ref: CaseRef
    kind: Kind
    state: Literal["active", "ended"]
    record_revision: Revision
    state_observed_at: str


class Freshness(Closed):
    observed_at: str
    valid_until: str

    @model_validator(mode="after")
    def window(self) -> Self:
        if timestamp(self.observed_at) >= timestamp(self.valid_until):
            raise ExternalCaseError("invalid")
        return self


class CasePage(Closed):
    schema_: Literal["portal-external-case-page.v1"] = Field(alias="schema")
    audience: Audience
    items: tuple[CaseSummary, ...] = Field(max_length=100)
    next_cursor: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]+$", max_length=2048)] | None
    freshness: Freshness

    @model_validator(mode="after")
    def own_kind_order(self) -> Self:
        refs = [item.case_ref for item in self.items]
        if refs != sorted(set(refs)) or any(item.kind not in KINDS[self.audience] for item in self.items):
            raise ExternalCaseError("invalid")
        return self


# WP-J1-07 (fino) — proveniencia de cada valor de `CaseOutcome`. Fica fora da docstring
# porque a docstring vira `description` no OpenAPI publico; a evidencia e interna.
#
# * `desfecho` — os cinco valores que o proprio BPMN declara em
#   `camunda:inputParameter name="event_desfecho"` das service tasks `ST_Publish*`
#   (`spec/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn` :103, :179, :244,
#   :444, :486), reproduzidos no contrato como o payload de
#   `agents.events.auth.completed` (`docs/processes/contracts/SP-OP-AUTH-001.md:268`).
#   O publicador copia a variavel do motor sem interpretar
#   (`src/maezo/tools/workers/events.py:277-279`). A origem no motor e
#   `ACT_HI_PROCINST_.END_ACT_ID_` / a projecao fechada equivalente.
# * `phase` — fatia fina: um unico valor fechado, `decisao_executada`. O contrato exige
#   distinguir "decisao executada" de "comunicado entregue"
#   (`docs/processes/contracts/SP-OP-AUTH-001.md:102`); a comunicacao de sistema em
#   `auth.completed` esta DIFERIDA para o WP-J1-07 completo, entao o unico valor que
#   esta fatia consegue provar a partir do motor e o primeiro. Literal de um membro e o
#   padrao ja usado na fronteira para cadencia fechada
#   (`StaffFreshness.refresh_after_seconds`) e obriga ampliacao deliberada.
# * `authorization_ref` — referencia OPACA de 64 hexadecimais. O `numero_autorizacao`
#   real (`AUTH-{tenant_id}-{numero_guia_tiss}-{uuid8}`, `src/maezo/tools/workers/auth.py:1329`)
#   carrega o numero de guia e NAO cruza esta fronteira: divulgar o recibo externo
#   depende do grant explicito que esta fatia difere. O formato hexadecimal torna
#   estruturalmente impossivel ecoar o composto.
class CaseOutcome(Closed):
    """Desfecho fechado projetado do estado terminal do motor; nada e inferido aqui."""

    phase: Literal["decisao_executada"]
    desfecho: Literal[
        "aprovada_automatica",
        "aprovada_auditor",
        "negada_auditor",
        "nao_requer_autorizacao",
        "cancelada_pendencia",
    ]
    authorization_ref: Digest | None

    @model_validator(mode="after")
    def issued_only_when_bpmn_carries_it(self) -> Self:
        if (self.authorization_ref is not None) != (self.desfecho in ISSUING_DESFECHOS):
            raise ExternalCaseError("invalid")
        return self


class CaseDetail(Closed):
    schema_: Literal["portal-external-case-detail.v1"] = Field(alias="schema")
    case: CaseSummary
    freshness: Freshness
    allowed_actions: tuple[()] = ()
    outcome: CaseOutcome | None

    @model_validator(mode="after")
    def outcome_exactly_when_ended(self) -> Self:
        """Fecha por ausencia: `ended` sem registro de desfecho nao vira projecao.

        A chave `outcome` e obrigatoria no fio (sem default), entao um produtor que a
        omita e recusado por `parse` antes deste validador. Instancia encerrada em um
        fim que nao publica desfecho (`End_FundamentacaoIncompletaBloqueada` :476,
        `End_ErrDecisaoInvalida` :565) nao tem desfecho projetavel e o caso fica
        indisponivel ao publico externo — nunca "encerrado" sem motivo.
        """
        if (self.outcome is None) != (self.case.state == "active"):
            raise ExternalCaseError("invalid")
        return self


class FinalizationReceipt(Closed):
    schema_: Literal["portal-external-case-finalized.v1"] = Field(alias="schema")
    public_projection_digest: Digest
    valid_until: str


def require_publication_capacity(
    packet: SourcePacket | CheckpointPacket, receipt: SourceIngressReceipt | CheckpointIngressReceipt
) -> None:
    """Check the existing 65536-byte external envelope limit before ingress commits.

    The SQL outbox reserves pub_ + 32 UUID hex characters. Signature and SHA256
    fields have fixed encoded lengths. These placeholders are size probes only;
    they are never signed, persisted, returned or treated as authority.
    """
    request = PublicationRequest(
        schema="portal-external-publication-request.v1",
        scope=receipt.scope,
        publication_id="pub_" + "0" * 32,
        requester_fingerprint="0" * 64,
        captured_provenance_digest=digest(receipt.wire()),
        kind="case" if isinstance(packet, SourcePacket) else "checkpoint",
        packet=packet,
        ingress_receipt=receipt,
    )
    envelope = {
        "schema": "portal-external-envelope.v1",
        "purpose": "portal-external-case-publication",
        "scope": receipt.scope.wire(),
        "key_fingerprint": "0" * 64,
        "configuration_digest": "0" * 64,
        "issued_at": receipt.committed_at,
        "expires_at": receipt.committed_at,
        "request": request.wire(),
        "signature": "A" * 86,
    }
    if len(canonicalize(envelope)) > MAX:
        raise ExternalCaseError("invalid")
