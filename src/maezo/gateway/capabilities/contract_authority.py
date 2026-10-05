"""OP12 read-only source projection over a separately qualified authority publisher.

No store, keys, authority installation or default verifier is constructed here. The
source reader and independent proof port must resolve genuine source evidence; the
shared CapabilityAdmission independently verifies the exact projected result again.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal, Protocol, Self

from pydantic import ConfigDict, field_validator, model_validator

from maezo.portal.engine.profile import canonicalize

from .admission import AdmissionBinding
from .models import (
    PROVIDER_SCHEMA_VERSION,
    AnyCapabilityEnvelope,
    CandidateDTO,
    CapabilityContractError,
    CapabilityRefusalReason,
    ClausePurpose,
    ContractAuthorityIntent,
    ContractAuthorityResult,
    Ref,
    parse_envelope,
    parse_request,
)


class SourceDTO(CandidateDTO):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class ContractReadScope(SourceDTO):
    """Actor/task comes from the server binding, never from the request payload."""

    tenant_ref: Ref
    legal_entity_ref: Ref
    principal_ref: Ref
    task_ref: Ref
    source_authority_ref: Ref
    policy_revision: Ref
    data_classification: Ref
    purpose_ref: Ref
    provider_ref: Ref
    contract_instrument_ref: Ref | None = None
    expected_business_revision: Ref | None = None
    clause_purpose: ClausePurpose
    purpose_policy_ref: Ref


class ContractClauseGroup(SourceDTO):
    clause_purpose: ClausePurpose
    clause_refs: tuple[Ref, ...]
    declared_value_refs: tuple[Ref, ...]


class ContractSnapshot(SourceDTO):
    """Full immutable source observation, not a caller assertion of ratification."""

    tenant_ref: Ref
    legal_entity_ref: Ref
    provider_ref: Ref
    contract_instrument_ref: Ref
    business_revision: Ref
    source_revision_ref: Ref
    contract_status: Literal["proposto", "vigente", "suspenso", "rescindido"]
    admission_state: Literal["pending", "received", "validated", "enabled", "invalid", "expired", "revoked"]
    clause_groups: tuple[ContractClauseGroup, ...]
    evidence_ref: Ref
    declared_at: datetime
    authority_receipt_ref: Ref
    source_publication_ref: Ref
    currentness_ref: Ref
    valid_from: datetime
    valid_until: datetime

    @field_validator("declared_at", "valid_from", "valid_until")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("source timestamp requires timezone")
        return value

    @model_validator(mode="after")
    def unique_purposes(self) -> Self:
        purposes = [group.clause_purpose for group in self.clause_groups]
        if len(set(purposes)) != len(purposes) or self.valid_from >= self.valid_until:
            raise ValueError("invalid immutable source snapshot")
        return self

    def snapshot_sha256(self) -> str:
        return hashlib.sha256(canonicalize(self.model_dump(mode="json"))).hexdigest()

    def snapshot_key(self) -> str:
        return hashlib.sha256(
            canonicalize([self.tenant_ref, "OP12", self.contract_instrument_ref, self.business_revision])
        ).hexdigest()


class ContractSnapshotProof(SourceDTO):
    """Returned only after source bytes/mandate/publication/receipt verification."""

    scope: ContractReadScope
    snapshot_sha256: str
    snapshot_key: str
    authority_receipt_ref: Ref
    source_publication_ref: Ref
    currentness_ref: Ref
    checked_at: datetime
    valid_until: datetime

    @field_validator("snapshot_sha256", "snapshot_key")
    @classmethod
    def sha256(cls, value: str) -> str:
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("invalid source digest")
        return value

    @field_validator("checked_at", "valid_until")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        return ContractSnapshot.aware(value)


class ContractSnapshotReader(Protocol):
    """Resolve the full source snapshot; optional identity requires factual discovery.

    Production reads use a DBA-qualified reader role. Snapshot/key/receipt rows are
    immutable publisher-owned records, not rows writable by this runtime.
    """

    async def resolve(self, scope: ContractReadScope, *, timeout_seconds: float) -> ContractSnapshot: ...


class ContractSnapshotVerifier(Protocol):
    """Resolve trusted publication/representation/receipt and current authority.

    Presence of references is insufficient. Implementations independently resolve
    source bytes and mandates; they cannot sign or publish caller snapshots. Each
    proof binds actor, task, tenant, provider, purpose, policy and exact source bytes.
    """

    async def verify_snapshot(
        self, scope: ContractReadScope, snapshot: ContractSnapshot
    ) -> ContractSnapshotProof: ...

    async def check_current(
        self, scope: ContractReadScope, proof: ContractSnapshotProof
    ) -> ContractSnapshotProof: ...


def _now() -> datetime:
    return datetime.now(UTC)


def _deny(reason: CapabilityRefusalReason) -> None:
    raise CapabilityContractError(reason)


class ContractAuthoritySource:
    """Factual OP12 projection; no cache grants authority or defeats revocation."""

    def __init__(
        self,
        *,
        binding: AdmissionBinding,
        reader: ContractSnapshotReader | None = None,
        verifier: ContractSnapshotVerifier | None = None,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self.binding = AdmissionBinding.model_validate(binding)
        if self.binding.operation_name != "contract.authority.resolve" or (
            self.binding.schema_version != PROVIDER_SCHEMA_VERSION
        ):
            raise ValueError("OP12 requires explicit provider schema binding")
        self.reader, self.verifier, self.clock = reader, verifier, clock

    def _scope(self, envelope: AnyCapabilityEnvelope, request: ContractAuthorityIntent) -> ContractReadScope:
        for name in (
            "tenant_ref",
            "legal_entity_ref",
            "operation_name",
            "schema_version",
            "source_authority_ref",
            "policy_revision",
            "data_classification",
        ):
            if getattr(envelope, name) != getattr(self.binding, name):
                _deny(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
        if request.expected_business_revision is not None and (
            request.expected_business_revision != envelope.expected_business_revision
        ):
            _deny(CapabilityRefusalReason.STALE_REVISION)
        return ContractReadScope(
            tenant_ref=self.binding.tenant_ref,
            legal_entity_ref=self.binding.legal_entity_ref,
            principal_ref=self.binding.principal_ref,
            task_ref=self.binding.task_ref,
            source_authority_ref=self.binding.source_authority_ref,
            policy_revision=self.binding.policy_revision,
            data_classification=self.binding.data_classification,
            purpose_ref=self.binding.purpose_ref,
            provider_ref=request.provider_ref,
            contract_instrument_ref=request.contract_instrument_ref,
            expected_business_revision=request.expected_business_revision,
            clause_purpose=request.clause_purpose,
            purpose_policy_ref=request.purpose_policy_ref,
        )

    def _snapshot(self, scope: ContractReadScope, snapshot: ContractSnapshot) -> None:
        for name in ("tenant_ref", "legal_entity_ref", "provider_ref"):
            if getattr(scope, name) != getattr(snapshot, name):
                _deny(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
        if scope.contract_instrument_ref is not None and (
            scope.contract_instrument_ref != snapshot.contract_instrument_ref
        ):
            _deny(CapabilityRefusalReason.CONTRACT_MISMATCH)
        if scope.expected_business_revision is not None and (
            scope.expected_business_revision != snapshot.business_revision
        ):
            _deny(CapabilityRefusalReason.STALE_REVISION)
        now = self.clock()
        if now.utcoffset() is None or not snapshot.valid_from <= now < snapshot.valid_until:
            _deny(CapabilityRefusalReason.STALE_REVISION)
        if snapshot.declared_at > now:
            _deny(CapabilityRefusalReason.CONTRACT_MISMATCH)
        if snapshot.admission_state not in {"validated", "enabled"}:
            _deny(CapabilityRefusalReason.SOURCE_UNAVAILABLE)

    def _proof(
        self,
        scope: ContractReadScope,
        snapshot: ContractSnapshot,
        proof: ContractSnapshotProof,
        *,
        original: ContractSnapshotProof | None = None,
    ) -> None:
        if (
            proof.scope != scope
            or proof.snapshot_sha256 != snapshot.snapshot_sha256()
            or proof.snapshot_key != snapshot.snapshot_key()
            or proof.authority_receipt_ref != snapshot.authority_receipt_ref
            or proof.source_publication_ref != snapshot.source_publication_ref
            or proof.currentness_ref != snapshot.currentness_ref
        ):
            _deny(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
        now = self.clock()
        if now.utcoffset() is None or not proof.checked_at <= now < proof.valid_until:
            _deny(CapabilityRefusalReason.STALE_REVISION)
        if proof.checked_at < max(snapshot.declared_at, snapshot.valid_from) or (
            proof.valid_until > snapshot.valid_until
        ):
            _deny(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
        if original is not None and (
            proof.checked_at < original.checked_at or proof.valid_until > original.valid_until
        ):
            _deny(CapabilityRefusalReason.AUTHORITY_UNPROVEN)

    async def execute(
        self, envelope: AnyCapabilityEnvelope, request: CandidateDTO, *, timeout_seconds: float
    ) -> object:
        envelope = parse_envelope(envelope)
        parsed = parse_request(envelope.operation_name, request, schema_version=envelope.schema_version)
        if not isinstance(parsed, ContractAuthorityIntent):
            _deny(CapabilityRefusalReason.CONTRACT_MISMATCH)
            raise AssertionError("unreachable")
        scope = self._scope(envelope, parsed)
        if self.reader is None:
            _deny(CapabilityRefusalReason.SOURCE_UNAVAILABLE)
        if self.verifier is None:
            _deny(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
        assert self.reader is not None and self.verifier is not None
        snapshot = ContractSnapshot.model_validate(
            await self.reader.resolve(scope, timeout_seconds=timeout_seconds)
        )
        self._snapshot(scope, snapshot)
        proof = ContractSnapshotProof.model_validate(await self.verifier.verify_snapshot(scope, snapshot))
        self._proof(scope, snapshot, proof)
        # Snapshot identity is independent of purpose; select after a fresh proof.
        group = next((g for g in snapshot.clause_groups if g.clause_purpose == scope.clause_purpose), None)
        if group is None or not group.clause_refs:
            _deny(CapabilityRefusalReason.SOURCE_UNAVAILABLE)
            raise AssertionError("unreachable")
        current = ContractSnapshotProof.model_validate(await self.verifier.check_current(scope, proof))
        self._snapshot(scope, snapshot)
        self._proof(scope, snapshot, current, original=proof)
        return ContractAuthorityResult(
            contract_status=snapshot.contract_status,
            clause_refs=group.clause_refs,
            declared_value_refs=group.declared_value_refs,
            declared_at=snapshot.declared_at.isoformat(),
            evidence_ref=snapshot.evidence_ref,
            source_revision_ref=snapshot.source_revision_ref,
            authority_receipt_ref=snapshot.authority_receipt_ref,
            single_provider_scope=True,
            no_inter_provider_aggregation=True,
        )
