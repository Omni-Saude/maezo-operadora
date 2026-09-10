"""SC1 cryptographic admission of independently issued staff authority.

This verifier does not issue policy. The native publication/read commands must
also verify their current, enlisted source heads and membership observations.
The complete retained witness, not the actor hash, is the membership read pin.
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from maezo.gateway.external_cases.models import Identity, Scope, digest, parse, timestamp
from maezo.gateway.human.auth_profile import Actor
from maezo.gateway.human.read_profile import digest as read_digest
from maezo.portal.contracts.models import HumanPrincipal
from maezo.portal.engine.profile import canonicalize

from .models import (
    FIELDS,
    Designation,
    DesignationEntry,
    MembershipWitness,
    PolicyDecision,
    Proof,
    ScopeCheckpoint,
    StaffCaseError,
    StaffCaseGrant,
    StaffPublication,
)


def actor_digest(principal: HumanPrincipal) -> str:
    """Reuse the AUTH Actor number-free wire; never hash an integer JSON revision."""
    return read_digest(Actor.from_principal(principal, "staff"))


def _decode(value: str, size: int | None = None) -> bytes:
    try:
        raw = base64.b64decode(value, validate=True)
        if base64.b64encode(raw).decode("ascii") != value or (size is not None and len(raw) != size):
            raise ValueError
        return raw
    except (ValueError, UnicodeError):
        raise StaffCaseError("invalid") from None


def _key(value: str) -> Ed25519PublicKey:
    try:
        key = serialization.load_der_public_key(_decode(value))
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError
        return key
    except (ValueError, TypeError):
        raise StaffCaseError("invalid") from None


def fingerprint(key: Ed25519PublicKey) -> str:
    return hashlib.sha256(
        key.public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    ).hexdigest()


def _window(start: str, end: str, now: datetime) -> datetime:
    a, b = timestamp(start), timestamp(end)
    if not a <= now < b:
        raise StaffCaseError("unavailable")
    return b


def _signature(proof: Proof, key: Ed25519PublicKey, statement: object, purpose: str) -> None:
    if (
        proof.purpose != purpose
        or proof.statement_digest != digest(statement)
        or proof.key_fingerprint != fingerprint(key)
    ):
        raise StaffCaseError("denied")
    unsigned = proof.wire()
    unsigned.pop("signature")
    try:
        key.verify(_decode(proof.signature, 64), canonicalize(unsigned))
    except InvalidSignature:
        raise StaffCaseError("denied") from None


@dataclass(frozen=True)
class VerifiedGrant:
    grant: StaffCaseGrant
    identity: Identity
    actor_digest: str
    membership_digest: str
    fields: Mapping[str, frozenset[str]]
    task_decisions: tuple[PolicyDecision, ...]
    valid_until: datetime


@dataclass(frozen=True)
class InstalledStaffAuthority:
    designation: Designation
    installation_digest: str
    entries: Mapping[str, DesignationEntry]
    revoked_fingerprints: frozenset[str]
    valid_until: datetime

    @classmethod
    def verify(
        cls,
        *,
        designation_bytes: bytes,
        installation_proof: Proof,
        expected_digest: str,
        expected_scope: Scope,
        root: Ed25519PublicKey,
        revoked_fingerprints: frozenset[str],
        now: datetime,
    ) -> InstalledStaffAuthority:
        designation = parse(Designation, designation_bytes)
        if (
            len(designation_bytes) > 65536
            or digest(designation.wire()) != expected_digest
            or designation.scope != expected_scope
            or designation.state != "active"
            or fingerprint(root) in revoked_fingerprints
        ):
            raise StaffCaseError("denied")
        _signature(installation_proof, root, designation.wire(), "installation")
        until = min(
            _window(designation.issued_at, designation.valid_until, now),
            _window(installation_proof.issued_at, installation_proof.expires_at, now),
        )
        entries: dict[str, DesignationEntry] = {}
        for entry in designation.entries:
            key = _key(entry.public_key)
            # Key bytes are independently supplied inputs, never obtained from the request proof.
            if fingerprint(key) != entry.key_fingerprint or fingerprint(key) == fingerprint(root):
                raise StaffCaseError("denied")
            if timestamp(entry.not_before) >= timestamp(entry.valid_until):
                raise StaffCaseError("invalid")
            entries[entry.key_fingerprint] = entry
        return cls(designation, expected_digest, MappingProxyType(entries), revoked_fingerprints, until)

    def proof(
        self,
        proof: Proof,
        statement: object,
        *,
        role: str,
        purpose: str,
        now: datetime,
        source_ref: str | None = None,
        projection: str | None = None,
        fields: frozenset[str] = frozenset(),
        operation: str | None = None,
    ) -> datetime:
        if now >= self.valid_until:
            raise StaffCaseError("unavailable")
        entry = self.entries.get(proof.key_fingerprint)
        if (
            entry is None
            or entry.role != role
            or purpose not in entry.purposes
            or entry.key_fingerprint in self.revoked_fingerprints
            or (source_ref is not None and entry.source_ref != source_ref)
        ):
            raise StaffCaseError("denied")
        if projection is not None and (
            projection not in entry.projections
            or operation not in entry.operations
            or not fields <= FIELDS[projection]
        ):
            raise StaffCaseError("denied")
        until = min(
            self.valid_until,
            _window(entry.not_before, entry.valid_until, now),
            _window(proof.issued_at, proof.expires_at, now),
        )
        if timestamp(proof.issued_at) < timestamp(entry.not_before) or timestamp(
            proof.expires_at
        ) > timestamp(entry.valid_until):
            raise StaffCaseError("denied")
        _signature(proof, _key(entry.public_key), statement, purpose)
        return until

    def membership(
        self,
        witness: MembershipWitness,
        principal: HumanPrincipal,
        *,
        now: datetime,
        native_revision: str,
        native_digest: str,
    ) -> datetime:
        if (
            witness.scope != self.designation.scope
            or principal.tenant != witness.scope.tenant
            or witness.actor != Actor.from_principal(principal, "staff")
            or witness.session_ref != principal.session_ref
            or witness.principal_record_revision != native_revision
            or witness.principal_record_digest != native_digest
        ):
            raise StaffCaseError("denied")
        payload = witness.wire()
        payload.pop("proof")
        until = self.proof(
            witness.proof,
            payload,
            role="identity_verifier",
            purpose="membership_current",
            source_ref=witness.source.source_ref,
            now=now,
        )
        if not witness.source.observed_at <= now < witness.source.valid_until:
            raise StaffCaseError("unavailable")
        return min(until, witness.source.valid_until, _window(witness.observed_at, witness.valid_until, now))

    def grant(
        self,
        publication: StaffPublication,
        principal: HumanPrincipal,
        identity: Identity,
        witness: MembershipWitness,
        *,
        now: datetime,
        native_revision: str,
        native_digest: str,
        operation: str = "detail",
    ) -> VerifiedGrant:
        if publication.kind != "case_grant" or not isinstance(publication.payload, StaffCaseGrant):
            raise StaffCaseError("denied")
        grant = publication.payload
        if (
            publication.scope != grant.scope
            or publication.source_ref != grant.source_ref
            or publication.source_revision != grant.source_revision
            or publication.payload_digest != digest(grant.wire())
        ):
            raise StaffCaseError("denied")
        signed_publication = publication.wire()
        signed_publication.pop("proof")
        membership_until = self.membership(
            witness, principal, now=now, native_revision=native_revision, native_digest=native_digest
        )
        if (
            grant.scope != self.designation.scope
            or grant.case_ref != identity.case_ref
            or identity.kind != "authorization"
            or grant.identity_digest != digest(identity.wire())
            or grant.state != "active"
            or grant.issuer != principal.issuer
            or grant.subject != principal.subject
            or grant.principal_ref != principal.principal_ref
            or grant.membership_revision != str(principal.membership_revision)
        ):
            raise StaffCaseError("denied")
        until = min(
            membership_until,
            _window(grant.observed_at, grant.valid_until, now),
            _window(publication.observed_at, publication.valid_until, now),
            self.proof(
                publication.proof,
                signed_publication,
                role="case_issuer",
                purpose="staff_case_grant",
                source_ref=grant.source_ref,
                now=now,
            ),
        )
        fields: dict[str, frozenset[str]] = {}
        task_decisions: list[PolicyDecision] = []
        for decision in grant.decisions:
            if decision.operation != operation:
                continue
            until = min(until, self._decision(decision, grant, principal, now))
            if decision.resource_identity_digest == grant.identity_digest:
                if decision.projection in fields:
                    raise StaffCaseError("denied")
                fields[decision.projection] = frozenset(decision.fields) - (
                    {"created_at"} if decision.projection == "staff_current_task.v1" else set()
                )
            else:
                # Exact task resource must be matched against current native facts
                # before this additional decision can expose created_at.
                task_decisions.append(decision)
        # Exact detail always discloses the complete closed summary and canonical identity.
        # A narrow field grant must not be widened by a DTO's required fields/defaults.
        required = (
            ("staff_summary.v1", "staff_identity.v1") if operation == "detail" else ("staff_summary.v1",)
        )
        for projection in required:
            if fields.get(projection) != FIELDS[projection]:
                raise StaffCaseError("denied")
        return VerifiedGrant(
            grant,
            identity,
            actor_digest(principal),
            digest(witness.wire()),
            MappingProxyType(fields),
            tuple(task_decisions),
            until,
        )

    def _decision(
        self,
        decision: PolicyDecision,
        grant: StaffCaseGrant,
        principal: HumanPrincipal,
        now: datetime,
    ) -> datetime:
        if (
            decision.state != "active"
            or decision.subject_identity_digest != actor_digest(principal)
            or decision.membership_revision != grant.membership_revision
            or (
                decision.resource_identity_digest != grant.identity_digest
                and (decision.projection != "staff_current_task.v1" or "created_at" not in decision.fields)
            )
        ):
            raise StaffCaseError("denied")
        payload = decision.wire()
        payload.pop("decision_proof")
        return min(
            _window(decision.observed_at, decision.valid_until, now),
            self.proof(
                decision.decision_proof,
                payload,
                role="case_issuer",
                purpose="staff_case_grant",
                projection=decision.projection,
                fields=frozenset(decision.fields),
                source_ref=grant.source_ref,
                now=now,
                operation=decision.operation,
            ),
        )

    def checkpoint(
        self,
        publication: StaffPublication,
        principal: HumanPrincipal,
        witness: MembershipWitness,
        *,
        now: datetime,
        native_revision: str,
        native_digest: str,
    ) -> tuple[ScopeCheckpoint, datetime]:
        if publication.kind != "scope_checkpoint" or not isinstance(publication.payload, ScopeCheckpoint):
            raise StaffCaseError("denied")
        checkpoint = publication.payload
        if (
            publication.scope != checkpoint.scope
            or publication.payload_digest != digest(checkpoint.wire())
            or checkpoint.scope != self.designation.scope
            or checkpoint.principal_identity_digest != actor_digest(principal)
            or checkpoint.issuer != principal.issuer
            or checkpoint.subject != principal.subject
            or checkpoint.principal_ref != principal.principal_ref
            or checkpoint.membership_revision != str(principal.membership_revision)
        ):
            raise StaffCaseError("denied")
        membership_until = self.membership(
            witness, principal, now=now, native_revision=native_revision, native_digest=native_digest
        )
        signed_checkpoint = checkpoint.wire()
        signed_checkpoint.pop("proof")
        signed_publication = publication.wire()
        signed_publication.pop("proof")
        until = min(
            membership_until,
            _window(checkpoint.observed_at, checkpoint.valid_until, now),
            _window(publication.observed_at, publication.valid_until, now),
            self.proof(
                checkpoint.proof,
                signed_checkpoint,
                role="case_issuer",
                purpose="scope_complete",
                source_ref=publication.source_ref,
                now=now,
            ),
            self.proof(
                publication.proof,
                signed_publication,
                role="case_issuer",
                purpose="scope_complete",
                source_ref=publication.source_ref,
                now=now,
            ),
        )
        return checkpoint, until
