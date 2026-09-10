"""ADR0049 D3/D5: authenticated legacy/v2 cohort qualification, not an authority issuer.

Production root keys/designations and real deployment/classification/consumer/freeze
issuers are independent prerequisites. Synthetic signing belongs exclusively in tests.
Every verification consumes the latest durable root-signed authority designation.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timedelta
from typing import Literal

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, Field, model_validator

from maezo.portal.contracts.models import OpaqueRef, Revision, Sha256Digest
from maezo.portal.engine.decision import _BINDINGS, _LEGACY_BINDINGS, _OUTCOMES
from maezo.portal.engine.profile import canonicalize

from .models import Scope
from .read_profile import Closed, ReadCatalogEntry, b64decode, parse_model, wire

MAX_BYTES = 2 * 1024 * 1024
COHORT_CONTRACT = "fd6a9190d53aa2689bc4e7e04d05350d4ee2562955316adf6675008382d69048"
FREEZE_CONTRACT = "human-decision-source-freeze.v1"
Purpose = Literal["deployment", "classification", "consumer", "freeze"]


class BindingUnavailableError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("decision binding unavailable")


def canonical(value: BaseModel) -> bytes:
    result = canonicalize(wire(value))
    if len(result) > MAX_BYTES:
        raise BindingUnavailableError()
    return result


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def decode[T: Closed](model: type[T], raw: bytes) -> T:
    try:
        if not raw or len(raw) > MAX_BYTES:
            raise BindingUnavailableError()

        def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in items:
                if key in result:
                    raise BindingUnavailableError()
                result[key] = value
            return result

        def number(_: str) -> None:
            raise BindingUnavailableError()

        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_int=number,
            parse_float=number,
            parse_constant=number,
        )
        if canonicalize(value) != raw:
            raise BindingUnavailableError()
        return parse_model(model, value)
    except Exception:
        raise BindingUnavailableError() from None


def current(start: datetime, end: datetime, now: datetime) -> None:
    if any(v.tzinfo is None or v.utcoffset() != timedelta(0) for v in (start, end, now)):
        raise BindingUnavailableError()
    if not start <= now < end:
        raise BindingUnavailableError()


class Signature(Closed):
    key_id: OpaqueRef
    value: str = Field(repr=False)


def verify_signature(raw: bytes, signature: Signature, key: str) -> None:
    try:
        Ed25519PublicKey.from_public_bytes(b64decode(key, size=32)).verify(
            b64decode(signature.value, size=64), raw
        )
    except Exception:
        raise BindingUnavailableError() from None


class SignerDesignation(Closed):
    issuer: OpaqueRef
    key_id: OpaqueRef
    public_key: str
    purpose: Purpose
    contract_ref: OpaqueRef
    contract_digest: Sha256Digest
    installed_authority_ref: OpaqueRef
    observed_at: datetime
    valid_until: datetime


class AuthorityDocument(Closed):
    schema_: Literal["human-decision-authorities.v1"] = Field(alias="schema")
    scope: Scope
    installation_id: OpaqueRef
    generation: Revision
    expected_tenant_revision: Revision
    operation_id: OpaqueRef
    observed_at: datetime
    valid_until: datetime
    signers: tuple[SignerDesignation, ...]

    @model_validator(mode="after")
    def unique(self) -> AuthorityDocument:
        if len({s.key_id for s in self.signers}) != len(self.signers):
            raise ValueError("duplicate authority key")
        # Different purpose labels on identical material are not independent signers.
        if len({s.public_key for s in self.signers}) != len(self.signers):
            raise ValueError("duplicate authority material")
        return self


class SignedAuthorities(Closed):
    document: AuthorityDocument
    signature: Signature


class RootDesignation(Closed):
    """Out-of-band root pin, never accepted as part of a submitted packet."""

    scope: Scope
    installation_id: OpaqueRef
    key_id: OpaqueRef
    public_key: str
    installation_digest: Sha256Digest
    freeze_contract_digest: Sha256Digest
    freeze_installed_authority_ref: OpaqueRef
    observed_at: datetime
    valid_until: datetime


def artifact_bytes(artifact: Artifact) -> bytes:
    try:
        value = base64.b64decode(artifact.bytes_base64, validate=True)
        if (
            not value
            or len(value) > 1024 * 1024
            or base64.b64encode(value).decode() != artifact.bytes_base64
            or sha(value) != artifact.digest
        ):
            raise BindingUnavailableError()
        return value
    except Exception:
        raise BindingUnavailableError() from None


class Artifact(Closed):
    """Bounded qualification artifact; AUTH BPMN exceeds Q2's 64KiB read artifact cap."""

    artifact_ref: OpaqueRef
    digest: Sha256Digest
    bytes_base64: str = Field(repr=False)

    @model_validator(mode="after")
    def authentic_bytes(self) -> Artifact:
        artifact_bytes(self)
        return self


