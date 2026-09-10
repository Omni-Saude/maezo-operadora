"""Frozen census delivery plan: original bytes, explicit CAS and durable custody."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Literal, Self
from uuid import uuid4

from pydantic import Field, model_validator

from maezo.gateway.external_cases.models import Digest, Ref, timestamp
from maezo.gateway.human.auth_profile import Actor
from maezo.gateway.human.read_profile import digest, parse_model, utc, wire
from maezo.portal.contracts.models import HumanPrincipal
from maezo.portal.engine.profile import canonicalize

from .authority import FIELDS, InstalledStaffAuthority
from .census_source import CensusCut, artifact_loads
from .models import (
    Closed,
    GrantEntry,
    MembershipWitness,
    Proof,
    Revoke,
    ScopeCheckpoint,
    ScopeChunk,
    ScopeChunkPin,
    StaffCaseError,
    StaffCaseGrant,
    StaffPublication,
)
from .publisher import StaffSigner

MAX_ENVELOPE = 65536
# Actual outer request fields/signature are bounded separately by the native client.
# Reserve their complete fixed envelope budget rather than filling the payload to MAX.
MAX_PUBLICATION = MAX_ENVELOPE - 4096


class PreparedCensus(Closed):
    schema_: Literal["staff-case-census-plan.v1"] = Field(alias="schema")
    plan_ref: Ref
    cut: CensusCut
    dependency_digest: Digest
    publications: tuple[StaffPublication, ...]
    proof: Proof

    @model_validator(mode="after")
    def exact_schedule(self) -> Self:
        validate_schedule(self.cut, self.publications)
        if self.proof.purpose != "scope_complete":
            raise StaffCaseError("invalid")
        return self


def validate_schedule(cut: CensusCut, publications: tuple[StaffPublication, ...]) -> None:
    if len({p.publication_id for p in publications}) != len(publications):
        raise StaffCaseError("invalid")
    originals = {m.request_digest: m for m in cut.materials}
    seen: set[str] = set()
    positions = {p.source_ref: int(p.expected_source_revision) for p in cut.source_positions}
    new_started = False
    barrier = cut.predecessor_material is None
    chunks: list[ScopeChunk] = []
    grants: dict[str, StaffPublication] = {}
    final: StaffPublication | None = None
    for publication in publications:
        raw = canonicalize(publication.wire())
        key = digest(publication.wire())
        if (
            len(raw) > MAX_PUBLICATION
            or publication.scope != cut.scope
            or publication.source_ref not in positions
        ):
            raise StaffCaseError("invalid")
        if publication.payload_digest != digest(publication.payload.wire()):
            raise StaffCaseError("invalid")
        material = originals.get(key)
        if material is not None:
            seen.add(key)
        prior = material is not None and material.position == "prior"
        if prior and new_started:
            raise StaffCaseError("invalid")
        if not prior:
            new_started = True
            if int(publication.expected_source_revision) != positions[publication.source_ref]:
                raise StaffCaseError("conflict")
            positions[publication.source_ref] = int(publication.source_revision)
        payload = publication.payload
        is_barrier = False
        if cut.predecessor_material is not None and isinstance(payload, Revoke):
            cp = originals[cut.predecessor_material.checkpoint_request_digest].publication.payload
            assert isinstance(cp, ScopeCheckpoint)
            is_barrier = (
                payload.target_kind == "scope_checkpoint"
                and payload.target_ref == cp.checkpoint_ref
                and payload.expected_revision == cp.generation
                and publication.source_ref == cut.source_ref
            )
        if not prior and not barrier and not is_barrier:
            raise StaffCaseError("invalid")
        if is_barrier:
            barrier = True
        if isinstance(payload, StaffCaseGrant):
            if payload.grant_ref in grants or payload not in cut.grants:
                raise StaffCaseError("invalid")
            grants[payload.grant_ref] = publication
        if material is None:
            if publication.kind == "case_grant":
                assert isinstance(payload, StaffCaseGrant)
                binding = next(b for b in cut.grant_bindings if b.grant_ref == payload.grant_ref)
                if binding.mode != "compile_new":
                    raise StaffCaseError("invalid")
            elif publication.kind == "scope_chunk":
                if (
                    not isinstance(payload, ScopeChunk)
                    or payload.checkpoint_ref != cut.checkpoint_ref
                    or payload.generation != cut.generation
                ):
                    raise StaffCaseError("invalid")
                chunks.append(payload)
            elif publication.kind == "scope_checkpoint":
                if final is not None or publication is not publications[-1]:
                    raise StaffCaseError("invalid")
                final = publication
            elif not (
                is_barrier
                and cut.predecessor_material is not None
                and cut.predecessor_material.revoke_request_digest is None
            ):
                raise StaffCaseError("invalid")
    if (
        seen != set(originals)
        or not barrier
        or final is None
        or set(grants) != {g.grant_ref for g in cut.grants}
    ):
        raise StaffCaseError("invalid")
    entries = [
        GrantEntry(
            case_ref=g.case_ref,
            identity_digest=g.identity_digest,
            grant_ref=g.grant_ref,
            grant_revision=g.grant_revision,
            grant_digest=digest(g.wire()),
            source_ref=grants[g.grant_ref].source_ref,
            source_revision=grants[g.grant_ref].source_revision,
        )
        for g in cut.grants
    ]
    if [e for c in chunks for e in c.entries] != entries or [int(c.chunk_index) for c in chunks] != list(
        range(len(chunks))
    ):
        raise StaffCaseError("invalid")
    cp = final.payload
    if not isinstance(cp, ScopeCheckpoint):
        raise StaffCaseError("invalid")
    if (
        cp.scope != cut.scope
        or cp.checkpoint_ref != cut.checkpoint_ref
        or cp.generation != cut.generation
        or cp.predecessor_digest != cut.predecessor_checkpoint_digest
        or cp.principal_identity_digest != digest(wire(cut.actor))
        or (cp.issuer, cp.subject, cp.principal_ref, cp.membership_revision)
        != (cut.actor.issuer, cut.actor.subject, cut.actor.principal_ref, str(cut.actor.membership_revision))
        or (cp.policy_scope_ref, cp.policy_scope_revision, cp.policy_scope_digest)
        != (cut.policy_scope.policy_ref, cut.policy_scope.policy_revision, cut.policy_scope.policy_digest)
        or cp.chunks
        != tuple(
            ScopeChunkPin(
                chunk_index=c.chunk_index, chunk_digest=digest(c.wire()), entry_count=str(len(c.entries))
            )
            for c in chunks
        )
        or int(cp.total_entries) != len(entries)
        or timestamp(cp.valid_until) > timestamp(cut.valid_until)
        or final.source_ref != cut.source_ref
    ):
        raise StaffCaseError("invalid")


def compile_plan(
    cut: CensusCut,
    *,
    principal: HumanPrincipal,
    witness: MembershipWitness,
    signer_for: Callable[[str, str], StaffSigner],
    authority: InstalledStaffAuthority,
    dependency_digest: str,
    now: datetime,
    session_until: datetime,
) -> PreparedCensus:
    if (
        cut.actor != Actor.from_principal(principal, "staff")
        or witness.actor != cut.actor
        or witness.session_ref != principal.session_ref
    ):
        raise StaffCaseError("denied")
    positions = {p.source_ref: int(p.expected_source_revision) for p in cut.source_positions}
    originals = {m.request_digest: m for m in cut.materials}
    result: list[StaffPublication] = []
    appended: set[str] = set()
    end = min(
        session_until,
        timestamp(cut.valid_until),
        timestamp(witness.valid_until),
        witness.source.valid_until,
        timestamp(cut.policy_scope.valid_until),
    )
    for grant in cut.grants:
        end = min(
            end,
            timestamp(grant.valid_until),
            *(timestamp(d.valid_until) for d in grant.decisions),
            *(timestamp(d.decision_proof.expires_at) for d in grant.decisions),
        )
    if not timestamp(cut.observed_at) <= now < end:
        raise StaffCaseError("unavailable")

    def original(key: str) -> None:
        if key in appended:
            return
        material = originals[key]
        publication = material.publication
        if material.position == "queued":
            if positions[publication.source_ref] != int(publication.expected_source_revision):
                raise StaffCaseError("conflict")
            positions[publication.source_ref] = int(publication.source_revision)
        result.append(publication)
        appended.add(key)

    for material in sorted(
        (m for m in cut.materials if m.position == "prior"),
        key=lambda m: (m.publication.source_ref, int(m.publication.source_revision)),
    ):
        original(material.request_digest)

    def make(
        payload: Closed, kind: str, source: str, *, publication_id: str | None = None
    ) -> StaffPublication:
        expected = positions[source]
        selected = signer_for(source, "staff_case_grant" if kind == "case_grant" else "scope_complete")
        limit = min(end, selected.until("staff_case_grant" if kind == "case_grant" else "scope_complete"))
        if isinstance(payload, StaffCaseGrant):
            if payload.source_revision != str(expected + 1) or payload.source_ref != source:
                raise StaffCaseError("conflict")
            limit = min(limit, timestamp(payload.valid_until))
        unsigned = dict(
            schema="staff-case-publication.v1",
            scope=wire(cut.scope),
            publication_id=publication_id or uuid4().hex,
            expected_source_revision=str(expected),
            source_ref=source,
            source_revision=str(expected + 1),
            kind=kind,
            membership_witness=wire(witness) if kind != "revoke" else None,
            payload=payload.wire(),
            payload_digest=digest(payload.wire()),
            observed_at=utc(now),
            valid_until=utc(limit),
        )
        proof_purpose = "staff_case_grant" if kind == "case_grant" else "scope_complete"
        unsigned["proof"] = wire(selected.proof(unsigned, proof_purpose, limit))
        publication = parse_model(StaffPublication, unsigned)
        if len(canonicalize(publication.wire())) > MAX_PUBLICATION:
            raise StaffCaseError("invalid")
        return publication

    def append(publication: StaffPublication) -> None:
        if int(publication.expected_source_revision) != positions[publication.source_ref]:
            raise StaffCaseError("conflict")
        result.append(publication)
        positions[publication.source_ref] = int(publication.source_revision)

    if cut.predecessor_material is not None:
        previous = cut.predecessor_material
        if previous.revoke_request_digest is not None:
            original(previous.revoke_request_digest)
        else:
            cp = originals[previous.checkpoint_request_digest].publication.payload
            assert isinstance(cp, ScopeCheckpoint)
            append(
                make(
                    Revoke(
                        target_kind="scope_checkpoint",
                        target_ref=cp.checkpoint_ref,
                        expected_revision=cp.generation,
                    ),
                    "revoke",
                    cut.source_ref,
                )
            )
    for binding in cut.policy_bindings:
        original(binding.request_digest)
    for binding in cut.withdrawal_bindings:
        original(binding.request_digest)
    bindings = {b.grant_ref: b for b in cut.grant_bindings}
    grant_pubs: dict[str, StaffPublication] = {}
    for grant in cut.grants:
        # Full decision signatures/policy namespace/field restrictions stay exact;
        # native publication still proves actual canonical case identity before write.
        for decision in grant.decisions:
            authority._decision(decision, grant, principal, now)
        summaries = [
            d
            for d in grant.decisions
            if d.operation == "list"
            and d.projection == "staff_summary.v1"
            and d.resource_identity_digest == grant.identity_digest
        ]
        if len(summaries) != 1 or frozenset(summaries[0].fields) != FIELDS["staff_summary.v1"]:
            raise StaffCaseError("denied")
        binding = bindings[grant.grant_ref]
        if binding.mode == "reuse_original":
            assert binding.request_digest is not None
            original(binding.request_digest)
            publication = originals[binding.request_digest].publication
        else:
            publication = make(grant, "case_grant", grant.source_ref)
            append(publication)
        grant_pubs[grant.grant_ref] = publication
    entries = [
        GrantEntry(
            case_ref=g.case_ref,
            identity_digest=g.identity_digest,
            grant_ref=g.grant_ref,
            grant_revision=g.grant_revision,
            grant_digest=digest(g.wire()),
            source_ref=g.source_ref,
            source_revision=grant_pubs[g.grant_ref].source_revision,
        )
        for g in cut.grants
    ]
    pins: list[ScopeChunkPin] = []
    while entries:
        # Bound the actual signed message, not just the entry count; shrinking does
        # not change any dispatched bytes (all preparation is effect-free).
        count = min(256, len(entries))
        while True:
            chunk = ScopeChunk(
                checkpoint_ref=cut.checkpoint_ref,
                generation=cut.generation,
                chunk_index=str(len(pins)),
                entries=tuple(entries[:count]),
            )
            try:
                publication = make(chunk, "scope_chunk", cut.source_ref)
                break
            except StaffCaseError as error:
                if error.code != "invalid" or count == 1:
                    raise
                count //= 2
        append(publication)
        pins.append(
            ScopeChunkPin(
                chunk_index=chunk.chunk_index, chunk_digest=digest(chunk.wire()), entry_count=str(count)
            )
        )
        del entries[:count]
    signer = signer_for(cut.source_ref, "scope_complete")
    end = min(end, signer.until("scope_complete"))
    value = dict(
        scope=wire(cut.scope),
        checkpoint_ref=cut.checkpoint_ref,
        generation=cut.generation,
        predecessor_digest=cut.predecessor_checkpoint_digest,
        principal_identity_digest=digest(wire(cut.actor)),
        issuer=cut.actor.issuer,
        subject=cut.actor.subject,
        principal_ref=cut.actor.principal_ref,
        membership_revision=str(cut.actor.membership_revision),
        kind="authorization",
        operations=["list"],
        chunks=[p.wire() for p in pins],
        total_entries=str(len(cut.grants)),
        policy_scope_ref=cut.policy_scope.policy_ref,
        policy_scope_revision=cut.policy_scope.policy_revision,
        policy_scope_digest=cut.policy_scope.policy_digest,
        observed_at=utc(now),
        valid_until=utc(end),
        coverage="complete",
    )
    value["proof"] = wire(signer.proof(value, "scope_complete", end))
    append(make(parse_model(ScopeCheckpoint, value), "scope_checkpoint", cut.source_ref))
    plan = dict(
        schema="staff-case-census-plan.v1",
        plan_ref=uuid4().hex,
        cut=cut.wire(),
        dependency_digest=dependency_digest,
        publications=[p.wire() for p in result],
    )
    plan["proof"] = wire(signer.proof(plan, "scope_complete", end))
    return parse_model(PreparedCensus, plan)


def directory(path: Path) -> int:
    if not path.is_absolute():
        raise StaffCaseError("invalid")
    # Walk every component with no-follow; a checked final basename alone is not enough.
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            if part in {".", ".."}:
                raise StaffCaseError("invalid")
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise StaffCaseError("unavailable")
        return fd
    except BaseException:
        os.close(fd)
        raise


def protected_read(path: Path, *, expected: str | None, maximum: int) -> bytes:
    fd = directory(path.parent)
    try:
        handle = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            info = os.fstat(handle)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) not in {0o400, 0o600}
                or not 0 < info.st_size <= maximum
            ):
                raise StaffCaseError("unavailable")
            with os.fdopen(handle, "rb", closefd=False) as stream:
                value = stream.read(maximum + 1)
            after = os.fstat(handle)
            unchanged = (
                "st_ino",
                "st_dev",
                "st_mode",
                "st_uid",
                "st_gid",
                "st_nlink",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
            )
            if len(value) != info.st_size or any(
                getattr(after, key) != getattr(info, key) for key in unchanged
            ):
                raise StaffCaseError("unavailable")
            if expected is not None and hashlib.sha256(value).hexdigest() != expected:
                raise StaffCaseError("unavailable")
            return value
        finally:
            os.close(handle)
    finally:
        os.close(fd)


def seal(plan: PreparedCensus, path: Path, *, maximum: int) -> str:
    raw = canonicalize(plan.wire())
    if len(raw) > maximum:
        raise StaffCaseError("invalid")
    fd = directory(path.parent)
    try:
        # O_EXCL is intentional: interrupted or uncertain files are retained for
        # exact custody investigation, never replaced by a new command plan.
        handle = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        try:
            with os.fdopen(handle, "wb", closefd=False) as stream:
                stream.write(raw)
                stream.flush()
            os.fsync(handle)
            os.fchmod(handle, 0o400)
            os.fsync(handle)
        finally:
            os.close(handle)
        os.fsync(fd)
    finally:
        os.close(fd)
    expected = hashlib.sha256(raw).hexdigest()
    protected_read(path, expected=expected, maximum=maximum)
    return expected


def read_plan(raw: bytes) -> PreparedCensus:
    value = artifact_loads(raw, 134217728)
    if canonicalize(value) != raw:
        raise StaffCaseError("invalid")
    return parse_model(PreparedCensus, value)
