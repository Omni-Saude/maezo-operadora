"""VW1-P0 vendor membership-to-access publication job; never a bootstrap-registered worker.

Publishes the access projection of vendor membership records enumerated from the migration-0019
vendor channel authority store. The durable ledger mirrors `membership_publication_job.py`
`PublicationLedger` (PW1-B: read as a molde, never edited): one JSON file, replaced atomically
(tmp + fsync + rename, mode 0600), so a second run with nothing changed publishes nothing.

Fail-closed by construction ("ausente = unknown ≠ zero"): a store that is absent or EMPTY is
UNKNOWN and refuses (`unavailable()`), never an empty set to iterate; a channel whose current
membership record cannot be read is UNKNOWN and refuses, never a deletion to publish. Nothing
here fabricates an answer for a source that did not answer. The retained surfaces stay honest
refusals: vendor comms delivery has no source in this wave (the comms recipient literal excludes
`vendor`, PW1-C fence), and no worker/bootstrap registers this job — cadence belongs to the
deployment that later adopts it.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from datetime import datetime as _datetime
from pathlib import Path
from typing import Literal, NoReturn, Protocol

from pydantic import Field

from maezo.gateway.human.models import Closed
from maezo.gateway.human.read_credentials import unavailable
from maezo.gateway.human.read_profile import parse_model, wire
from maezo.gateway.human.vendor_membership_administration import VendorChannelState
from maezo.portal.api.records import MembershipRecord
from maezo.portal.contracts.models import MembershipBinding, OpaqueRef, Revision, Sha256Digest, SubjectBinding
from maezo.portal.engine.profile import canonicalize

Clock = Callable[[], datetime]


def _now() -> datetime:
    return datetime.now(UTC)


class VendorMembershipAccessPayload(Closed):
    """The access projection published for one vendor record: the human-envelope wire subset
    (revisions as strings, `state` closed to active/revoked — the revoke rides INSIDE the
    payload, never as a side-band deletion). Mirrors `read_profile.MembershipProjection` plus
    the tenant/issuer identity the access plane needs to file the record."""

    tenant: OpaqueRef
    issuer: str
    subject: OpaqueRef
    principal_ref: OpaqueRef
    membership_revision: Revision
    audience: Literal["staff", "beneficiary", "provider", "vendor"]
    memberships: tuple[MembershipBinding, ...]
    subject_bindings: tuple[SubjectBinding, ...]
    state: Literal["active", "revoked"]
    reviewed_until: _datetime


def access_payload(record: MembershipRecord) -> bytes:
    """Canonical wire bytes of the record's access projection (deterministic, content-addressed)."""
    return canonicalize(
        wire(
            dict(
                tenant=record.tenant,
                issuer=record.issuer,
                subject=record.subject,
                principal_ref=record.principal_ref,
                membership_revision=record.revision,
                audience=record.audience,
                memberships=record.memberships,
                subject_bindings=record.subject_bindings,
                state="revoked" if record.revoked else "active",
                reviewed_until=record.reviewed_until,
            )
        )
    )


def payload_digest(record: MembershipRecord) -> str:
    return hashlib.sha256(access_payload(record)).hexdigest()


class RefusingVendorDeliverySource:
    """Vendor comms delivery is RETAINED in this wave: the comms recipient literal excludes
    `vendor` (PW1-C fence), so there is no source to answer from. Absent source = unknown:
    an honest typed refusal, never a silent drop and never a stub that answers."""

    async def read(self, channel_ref: str) -> NoReturn:
        raise unavailable()


class VendorLedgerEntry(Closed):
    kind: Literal["membership"]
    channel_ref: OpaqueRef
    principal_ref: OpaqueRef
    membership_revision: Revision
    payload_digest: Sha256Digest
    revoked: bool


class VendorLedgerState(Closed):
    schema_: Literal["vendor-membership-publication-ledger.v1"] = Field(alias="schema")
    tenant: OpaqueRef
    entries: tuple[VendorLedgerEntry, ...]


