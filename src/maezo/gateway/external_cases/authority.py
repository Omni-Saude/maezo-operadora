"""Concrete Ed25519 verification against an independently installed authority bundle.

The importer cannot designate a signer. The pinned installation public key is a
separate deployment input; absence refuses. Original proof bytes remain in every
request so the native receiver can independently repeat these checks.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Self

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import Field, model_validator

from .models import (
    Audience,
    CheckpointPacket,
    Closed,
    Digest,
    ExternalCaseError,
    Kind,
    Proof,
    Ref,
    Revision,
    Scope,
    SourcePacket,
    digest,
    parse,
    timestamp,
)


def unbase64(value: str) -> bytes:
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        if base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") != value:
            raise ValueError
        return raw
    except Exception:
        raise ExternalCaseError("invalid") from None


class Signer(Closed):
    fingerprint: Digest
    public_key_spki_base64: str
    purposes: tuple[Literal["ownership", "disclosure", "completeness"], ...]
    source_namespaces: tuple[Ref, ...]
    kinds: tuple[Kind, ...]
    audiences: tuple[Audience, ...]
    not_before: str
    not_after: str

    @model_validator(mode="after")
    def key_and_scope(self) -> Self:
        raw = unbase64(self.public_key_spki_base64)
        if hashlib.sha256(raw).hexdigest() != self.fingerprint:
            raise ExternalCaseError("invalid")
        try:
            key = serialization.load_der_public_key(raw)
        except Exception:
            raise ExternalCaseError("invalid") from None
        if (
            not isinstance(key, Ed25519PublicKey)
            or key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
            != raw
        ):
            raise ExternalCaseError("invalid")
        if timestamp(self.not_before) >= timestamp(self.not_after):
            raise ExternalCaseError("invalid")
        for values in (self.purposes, self.source_namespaces, self.kinds, self.audiences):
            if not values or len(set(values)) != len(values):
                raise ExternalCaseError("invalid")
        return self


class CompletenessDesignation(Closed):
    designation_ref: Ref
    policy_receipt_ref: Ref
    policy_receipt_digest: Digest
    required_namespaces: tuple[Ref, ...]
    signer_fingerprints: tuple[Digest, ...]
    valid_until: str

    @model_validator(mode="after")
    def nonempty(self) -> Self:
        for values in (self.required_namespaces, self.signer_fingerprints):
            if not values or len(values) != len(set(values)):
                raise ExternalCaseError("invalid")
        return self


class AuthorityBundle(Closed):
    schema_: Literal["portal-external-authority-designation.v1"] = Field(alias="schema")
    scope: Scope
    designation_ref: Ref
    designation_revision: Revision
    installation_receipt_ref: Ref
    policy_receipt_ref: Ref
    policy_receipt_digest: Digest
    signers: tuple[Signer, ...]
    completeness: CompletenessDesignation
    observed_at: str
    valid_until: str

    @model_validator(mode="after")
    def unique(self) -> Self:
        pins = [s.fingerprint for s in self.signers]
        if (
            not pins
            or len(set(pins)) != len(pins)
            or not set(self.completeness.signer_fingerprints).issubset(pins)
            or timestamp(self.observed_at) >= timestamp(self.valid_until)
            or timestamp(self.completeness.valid_until) > timestamp(self.valid_until)
        ):
            raise ExternalCaseError("invalid")
        return self


class InstallationReceipt(Closed):
    schema_: Literal["portal-external-authority-installation.v1"] = Field(alias="schema")
    scope: Scope
    designation_digest: Digest
    receipt_ref: Ref
    observed_at: str
    valid_until: str
    signature: str

    def signed_bytes(self) -> bytes:
        from maezo.portal.engine.profile import canonicalize

        return canonicalize({k: v for k, v in self.wire().items() if k != "signature"})


@dataclass(frozen=True, slots=True, repr=False)
class InstalledAuthority:
    bundle: AuthorityBundle
    designation_digest: str
    revoked_fingerprints: frozenset[str]
    authority_revision: str
    installation_until: datetime

    @classmethod
    def verify(
        cls,
        *,
        bundle_bytes: bytes,
        receipt_bytes: bytes,
        expected_digest: str,
        scope: Scope,
        installation_key: Ed25519PublicKey | None,
        revoked_fingerprints: frozenset[str],
        authority_revision: str,
        now: datetime,
    ) -> InstalledAuthority:
        if installation_key is None:
            raise ExternalCaseError("unavailable")
        bundle = parse(AuthorityBundle, bundle_bytes)
        receipt = parse(InstallationReceipt, receipt_bytes)
        actual = hashlib.sha256(bundle_bytes).hexdigest()
        if (
            actual != expected_digest
            or receipt.designation_digest != actual
            or bundle.scope != scope
            or receipt.scope != scope
            or receipt.receipt_ref != bundle.installation_receipt_ref
        ):
            raise ExternalCaseError("denied")
        try:
            installation_key.verify(unbase64(receipt.signature), receipt.signed_bytes())
        except Exception:
            raise ExternalCaseError("denied") from None
        if not timestamp(receipt.observed_at) <= now < timestamp(receipt.valid_until):
            raise ExternalCaseError("unavailable")
        result = cls(bundle, actual, revoked_fingerprints, authority_revision, timestamp(receipt.valid_until))
        result.current(now)
        return result

    def current(self, now: datetime) -> None:
        if (
            not timestamp(self.bundle.observed_at)
            <= now
            < min(timestamp(self.bundle.valid_until), self.installation_until)
        ):
            raise ExternalCaseError("unavailable")

    def proof(
        self,
        proof: Proof,
        *,
        purpose: str,
        payload: Closed,
        namespaces: set[str],
        kind: str | None,
        audience: str | None,
        now: datetime,
    ) -> datetime:
        self.current(now)
        signer = next((s for s in self.bundle.signers if s.fingerprint == proof.key_fingerprint), None)
        if (
            signer is None
            or proof.key_fingerprint in self.revoked_fingerprints
            or proof.purpose != purpose
            or purpose not in signer.purposes
            or proof.digest != digest(payload.wire())
            or not namespaces.issubset(signer.source_namespaces)
            or (kind is not None and kind not in signer.kinds)
            or (audience is not None and audience not in signer.audiences)
        ):
            raise ExternalCaseError("denied")
        start, end = timestamp(proof.issued_at), timestamp(proof.expires_at)
        if not timestamp(signer.not_before) <= start <= now < end <= timestamp(signer.not_after):
            raise ExternalCaseError("denied")
        try:
            key = serialization.load_der_public_key(unbase64(signer.public_key_spki_base64))
            assert isinstance(key, Ed25519PublicKey)
            key.verify(unbase64(proof.signature), proof.signed_bytes())
        except Exception:
            raise ExternalCaseError("denied") from None
        return min(end, timestamp(self.bundle.valid_until), self.installation_until)

    def source(self, packet: SourcePacket, now: datetime) -> datetime:
        source = packet.statement
        if source.scope != self.bundle.scope or not timestamp(source.observed_at) <= now < timestamp(
            source.valid_until
        ):
            raise ExternalCaseError("denied")
        if packet.ownership_proof.key_fingerprint != source.ownership_signer_fingerprint:
            raise ExternalCaseError("denied")
        until = min(
            timestamp(source.valid_until),
            self.proof(
                packet.ownership_proof,
                purpose="ownership",
                payload=source,
                namespaces={source.source_namespace},
                kind=source.identity.kind,
                audience=None,
                now=now,
            ),
        )
        proofs = {p.digest: p for p in packet.disclosure_proofs}
        if len(proofs) != len(packet.disclosure_proofs) or len(proofs) != len(source.disclosure_grants):
            raise ExternalCaseError("denied")
        for grant in source.disclosure_grants:
            proof = proofs.get(digest(grant.wire()))
            if (
                proof is None
                or proof.key_fingerprint != grant.signer_fingerprint
                or now >= timestamp(grant.valid_until)
            ):
                raise ExternalCaseError("denied")
            until = min(
                until,
                timestamp(grant.valid_until),
                self.proof(
                    proof,
                    purpose="disclosure",
                    payload=grant,
                    namespaces={source.source_namespace},
                    kind=source.identity.kind,
                    audience=grant.audience,
                    now=now,
                ),
            )
        return until

    def checkpoint(self, packet: CheckpointPacket, now: datetime) -> datetime:
        checkpoint, complete = packet.statement, self.bundle.completeness
        namespaces = {p.namespace for p in checkpoint.namespace_positions}
        if (
            checkpoint.scope != self.bundle.scope
            or checkpoint.designation_digest != self.designation_digest
            or namespaces != set(complete.required_namespaces)
            or any(h.namespace not in namespaces for h in checkpoint.heads)
            or packet.completeness_proof.key_fingerprint not in complete.signer_fingerprints
            or not timestamp(checkpoint.observed_at) <= now < timestamp(checkpoint.valid_until)
            or now >= timestamp(complete.valid_until)
        ):
            raise ExternalCaseError("denied")
        return min(
            timestamp(checkpoint.valid_until),
            timestamp(complete.valid_until),
            self.proof(
                packet.completeness_proof,
                purpose="completeness",
                payload=checkpoint,
                namespaces=namespaces,
                kind=None,
                audience=None,
                now=now,
            ),
        )