class Classification(Closed):
    contract: Artifact
    input_fields: tuple[OpaqueRef, ...]
    outcome_field: OpaqueRef
    phi_fields: tuple[OpaqueRef, ...]


class ConsumerQualificationFields(Closed):
    deployed_consumer: Artifact
    deployment_receipt: Artifact
    hydration_contract: Artifact
    custody_schema: Literal["phi-human-decision-custody.v1"]


class ConsumerQualification(ConsumerQualificationFields):
    mode: Literal["worker_hydration", "phi_case_reader"]


class ConsumerQualificationV2(ConsumerQualificationFields):
    mode: Literal["worker_hydration", "phi_case_reader", "classified_outcome_only"]


class BindingMaterial(Closed):
    entry: ReadCatalogEntry
    process: Artifact
    group_dmn: Artifact | None
    form: Artifact
    classification: Classification
    consumer: ConsumerQualification | ConsumerQualificationV2
    required_group: OpaqueRef

    @model_validator(mode="after")
    def supported_material(self) -> BindingMaterial:
        e = self.entry
        kind = _BINDINGS.get((e.process_definition_key, e.task_definition_key))
        if kind != e.form_key or kind is None:
            raise ValueError("decision binding unavailable")
        outcome = _OUTCOMES[kind][0]
        if (
            self.process.digest != e.process_definition_digest
            or self.process.artifact_ref != e.process_definition_id
            or self.form.digest != e.form_digest
            or self.form.artifact_ref != e.form_key
            or self.classification.input_fields != e.allowed_inputs
            or self.classification.outcome_field != outcome
            or self.classification.phi_fields != tuple(f for f in e.allowed_inputs if f != outcome)
            or self.required_group not in e.group_domain.groups
            or self.consumer.mode
            != (
                "classified_outcome_only"
                if kind == "auth_pendencia"
                else "phi_case_reader"
                if kind == "escalation"
                else "worker_hydration"
            )
        ):
            raise ValueError("incomplete decision classification")
        domain = e.group_domain
        if domain.kind == "static":
            if self.group_dmn is not None:
                raise ValueError("unexpected group DMN")
        elif (
            self.group_dmn is None
            or self.group_dmn.artifact_ref != domain.dmn_definition_id
            or self.group_dmn.digest != domain.dmn_resource_digest
        ):
            raise ValueError("missing deployed group DMN")
        return self


class BatchMember(Closed):
    process_definition_id: OpaqueRef
    process_definition_key: OpaqueRef
    task_definition_key: OpaqueRef
    material_digest: Sha256Digest


def batch_member(material: BindingMaterial) -> BatchMember:
    return BatchMember(
        process_definition_id=material.entry.process_definition_id,
        process_definition_key=material.entry.process_definition_key,
        task_definition_key=material.entry.task_definition_key,
        material_digest=sha(canonical(material)),
    )


class NativeCohortScope(Closed):
    tenant: OpaqueRef
    environment: OpaqueRef
    engine_name: OpaqueRef
    database_incarnation: OpaqueRef
    database_binding_digest: Sha256Digest


class CohortManifest(Closed):
    schema_: Literal["human-decision-cohort.v2"] = Field(alias="schema")
    scope: NativeCohortScope
    members: tuple[BatchMember, ...] = Field(min_length=1, max_length=43)

    @model_validator(mode="after")
    def supported(self) -> CohortManifest:
        keys = [
            (m.process_definition_key, m.task_definition_key, m.process_definition_id) for m in self.members
        ]
        if keys != sorted(keys) or len({k[:2] for k in keys}) != len(keys):
            raise ValueError("invalid cohort order")
        if any(k[:2] not in _BINDINGS for k in keys):
            raise ValueError("unsupported cohort member")
        return self

    @property
    def digest(self) -> str:
        return sha(canonicalize({"schema": "human-decision-cohort-hash.v2", "value": wire(self)}))


