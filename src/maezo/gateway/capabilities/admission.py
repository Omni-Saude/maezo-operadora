"""Inert v2.1 admission candidate; no provider, policy or production binding is installed.

The owner-qualified verifier is the trust boundary. Request fields, shadow verdicts and
the presence of an evidence reference never create authority. Leases only guard this
invocation; they are not source idempotency, a durable receipt or a business commit.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError, field_validator

from maezo.gateway.capabilities.models import (
    AnyCapabilityEnvelope,
    CandidateDTO,
    CapabilityRefusalReason,
    request_digest,
)
from maezo.gateway.pep import HARD_ACTIONS, PEP, Decision
from maezo.portal.engine.profile import canonicalize

Ref = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=256, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/@-]*$"),
]
Digest = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")]
Phase = Literal["before_source", "before_disclosure"]


class AdmissionDeniedError(PermissionError):
    """Sanitized technical refusal; neither requests nor provider exceptions are retained."""

    def __init__(self, reason: CapabilityRefusalReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


class AdmissionDTO(CandidateDTO):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class AdmissionBinding(AdmissionDTO):
    """Server-owned agent/task/operation scope, subject to W0 publication and ratification.

    These values are never resolved from a graph's authority claims. A qualified verifier
    must check the signed task/Card, operation allowlist and corresponding action policy.
    Constructing this value object does not admit a binding.
    """

    principal_ref: Ref
    task_ref: Ref
    tenant_ref: Ref
    legal_entity_ref: Ref
    purpose_ref: Ref
    operation_name: Ref
    schema_version: Ref
    contract_revision: Ref
    source_authority_ref: Ref
    policy_revision: Ref
    data_classification: Ref
    autonomy_action: Ref
    security_zone: Literal["general", "phi"]

    @field_validator("autonomy_action")
    @classmethod
    def preserve_hard_authority(cls, value: str) -> str:
        if value in HARD_ACTIONS:
            raise ValueError("hard action requires its existing human boundary")
        return value


class VerifiedAuthority(AdmissionDTO):
    """Verified evidence returned by the qualified port, never an input DTO.

    The port must prove every field, not echo the caller. ``enforcing`` and ``allow``
    describe its checked result; they are not configuration switches in this module.
    """

    binding: AdmissionBinding
    request_sha256: Digest
    authorization_ref: Ref
    signed_task_ref: Ref
    agent_card_sha256: Digest
    source_contract_publication_ref: Ref
    policy_ratification_ref: Ref
    currentness_ref: Ref
    enforcement: Literal["enforcing"]
    decision: Literal["allow"]
    verified_at: datetime
    valid_until: datetime

    @field_validator("verified_at", "valid_until")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("authoritative timestamp requires timezone")
        return value


class VerifiedCurrentness(AdmissionDTO):
    """Current source/policy/actor/consent observation; revocation returns a refusal.

    ``currentness_ref`` must encompass all revisions required by the operation's source
    contract, including erasure/alias/consent where applicable. No generic TTL is invented.
    """

    binding: AdmissionBinding
    request_sha256: Digest
    authorization_ref: Ref
    currentness_ref: Ref
    source_result_sha256: Digest | None = None
    checked_at: datetime
    valid_until: datetime

    @field_validator("checked_at", "valid_until")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        return VerifiedAuthority.aware_time(value)


class VerifiedSourceResult(AdmissionDTO):
    """Exact result receipt verified by the domain source; HTTP/Kafka ACKs do not qualify."""

    binding: AdmissionBinding
    request_sha256: Digest
    result_sha256: Digest
    authorization_ref: Ref
    source_receipt_ref: Ref
    source_revision_ref: Ref
    currentness_ref: Ref
    checked_at: datetime
    valid_until: datetime

    @field_validator("checked_at", "valid_until")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        return VerifiedAuthority.aware_time(value)


class AdmissionAuditIntent(AdmissionDTO):
    """Audit-before-dispatch metadata: opaque references and digests, no request payload."""

    binding: AdmissionBinding
    request_sha256: Digest
    authorization_ref: Ref
    source_contract_publication_ref: Ref
    policy_ratification_ref: Ref
    currentness_ref: Ref


class AuthorityVerifier(Protocol):
    """Must be qualified for its source, policy and signed principal/task before composition.

    Each failure (including unknown, revoked or erased authority) raises AdmissionDeniedError.
    No factory for this protocol is included in the candidate. UNIT doubles are not providers.
    """

    async def authorize(
        self, binding: AdmissionBinding, envelope: AnyCapabilityEnvelope, request: BaseModel
    ) -> VerifiedAuthority: ...

    async def check_current(
        self, authority: VerifiedAuthority, *, source_result: VerifiedSourceResult | None = None
    ) -> VerifiedCurrentness: ...

    async def verify_source_result(
        self, authority: VerifiedAuthority, result: BaseModel
    ) -> VerifiedSourceResult: ...


class AdmissionAuditPort(Protocol):
    """Commit an audit intent durably and return its SHA-256 receipt before source IO."""

    async def emit_intent(self, intent: AdmissionAuditIntent) -> str: ...


@dataclass(frozen=True, slots=True, repr=False)
class AdmissionLease:
    """Unforgeable by value: only the exact object issued by this boundary is accepted."""

    token: str


@dataclass(slots=True, repr=False)
class _LeaseRecord:
    lease: AdmissionLease
    envelope: AnyCapabilityEnvelope
    request: BaseModel
    authority: VerifiedAuthority
    audit_receipt: str
    phase: Literal["authorized", "dispatched", "result_verified"] = "authorized"
    result: BaseModel | None = None
    result_sha256: str | None = None
    source_result: VerifiedSourceResult | None = None


def _utc_now() -> datetime:
    return datetime.now(UTC)


def result_digest(result: BaseModel) -> str:
    """Hash the exact closed result using the same canonical profile as request_digest."""
    # model_dump_json is not used as a signature profile: key ordering must be canonical.
    return hashlib.sha256(canonicalize(result.model_dump(mode="json"))).hexdigest()


class CapabilityAdmission:
    """Strict invocation guard with no permissive fallback and no operational defaults."""

    def __init__(
        self,
        *,
        binding: AdmissionBinding,
        authority: AuthorityVerifier | None = None,
        audit: AdmissionAuditPort | None = None,
        autonomy: PEP | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._binding = AdmissionBinding.model_validate(binding)
        self._authority = authority
        self._audit = audit
        self._autonomy = autonomy
        self._clock = clock
        self._leases: dict[str, _LeaseRecord] = {}

    @property
    def binding(self) -> AdmissionBinding:
        return self._binding

    def _fresh(self, observed_at: datetime, valid_until: datetime) -> None:
        now = self._clock()
        if now.utcoffset() is None or not observed_at <= now < valid_until:
            raise AdmissionDeniedError(CapabilityRefusalReason.STALE_REVISION)

    def _envelope_scope(self, envelope: AnyCapabilityEnvelope) -> None:
        for name in (
            "tenant_ref",
            "legal_entity_ref",
            "operation_name",
            "schema_version",
            "source_authority_ref",
            "policy_revision",
            "data_classification",
        ):
            if getattr(envelope, name) != getattr(self._binding, name):
                raise AdmissionDeniedError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)

    def _autonomy_allowed(self) -> None:
        if (
            self._autonomy is None
            or self._autonomy.matrix.tenant != self._binding.tenant_ref
            or self._autonomy.evaluate(self._binding.autonomy_action) is not Decision.ALLOW
        ):
            raise AdmissionDeniedError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)

    def _record(self, lease: AdmissionLease) -> _LeaseRecord:
        record = self._leases.get(lease.token)
        if record is None or record.lease is not lease:
            raise AdmissionDeniedError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
        if request_digest(record.envelope, record.request) != record.authority.request_sha256:
            raise AdmissionDeniedError(CapabilityRefusalReason.CONTRACT_MISMATCH)
        self._fresh(record.authority.verified_at, record.authority.valid_until)
        return record

    def release(self, lease: AdmissionLease) -> None:
        """Forget this invocation after success/failure/cancellation, never settle source work.

        The dispatcher calls this in finally. Domain idempotency and uncertain effects
        belong to the durable source and are unaffected by invalidating a local lease.
        """
        record = self._leases.get(lease.token)
        if record is not None and record.lease is lease:
            del self._leases[lease.token]

    async def authorize(self, envelope: AnyCapabilityEnvelope, request: BaseModel) -> AdmissionLease:
        try:
            self._envelope_scope(envelope)
            self._autonomy_allowed()
            if self._authority is None:
                raise AdmissionDeniedError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
            if self._audit is None:
                raise AdmissionDeniedError(CapabilityRefusalReason.AUDIT_UNAVAILABLE)
            evidence = VerifiedAuthority.model_validate(
                await self._authority.authorize(self._binding, envelope, request)
            )
            if evidence.binding != self._binding or evidence.request_sha256 != request_digest(
                envelope, request
            ):
                raise AdmissionDeniedError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
            self._fresh(evidence.verified_at, evidence.valid_until)
            intent = AdmissionAuditIntent(
                binding=self._binding,
                request_sha256=evidence.request_sha256,
                authorization_ref=evidence.authorization_ref,
                source_contract_publication_ref=evidence.source_contract_publication_ref,
                policy_ratification_ref=evidence.policy_ratification_ref,
                currentness_ref=evidence.currentness_ref,
            )
            try:
                receipt = await self._audit.emit_intent(intent)
                if type(receipt) is not str or re.fullmatch(r"[0-9a-f]{64}", receipt) is None:
                    raise AdmissionDeniedError(CapabilityRefusalReason.AUDIT_UNAVAILABLE)
            except Exception:
                raise AdmissionDeniedError(CapabilityRefusalReason.AUDIT_UNAVAILABLE) from None
            lease = AdmissionLease(uuid.uuid4().hex)
            self._leases[lease.token] = _LeaseRecord(lease, envelope, request, evidence, receipt)
            return lease
        except AdmissionDeniedError:
            raise
        except (ValidationError, ValueError, TypeError):
            raise AdmissionDeniedError(CapabilityRefusalReason.AUTHORITY_UNPROVEN) from None
        except Exception:
            raise AdmissionDeniedError(CapabilityRefusalReason.SOURCE_UNAVAILABLE) from None

    async def revalidate(self, lease: AdmissionLease, *, phase: Phase) -> None:
        try:
            record = self._record(lease)
            self._autonomy_allowed()
            expected = "authorized" if phase == "before_source" else "result_verified"
            if phase not in ("before_source", "before_disclosure") or record.phase != expected:
                raise AdmissionDeniedError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
            if self._authority is None:
                raise AdmissionDeniedError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
            current = VerifiedCurrentness.model_validate(
                await self._authority.check_current(record.authority, source_result=record.source_result)
            )
            if (
                current.binding != self._binding
                or current.authorization_ref != record.authority.authorization_ref
                or current.request_sha256 != record.authority.request_sha256
                or current.currentness_ref != record.authority.currentness_ref
                or current.source_result_sha256 != record.result_sha256
                or current.checked_at < record.authority.verified_at
            ):
                raise AdmissionDeniedError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
            self._fresh(current.checked_at, current.valid_until)
            # Recheck after the await: two concurrent callers cannot both dispatch this lease.
            if self._record(lease).phase != expected:
                raise AdmissionDeniedError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
            if phase == "before_source":
                record.phase = "dispatched"
            else:
                if record.result is None or result_digest(record.result) != record.result_sha256:
                    raise AdmissionDeniedError(CapabilityRefusalReason.CONTRACT_MISMATCH)
                if record.source_result is None:
                    raise AdmissionDeniedError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
                self._fresh(record.source_result.checked_at, record.source_result.valid_until)
                del self._leases[lease.token]
        except AdmissionDeniedError:
            raise
        except (ValidationError, ValueError, TypeError):
            raise AdmissionDeniedError(CapabilityRefusalReason.AUTHORITY_UNPROVEN) from None
        except Exception:
            raise AdmissionDeniedError(CapabilityRefusalReason.SOURCE_UNAVAILABLE) from None

    async def verify_result(self, lease: AdmissionLease, result: BaseModel) -> None:
        try:
            record = self._record(lease)
            if record.phase != "dispatched" or self._authority is None:
                raise AdmissionDeniedError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
            if (
                self._binding.security_zone == "general"
                and getattr(result, "clinical_result_context_ref", None) is not None
            ):
                raise AdmissionDeniedError(CapabilityRefusalReason.PURPOSE_DENIED)
            evidence = VerifiedSourceResult.model_validate(
                await self._authority.verify_source_result(record.authority, result)
            )
            digest = result_digest(result)
            if (
                evidence.binding != self._binding
                or evidence.request_sha256 != record.authority.request_sha256
                or evidence.authorization_ref != record.authority.authorization_ref
                or evidence.result_sha256 != digest
                or evidence.currentness_ref != record.authority.currentness_ref
                or evidence.checked_at < record.authority.verified_at
            ):
                raise AdmissionDeniedError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
            self._fresh(evidence.checked_at, evidence.valid_until)
            if self._record(lease).phase != "dispatched":
                raise AdmissionDeniedError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
            record.result = result
            record.result_sha256 = digest
            record.source_result = evidence
            record.phase = "result_verified"
        except AdmissionDeniedError:
            raise
        except (ValidationError, ValueError, TypeError):
            raise AdmissionDeniedError(CapabilityRefusalReason.AUTHORITY_UNPROVEN) from None
        except Exception:
            raise AdmissionDeniedError(CapabilityRefusalReason.SOURCE_UNAVAILABLE) from None
