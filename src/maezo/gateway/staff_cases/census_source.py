"""Owner-issued complete staff scope, CENSUS C1/C2; parsing never creates authority."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from maezo.gateway.external_cases.models import Digest, Ref, Scope, timestamp
from maezo.gateway.human.auth_profile import Actor
from maezo.gateway.human.read_profile import digest, parse_model
from maezo.portal.engine.profile import canonicalize

from .authority import InstalledStaffAuthority
from .models import (
    Closed,
    N,
    Proof,
    Revoke,
    ScopeCheckpoint,
    StaffCaseError,
    StaffCaseGrant,
    StaffPolicyHeadPublication,
    StaffPublication,
    T,
)


def artifact_loads(raw: bytes, maximum: int) -> Any:
    """Source artifacts may span many native envelopes; native parser stays64KiB."""
    if not 0 < len(raw) <= maximum:
        raise StaffCaseError("invalid")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise StaffCaseError("invalid")
            result[key] = value
        return result

    def number(value: str) -> Any:
        raise StaffCaseError("invalid")

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_int=number,
            parse_float=number,
            parse_constant=number,
        )
        if canonicalize(value) != raw:
            raise StaffCaseError("invalid")
        return value
    except (UnicodeError, ValueError, RecursionError):
        raise StaffCaseError("invalid") from None


class NativePublicationReceipt(Closed):
    schema_: Literal["staff-case-publication-receipt.v1"] = Field(alias="schema")
    publication_id: Ref
    request_digest: Digest
    scope: Scope
    source_ref: Ref
    source_revision: N
    payload_digest: Digest
    disposition: Literal["committed"]
    committed_at: T
    native_receipt_ref: Ref
    valid_until: T
    proof: Proof


def correlate(publication: StaffPublication, receipt: NativePublicationReceipt) -> None:
    if (
        receipt.publication_id != publication.publication_id
        or receipt.request_digest != digest(publication.wire())
        or receipt.scope != publication.scope
        or receipt.source_ref != publication.source_ref
        or receipt.source_revision != publication.source_revision
        or receipt.payload_digest != publication.payload_digest
        or receipt.proof.purpose != "native_result"
    ):
        raise StaffCaseError("conflict")


class OriginalMaterial(Closed):
    request_digest: Digest
    position: Literal["prior", "queued"]
    publication: StaffPublication
    receipt: NativePublicationReceipt | None

    @model_validator(mode="after")
    def exact(self) -> Self:
        if self.request_digest != digest(self.publication.wire()):
            raise StaffCaseError("invalid")
        if self.receipt is not None:
            correlate(self.publication, self.receipt)
        return self


class PolicyBinding(Closed):
    policy_ref: Ref
    request_digest: Digest


class GrantBinding(Closed):
    grant_ref: Ref
    mode: Literal["compile_new", "reuse_original"]
    request_digest: Digest | None


class WithdrawalBinding(Closed):
    target_kind: Literal["case_grant", "scope_checkpoint"]
    target_ref: Ref
    request_digest: Digest


class PredecessorMaterial(Closed):
    checkpoint_request_digest: Digest
    revoke_request_digest: Digest | None


class SourcePosition(Closed):
    source_ref: Ref
    expected_source_revision: N


class CensusCut(Closed):
    schema_: Literal["staff-case-census-source.v1"] = Field(alias="schema")
    scope: Scope
    source_ref: Ref
    cut_ref: Ref
    cut_revision: N
    predecessor_cut_digest: Digest | None
    actor: Actor
    policy_scope: StaffPolicyHeadPublication
    checkpoint_ref: Ref
    generation: N
    predecessor_checkpoint_digest: Digest | None
    source_positions: tuple[SourcePosition, ...]
    grants: tuple[StaffCaseGrant, ...]
    withdrawn_grants: tuple[Revoke, ...]
    coverage: Literal["complete"]
    kind: Literal["authorization"]
    operations: tuple[Literal["list"], ...]
    observed_at: T
    valid_until: T
    proof: Proof
    materials: tuple[OriginalMaterial, ...]
    policy_bindings: tuple[PolicyBinding, ...]
    grant_bindings: tuple[GrantBinding, ...]
    withdrawal_bindings: tuple[WithdrawalBinding, ...]
    predecessor_material: PredecessorMaterial | None

    @model_validator(mode="after")
    def closure(self) -> Self:
        ordered_unique([p.source_ref for p in self.source_positions])
        ordered_unique([m.request_digest for m in self.materials])
        ordered_unique([p.policy_ref for p in self.policy_bindings])
        ordered_unique([g.grant_ref for g in self.grant_bindings])
        ordered_unique([(g.case_ref, g.grant_ref) for g in self.grants])
        ordered_unique([(w.target_kind, w.target_ref) for w in self.withdrawal_bindings])
        if (
            self.operations != ("list",)
            or self.actor.audience != "staff"
            or self.proof.purpose != "scope_complete"
            or timestamp(self.observed_at) >= timestamp(self.valid_until)
            or len({g.case_ref for g in self.grants}) != len(self.grants)
            or len({m.publication.publication_id for m in self.materials}) != len(self.materials)
        ):
            raise StaffCaseError("invalid")
        materials = {m.request_digest: m for m in self.materials}
        used: set[str] = set()

        def select(ref: str) -> OriginalMaterial:
            if ref not in materials:
                raise StaffCaseError("invalid")
            used.add(ref)
            return materials[ref]

        heads: dict[str, StaffPolicyHeadPublication] = {}
        for binding in self.policy_bindings:
            publication = select(binding.request_digest).publication
            if publication.kind != "policy_head" or not isinstance(
                publication.payload, StaffPolicyHeadPublication
            ):
                raise StaffCaseError("invalid")
            head = publication.payload
            if (
                head.policy_ref != binding.policy_ref
                or head.scope != self.scope
                or head.source_ref != publication.source_ref
                or head.source_revision != publication.source_revision
                or head.state != "active"
            ):
                raise StaffCaseError("denied")
            heads[binding.policy_ref] = head
        required = {self.policy_scope.policy_ref} | {d.policy_ref for g in self.grants for d in g.decisions}
        if set(heads) != required or heads[self.policy_scope.policy_ref] != self.policy_scope:
            raise StaffCaseError("invalid")
        bindings = {b.grant_ref: b for b in self.grant_bindings}
        if set(bindings) != {g.grant_ref for g in self.grants}:
            raise StaffCaseError("invalid")
        for grant in self.grants:
            if (
                grant.scope != self.scope
                or grant.state != "active"
                or grant.audience != "staff"
                or (grant.issuer, grant.subject, grant.principal_ref, grant.membership_revision)
                != (
                    self.actor.issuer,
                    self.actor.subject,
                    self.actor.principal_ref,
                    str(self.actor.membership_revision),
                )
            ):
                raise StaffCaseError("denied")
            binding = bindings[grant.grant_ref]
            if binding.mode == "compile_new":
                if binding.request_digest is not None:
                    raise StaffCaseError("invalid")
            elif (
                binding.request_digest is None
                or select(binding.request_digest).publication.kind != "case_grant"
                or select(binding.request_digest).publication.payload != grant
            ):
                raise StaffCaseError("invalid")
            for decision in grant.decisions:
                head = heads[decision.policy_ref]
                if grant.source_ref != head.source_ref or (
                    decision.policy_revision,
                    decision.policy_digest,
                    decision.decision_proof.key_fingerprint,
                    decision.decision_proof.purpose,
                ) != (
                    head.policy_revision,
                    head.policy_digest,
                    head.decision_issuer_key_fingerprint,
                    head.decision_purpose,
                ):
                    raise StaffCaseError("denied")
        withdrawals = {(w.target_kind, w.target_ref): w for w in self.withdrawn_grants}
        if len(withdrawals) != len(self.withdrawn_grants) or set(withdrawals) != {
            (w.target_kind, w.target_ref) for w in self.withdrawal_bindings
        }:
            raise StaffCaseError("invalid")
        for binding in self.withdrawal_bindings:
            pub = select(binding.request_digest).publication
            if pub.kind != "revoke" or pub.payload != withdrawals[binding.target_kind, binding.target_ref]:
                raise StaffCaseError("invalid")
        prior = self.predecessor_material
        if (prior is None) != (self.predecessor_checkpoint_digest is None):
            raise StaffCaseError("invalid")
        if prior is not None:
            material = select(prior.checkpoint_request_digest)
            pub = material.publication
            cp = pub.payload
            if (
                material.position != "prior"
                or pub.kind != "scope_checkpoint"
                or not isinstance(cp, ScopeCheckpoint)
                or digest(cp.wire()) != self.predecessor_checkpoint_digest
                or int(self.generation) != int(cp.generation) + 1
                or cp.scope != self.scope
                or pub.source_ref != self.source_ref
                or (cp.issuer, cp.subject, cp.principal_ref, cp.membership_revision)
                != (
                    self.actor.issuer,
                    self.actor.subject,
                    self.actor.principal_ref,
                    str(self.actor.membership_revision),
                )
            ):
                raise StaffCaseError("conflict")
            if prior.revoke_request_digest is not None:
                revoked = select(prior.revoke_request_digest).publication
                expected = Revoke(
                    target_kind="scope_checkpoint",
                    target_ref=cp.checkpoint_ref,
                    expected_revision=cp.generation,
                )
                if (
                    revoked.kind != "revoke"
                    or revoked.source_ref != pub.source_ref
                    or revoked.payload != expected
                ):
                    raise StaffCaseError("invalid")
        if used != set(materials):
            raise StaffCaseError("invalid")
        streams = (
            {self.source_ref}
            | {g.source_ref for g in self.grants}
            | {m.publication.source_ref for m in self.materials}
        )
        positions = {p.source_ref: int(p.expected_source_revision) for p in self.source_positions}
        if set(positions) != streams:
            raise StaffCaseError("invalid")
        for material in self.materials:
            pub = material.publication
            if (
                pub.scope != self.scope
                or pub.payload_digest != digest(pub.payload.wire())
                or (material.position == "prior" and int(pub.source_revision) > positions[pub.source_ref])
            ):
                raise StaffCaseError("invalid")
        return self


def ordered_unique[V: (str, tuple[str, str])](values: list[V]) -> None:
    if values != sorted(set(values)):
        raise StaffCaseError("invalid")


def purpose(publication: StaffPublication) -> str:
    if publication.kind == "policy_head":
        return "staff_policy_head"
    if publication.kind in {"scope_chunk", "scope_checkpoint"} or (
        isinstance(publication.payload, Revoke) and publication.payload.target_kind == "scope_checkpoint"
    ):
        return "scope_complete"
    return "staff_case_grant"


def verify_publication(
    authority: InstalledStaffAuthority, publication: StaffPublication, now: datetime, *, historical: bool
) -> None:
    raw = publication.wire()
    raw.pop("proof")
    when = timestamp(publication.proof.issued_at) if historical else now
    if when > now or now >= authority.valid_until:
        raise StaffCaseError("unavailable")
    authority.proof(
        publication.proof,
        raw,
        role="case_issuer",
        purpose=purpose(publication),
        source_ref=publication.source_ref,
        now=when,
    )
    payload = publication.payload
    if isinstance(payload, (ScopeCheckpoint, StaffPolicyHeadPublication)):
        statement = payload.wire()
        statement.pop("proof")
        authority.proof(
            payload.proof,
            statement,
            role="case_issuer",
            purpose=purpose(publication),
            source_ref=publication.source_ref,
            now=when,
        )
        if isinstance(payload, StaffPolicyHeadPublication):
            entry = authority.entries[payload.proof.key_fingerprint]
            issuer = authority.entries.get(payload.decision_issuer_key_fingerprint)
            if (
                entry.source_namespace != payload.policy_ref
                or issuer is None
                or issuer.role != "case_issuer"
                or issuer.source_ref != payload.source_ref
                or issuer.source_namespace != payload.policy_ref
                or payload.decision_purpose not in issuer.purposes
            ):
                raise StaffCaseError("denied")


class OwnerManifestStaffCensusSource:
    def __init__(self, authority: InstalledStaffAuthority, *, maximum_bytes: int, maximum_records: int):
        if not 1 <= maximum_bytes <= 64 * 1024 * 1024 or not 1 <= maximum_records <= 100000:
            raise StaffCaseError("invalid")
        self.authority, self.maximum_bytes, self.maximum_records = authority, maximum_bytes, maximum_records

    def read(self, raw: bytes, expected_digest: str, *, now: datetime, current: bool = True) -> CensusCut:
        if not 0 < len(raw) <= self.maximum_bytes:
            raise StaffCaseError("invalid")
        value = artifact_loads(raw, self.maximum_bytes)
        if canonicalize(value) != raw or digest(value) != expected_digest:
            raise StaffCaseError("invalid")
        cut = parse_model(CensusCut, value)
        if sum(len(v) for v in (cut.grants, cut.materials, cut.withdrawn_grants)) > self.maximum_records:
            raise StaffCaseError("invalid")
        when = now if current else timestamp(cut.proof.issued_at)
        if when > now or now >= self.authority.valid_until or cut.scope != self.authority.designation.scope:
            raise StaffCaseError("unavailable")
        unsigned = cut.wire()
        unsigned.pop("proof")
        self.authority.proof(
            cut.proof,
            unsigned,
            role="case_issuer",
            purpose="scope_complete",
            source_ref=cut.source_ref,
            now=when,
        )
        if current and not timestamp(cut.observed_at) <= now < timestamp(cut.valid_until):
            raise StaffCaseError("unavailable")
        for material in cut.materials:
            verify_publication(
                self.authority,
                material.publication,
                now,
                historical=material.position == "prior" or not current,
            )
            if material.receipt is not None:
                receipt = material.receipt
                statement = receipt.wire()
                statement.pop("proof")
                at = timestamp(receipt.proof.issued_at)
                if at > now:
                    raise StaffCaseError("invalid")
                self.authority.proof(
                    receipt.proof, statement, role="native_result", purpose="native_result", now=at
                )
        return cut