class ReceiptFields(Closed):
    purpose: Purpose
    scope: Scope
    installation_id: OpaqueRef
    authority_generation: Revision
    operation_id: OpaqueRef
    expected_tenant_revision: Revision
    issuer: OpaqueRef
    source_ref: OpaqueRef
    source_revision: Revision
    source_digest: Sha256Digest
    receipt_ref: OpaqueRef
    contract_ref: OpaqueRef
    contract_digest: Sha256Digest
    installed_authority_ref: OpaqueRef
    material_digest: Sha256Digest
    batch: tuple[BatchMember, ...]
    receipt_digests: tuple[Sha256Digest, ...]
    freeze_epoch: Revision
    observed_at: datetime
    valid_until: datetime


class Receipt(ReceiptFields):
    schema_: Literal["human-decision-qualification-receipt.v1"] = Field(alias="schema")
    batch: tuple[BatchMember, ...] = Field(min_length=1, max_length=6)


class ReceiptV2(ReceiptFields):
    schema_: Literal["human-decision-qualification-receipt.v2"] = Field(alias="schema")
    cohort_digest: Sha256Digest
    batch: tuple[BatchMember, ...] = Field(min_length=1, max_length=43)


class SignedReceiptV2(Closed):
    receipt: ReceiptV2
    signature: Signature


class SignedReceipt(Closed):
    receipt: Receipt
    signature: Signature


class QualificationPacket(Closed):
    material: BindingMaterial
    receipts: tuple[SignedReceipt, ...]
    freeze: SignedReceipt


class QualificationPacketV2(Closed):
    schema_: Literal["human-decision-qualification-packet.v2"] = Field(alias="schema")
    material: BindingMaterial
    receipts: tuple[SignedReceiptV2, ...]
    freeze: SignedReceiptV2
    cohort: CohortManifest


def decode_packet(raw: bytes) -> QualificationPacket | QualificationPacketV2:
    try:
        value = json.loads(raw)
        if type(value) is dict and "schema" in value:
            return decode(QualificationPacketV2, raw)
        return decode(QualificationPacket, raw)
    except Exception:
        raise BindingUnavailableError() from None


def packet_cohort(packet: QualificationPacket | QualificationPacketV2) -> str | None:
    return packet.cohort.digest if isinstance(packet, QualificationPacketV2) else None


def member_order(member: BatchMember, *, v2: bool) -> tuple[str, ...]:
    if v2:
        return (member.process_definition_key, member.task_definition_key, member.process_definition_id)
    return (member.process_definition_id, member.task_definition_key)


class Revocation(Closed):
    """Signed freeze authority instruction; it never creates a qualifying row."""

    receipt: Receipt
    signature: Signature


class VerifiedQualification(Closed):
    packet: QualificationPacket | QualificationPacketV2 = Field(repr=False)
    binding_digest: Sha256Digest
    authority_generation: Revision
    expected_tenant_revision: Revision
    operation_id: OpaqueRef
    valid_until: datetime


