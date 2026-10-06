"""Reversible publication state for the proposed internal capability contract.

V2 W1/W2 (INTEGRATION-PLAN-V2, sha256 8b230517...): typed closed contracts are
published before build and before a consumer is enabled; operation catalogs stay
closed, versioned and allowlisted; new bindings stay default-off; withdrawal is a
logical reversal that stops new admissions and never deletes a retained record.

No store, route, DMN or authority is created here. Publication is a governance
record owned by trusted composition: it never proves source authority, currentness
or policy - the admission boundary still verifies every invocation independently.
Only one consumer (the compras runner) is exercised with this seam in its landing
package; the seam therefore stays internal to this cluster until the second
consumer (the vendor OP15 projection, wave VW2) arrives.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import ConfigDict, StringConstraints, ValidationError, field_validator

from maezo.portal.engine.profile import canonicalize

from .admission import Digest
from .models import (
    CONTRACT_STATE,
    CONTRACT_STATE_PUBLICATION_WITHDRAWN,
    CONTRACT_STATE_PUBLISHED,
    PROVIDER_SCHEMA_VERSION,
    REQUEST_MODEL_REGISTRIES,
    RESULT_MODEL_REGISTRIES,
    CandidateDTO,
    CapabilityContractError,
    CapabilityEnvelope,
    CapabilityRefusalReason,
    ProviderCapabilityEnvelope,
)

PublicationSchemaVersion = Literal[
    "v21-capabilities.proposed.v1",
    "provider-capabilities.internal.v1",
]
_PUBLICATION_RECEIPT_MAX = 256
PublicationReceiptRef = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=_PUBLICATION_RECEIPT_MAX,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/@-]*$",
    ),
]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class _PublicationDTO(CandidateDTO):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class ContractPublication(_PublicationDTO):
    """Immutable record of one publication act; the receipt is governance evidence.

    ``contract_sha256`` pins the exact published contract bytes (envelope and the
    closed request/result registries) for the schema version, so a later mutation
    of the mapped fields cannot masquerade as the published contract.
    """

    schema_version: PublicationSchemaVersion
    contract_sha256: Digest
    operations: frozenset[str]
    published_at: datetime
    publication_receipt_ref: PublicationReceiptRef
    contract_state: Literal["INTERNAL_CONTRACT_PUBLISHED"] = "INTERNAL_CONTRACT_PUBLISHED"

    @field_validator("published_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("publication timestamp requires timezone")
        return value


class ContractPublicationWithdrawal(_PublicationDTO):
    """Logical reversal record; retained publication records are never rewritten."""

    schema_version: PublicationSchemaVersion
    contract_sha256: Digest
    withdrawn_at: datetime
    withdrawal_receipt_ref: PublicationReceiptRef
    contract_state: Literal["INTERNAL_CONTRACT_PUBLICATION_WITHDRAWN"] = (
        "INTERNAL_CONTRACT_PUBLICATION_WITHDRAWN"
    )

    @field_validator("withdrawn_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("withdrawal timestamp requires timezone")
        return value


PublicationRecord = ContractPublication | ContractPublicationWithdrawal


def _schema_version_or_none(schema_version: str) -> str | None:
    if type(schema_version) is not str or schema_version not in REQUEST_MODEL_REGISTRIES:
        return None
    return schema_version


def _contract_digest(schema_version: str) -> str:
    """Hash the exact closed contract bytes for one schema version.

    The schema registries are closed module constants; this digest is therefore a
    byte-exact commitment to the mapped envelope and request/result models that
    publication attests. Recomputed from live models on every publish attempt.
    """

    envelope = ProviderCapabilityEnvelope if schema_version == PROVIDER_SCHEMA_VERSION else CapabilityEnvelope
    requests: Mapping[str, type[CandidateDTO]] = REQUEST_MODEL_REGISTRIES[schema_version]
    results: Mapping[str, type[CandidateDTO]] = RESULT_MODEL_REGISTRIES[schema_version]
    return hashlib.sha256(
        canonicalize(
            {
                "schema_version": schema_version,
                "envelope": envelope.model_json_schema(),
                "requests": {
                    operation: model.model_json_schema() for operation, model in sorted(requests.items())
                },
                "results": {
                    operation: model.model_json_schema() for operation, model in sorted(results.items())
                },
            }
        )
    ).hexdigest()


def _receipt(value: str) -> str:
    """Receipt refs are opaque non-blank strings; shape errors are typed refusals."""

    if type(value) is not str or not value.strip():
        raise CapabilityContractError(CapabilityRefusalReason.CONTRACT_MISMATCH)
    return value


class ContractPublicationLedger:
    """Append-only publication record for the closed candidate contracts.

    The current state of a schema version is exactly one of: never published
    (``CONTRACT_STATE``), published (``CONTRACT_STATE_PUBLISHED``) or withdrawn
    (``CONTRACT_STATE_PUBLICATION_WITHDRAWN``). Records are immutable, retained in
    arrival order and never deleted; re-publishing identical content is the same
    record (NO-DUPLICATE-INSTANCE), and conflicting content or receipts under an
    immutable record are refused instead of being merged.
    """

    def __init__(self, *, clock: Callable[[], datetime] = _utc_now) -> None:
        if not callable(clock):
            raise ValueError("invalid publication clock")
        self._clock = clock
        self._latest: dict[str, PublicationRecord] = {}
        self._history: dict[str, list[PublicationRecord]] = {}

    def _append(self, schema_version: str, record: PublicationRecord) -> None:
        self._latest[schema_version] = record
        self._history.setdefault(schema_version, []).append(record)

    def state(self, schema_version: str) -> str:
        record = self._latest.get(schema_version) if type(schema_version) is str else None
        if record is None:
            return CONTRACT_STATE
        return record.contract_state

    def published_contract(self, schema_version: str) -> ContractPublication | None:
        record = self._latest.get(schema_version) if type(schema_version) is str else None
        return record if type(record) is ContractPublication else None

    def history(self, schema_version: str) -> tuple[PublicationRecord, ...]:
        """Retained records in append order; withdrawal never rewrites history."""

        return tuple(self._history.get(schema_version, ()))

    def publish(self, schema_version: str, *, publication_receipt_ref: str) -> ContractPublication:
        if _schema_version_or_none(schema_version) is None:
            raise CapabilityContractError(CapabilityRefusalReason.UNKNOWN_OPERATION)
        _receipt(publication_receipt_ref)
        digest = _contract_digest(schema_version)
        current = self._latest.get(schema_version)
        if type(current) is ContractPublication:
            # The published record is immutable: only the exact replay returns it.
            if (
                current.contract_sha256 != digest
                or current.publication_receipt_ref != publication_receipt_ref
            ):
                raise CapabilityContractError(CapabilityRefusalReason.CONTRACT_MISMATCH)
            return current
        try:
            record = ContractPublication(
                schema_version=schema_version,  # type: ignore[arg-type]
                contract_sha256=digest,
                operations=frozenset(REQUEST_MODEL_REGISTRIES[schema_version]),
                published_at=self._clock(),
                publication_receipt_ref=publication_receipt_ref,
            )
        except ValidationError as exc:
            raise CapabilityContractError(CapabilityRefusalReason.CONTRACT_MISMATCH) from exc
        self._append(schema_version, record)
        return record

    def withdraw(self, schema_version: str, *, withdrawal_receipt_ref: str) -> ContractPublicationWithdrawal:
        if _schema_version_or_none(schema_version) is None:
            raise CapabilityContractError(CapabilityRefusalReason.UNKNOWN_OPERATION)
        _receipt(withdrawal_receipt_ref)
        current = self._latest.get(schema_version)
        if type(current) is ContractPublicationWithdrawal:
            if current.withdrawal_receipt_ref != withdrawal_receipt_ref:
                raise CapabilityContractError(CapabilityRefusalReason.CONTRACT_MISMATCH)
            return current
        if current is None:
            # Honest inertia: a never-published contract has nothing to withdraw.
            raise CapabilityContractError(CapabilityRefusalReason.CONTRACT_MISMATCH)
        try:
            record = ContractPublicationWithdrawal(
                schema_version=schema_version,  # type: ignore[arg-type]
                contract_sha256=current.contract_sha256,
                withdrawn_at=self._clock(),
                withdrawal_receipt_ref=withdrawal_receipt_ref,
            )
        except ValidationError as exc:
            raise CapabilityContractError(CapabilityRefusalReason.CONTRACT_MISMATCH) from exc
        self._append(schema_version, record)
        return record

    def require_published(self, schema_version: str, operation_name: str) -> None:
        """Consumer gate over the published contract; fail-closed and typed.

        Raises ``CapabilityContractError(SOURCE_UNAVAILABLE)`` when the contract is
        not currently published - the honest inertia the vendor registry (section
        5.1) assigns to consumers of an unpublished núcleo. It never proves source
        authority; admission still verifies each invocation independently.
        """

        published = self.published_contract(schema_version)
        if published is None or operation_name not in published.operations:
            raise CapabilityContractError(CapabilityRefusalReason.SOURCE_UNAVAILABLE)


__all__ = [
    "CONTRACT_STATE_PUBLICATION_WITHDRAWN",
    "CONTRACT_STATE_PUBLISHED",
    "ContractPublication",
    "ContractPublicationLedger",
    "ContractPublicationWithdrawal",
    "PublicationRecord",
    "PublicationReceiptRef",
    "PublicationSchemaVersion",
]