class VendorPublicationLedger:
    """Durable JSON ledger, replaced atomically (tmp + fsync + rename), mode 0600."""

    def __init__(self, path: Path, tenant: str) -> None:
        self.path, self.tenant = Path(path), tenant
        try:
            if self.path.exists():
                if not stat.S_ISREG(self.path.stat().st_mode):
                    raise ValueError
                value = json.loads(self.path.read_bytes())
            else:
                value = {
                    "schema": "vendor-membership-publication-ledger.v1",
                    "tenant": tenant,
                    "entries": [],
                }
            state = parse_model(VendorLedgerState, value)
        except Exception:
            raise unavailable() from None
        if state.tenant != tenant:
            raise unavailable()
        self.state = state

    def entry(self, channel_ref: str) -> VendorLedgerEntry | None:
        return next((e for e in self.state.entries if e.channel_ref == channel_ref), None)

    def has(self, channel_ref: str, principal_ref: str, revision: int, digest: str, revoked: bool) -> bool:
        e = self.entry(channel_ref)
        return e is not None and (
            e.principal_ref,
            e.membership_revision,
            e.payload_digest,
            e.revoked,
        ) == (principal_ref, revision, digest, revoked)

    def record(self, entry: VendorLedgerEntry) -> None:
        kept = tuple(e for e in self.state.entries if e.channel_ref != entry.channel_ref)
        self._write(self.state.model_copy(update={"entries": (*kept, entry)}))

    def _write(self, state: VendorLedgerState) -> None:
        raw = canonicalize(wire(state))
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".vendor-ledger-")
        try:
            os.chmod(tmp, 0o600)
            with os.fdopen(fd, "wb") as out:
                out.write(raw)
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        self.state = state


class VendorChannelReader(Protocol):
    """The migration-0019 store view. `None` is an ABSENT store (unknown), never zero rows."""

    async def channels(self, tenant: str) -> tuple[VendorChannelState, ...] | None: ...


class VendorMembershipReader(Protocol):
    """Current vendor membership bound to a channel; `None` is UNKNOWN, never a deletion."""

    async def membership(self, tenant: str, channel_ref: str) -> MembershipRecord | None: ...


class VendorAccessPublisher(Protocol):
    """Carries one canonical record payload to the access plane; outcome unknown refuses."""

    async def publish(self, payload: bytes) -> None: ...


@dataclass(frozen=True, slots=True)
class VendorJobResult:
    published: int
    unchanged: int


class VendorMembershipPublicationJob:
    """Every vendor channel whose record is not committed in this exact state gets published.

    No worker/bootstrap registers this class in this wave; it is driven by the deployment that
    adopts the vendor plane (VW1-P4 onwards).
    """

    def __init__(
        self,
        *,
        channels: VendorChannelReader,
        memberships: VendorMembershipReader,
        publisher: VendorAccessPublisher,
        ledger: VendorPublicationLedger,
        clock: Clock | None = None,
    ) -> None:
        self._channels, self._memberships = channels, memberships
        self._publisher, self._ledger = publisher, ledger
        self._clock = clock or _now

    async def run(self) -> VendorJobResult:
        ledger = self._ledger
        rows = await self._channels.channels(ledger.tenant)
        # Absent store AND empty store are UNKNOWN (the source did not vouch for zero): refuse.
        if rows is None or not rows:
            raise unavailable()
        published = unchanged = 0
        for row in sorted(rows, key=lambda r: (r.channel_ref, r.revision)):
            record = await self._memberships.membership(ledger.tenant, row.channel_ref)
            if record is None or record.audience != "vendor":
                raise unavailable()  # unknown ≠ zero: never publish a deletion for an absent record
            digest = payload_digest(record)
            if ledger.has(row.channel_ref, record.principal_ref, record.revision, digest, record.revoked):
                unchanged += 1
                continue
            await self._publisher.publish(access_payload(record))
            ledger.record(
                VendorLedgerEntry(
                    kind="membership",
                    channel_ref=row.channel_ref,
                    principal_ref=record.principal_ref,
                    membership_revision=record.revision,
                    payload_digest=digest,
                    revoked=record.revoked,
                )
            )
            published += 1
        return VendorJobResult(published, unchanged)