class QualificationVerifier:
    def __init__(self, root: RootDesignation) -> None:
        self.root = RootDesignation.model_validate(root)
        b64decode(root.public_key, size=32)

    def authorities(self, value: SignedAuthorities, now: datetime) -> AuthorityDocument:
        try:
            value = decode(SignedAuthorities, canonical(value))
            d, root = value.document, self.root
            current(root.observed_at, root.valid_until, now)
            current(d.observed_at, d.valid_until, now)
            if (
                value.signature.key_id != root.key_id
                or d.scope != root.scope
                or d.installation_id != root.installation_id
                or d.valid_until > root.valid_until
            ):
                raise BindingUnavailableError()
            if any(s.public_key == root.public_key for s in d.signers):
                raise BindingUnavailableError()
            verify_signature(canonical(d), value.signature, root.public_key)
            return d
        except Exception:
            raise BindingUnavailableError() from None

    def _receipt(
        self, signed: SignedReceipt | SignedReceiptV2, d: AuthorityDocument, now: datetime
    ) -> Receipt | ReceiptV2:
        r = signed.receipt
        matches = [s for s in d.signers if s.key_id == signed.signature.key_id]
        if len(matches) != 1:
            raise BindingUnavailableError()
        s = matches[0]
        current(s.observed_at, s.valid_until, now)
        current(r.observed_at, r.valid_until, now)
        if (
            (r.scope, r.installation_id, r.authority_generation) != (d.scope, d.installation_id, d.generation)
            or (r.issuer, r.purpose, r.contract_ref, r.contract_digest, r.installed_authority_ref)
            != (s.issuer, s.purpose, s.contract_ref, s.contract_digest, s.installed_authority_ref)
            or r.valid_until > min(d.valid_until, s.valid_until)
        ):
            raise BindingUnavailableError()
        if r.purpose == "freeze" and (
            r.contract_ref != FREEZE_CONTRACT
            or r.contract_digest != self.root.freeze_contract_digest
            or r.installed_authority_ref != self.root.freeze_installed_authority_ref
        ):
            raise BindingUnavailableError()
        verify_signature(canonical(r), signed.signature, s.public_key)
        return r

    def verify(
        self,
        packet: QualificationPacket | QualificationPacketV2,
        authorities: SignedAuthorities,
        now: datetime,
    ) -> VerifiedQualification:
        try:
            packet = decode_packet(canonical(packet))
            v2 = isinstance(packet, QualificationPacketV2)
            if (
                not v2
                and (packet.material.entry.process_definition_key, packet.material.entry.task_definition_key)
                not in _LEGACY_BINDINGS
            ):
                raise BindingUnavailableError()
            d = self.authorities(authorities, now)
            material_digest = sha(canonical(packet.material))
            receipts = [self._receipt(s, d, now) for s in packet.receipts]
            f = self._receipt(packet.freeze, d, now)
            if [r.purpose for r in receipts] != [
                "deployment",
                "classification",
                "consumer",
            ] or f.purpose != "freeze":
                raise BindingUnavailableError()
            source_digests = (
                sha(
                    canonical(packet.material.entry)
                    + canonical(packet.material.process)
                    + canonical(packet.material.form)
                ),
                sha(canonical(packet.material.classification)),
                sha(canonical(packet.material.consumer)),
            )
            for r, source_digest in zip(receipts, source_digests, strict=True):
                if r.source_digest != source_digest or r.receipt_digests:
                    raise BindingUnavailableError()
            if f.receipt_digests != tuple(
                sha(canonical(s)) for s in packet.receipts
            ) or f.source_digest != sha(canonicalize(list(f.receipt_digests))):
                raise BindingUnavailableError()
            members = f.batch
            targets = [member_order(m, v2=v2) for m in members]
            if (
                targets != sorted(targets)
                or len(set(targets)) != len(targets)
                or any(
                    (m.process_definition_key, m.task_definition_key)
                    not in (_BINDINGS if v2 else _LEGACY_BINDINGS)
                    for m in members
                )
                or sum(m == batch_member(packet.material) for m in members) != 1
            ):
                raise BindingUnavailableError()
            if isinstance(packet, QualificationPacketV2) and (
                (packet.cohort.scope.tenant, packet.cohort.scope.environment)
                != (d.scope.tenant, d.scope.environment)
                or self.root.freeze_contract_digest != COHORT_CONTRACT
                or any(r.contract_digest != COHORT_CONTRACT for r in (*receipts, f))
                or members != packet.cohort.members
                or any(
                    not isinstance(r, ReceiptV2) or r.cohort_digest != packet.cohort.digest
                    for r in (*receipts, f)
                )
            ):
                raise BindingUnavailableError()
            for r in (*receipts, f):
                if (
                    r.batch != members
                    or r.material_digest != material_digest
                    or (
                        r.operation_id,
                        r.expected_tenant_revision,
                        r.freeze_epoch,
                    )
                    != (f.operation_id, f.expected_tenant_revision, f.freeze_epoch)
                ):
                    raise BindingUnavailableError()
            return VerifiedQualification(
                packet=packet,
                binding_digest=sha(canonical(packet)),
                authority_generation=d.generation,
                expected_tenant_revision=f.expected_tenant_revision,
                operation_id=f.operation_id,
                valid_until=min(
                    self.root.valid_until, d.valid_until, *(r.valid_until for r in (*receipts, f))
                ),
            )
        except Exception:
            raise BindingUnavailableError() from None

    def revocation(self, value: Revocation, authorities: SignedAuthorities, now: datetime) -> Receipt:
        d = self.authorities(authorities, now)
        r = self._receipt(SignedReceipt(receipt=value.receipt, signature=value.signature), d, now)
        if (
            not isinstance(r, Receipt)
            or r.purpose != "freeze"
            or r.source_ref != "decision-binding-revocation"
            or r.receipt_digests
        ):
            raise BindingUnavailableError()
        if r.source_digest != r.material_digest:
            raise BindingUnavailableError()
        return r
