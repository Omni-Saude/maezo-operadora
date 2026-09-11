"""Pinned native producer-context transport and authentic acquisition handoff."""

from __future__ import annotations

import base64
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, NoReturn, SupportsIndex

import httpx
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from maezo.gateway.engine_lifetime import CallerSelection, LocalLifetimeOwner, ProfileSelectionKey
from maezo.gateway.human.read_profile import parse_model
from maezo.gateway.native_fetch.adapter import AcquiredInputs
from maezo.gateway.native_fetch.models import (
    FetchProfile,
    PreparedFetch,
    closed,
    command,
    decode,
    encode,
    identity,
    integer,
    ref,
    sha,
    token,
)
from maezo.gateway.native_fetch.models import digest as native_digest
from maezo.gateway.native_fetch.transport import AdmissionLease, NativeAdmissionProvider, NativeTLS

from .models import ProducerContext, require


def milliseconds(value: datetime) -> int:
    delta = value - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000


class NativeChannel:
    """One owned lifetime; original admission/TLS ceilings checked after awaited cleanup."""

    def __init__(
        self,
        tls: NativeTLS,
        selection: bytes,
        profiles: tuple[tuple[bytes, str], ...],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        require(type(tls) is NativeTLS, "unavailable")
        self.tls, self.clock = tls, clock
        self.selection_digest = sha(selection)
        self.owner = LocalLifetimeOwner(
            CallerSelection(
                tls.identity_document,
                self.selection_digest,
                tuple(ProfileSelectionKey(raw, hashed, self.selection_digest) for raw, hashed in profiles),
            )
        )
        self.borrower = self.owner.borrow(tuple(hashed for _, hashed in profiles))

    def guard(self, lease: AdmissionLease, binding: bytes, purpose: str) -> None:
        self.borrower.check()
        lease.guard(self.clock)
        require(
            lease.selection_digest == self.selection_digest
            and lease.binding == binding
            and lease.purpose == purpose
        )

    async def close(self, timeout: float | None = None) -> bool:  # noqa: ASYNC109
        return await self.owner.close(timeout)

    async def exchange(self, route: str, raw: bytes, current: Callable[[], None]) -> tuple[int, bytes]:
        require(
            route
            in {
                "/maezo-workload/v2/auth-document-request-context",
                "/maezo-workload/v2/operations",
                "/maezo-workload/v2/outcomes",
            }
        )
        current()
        tls, client_until = self.tls.material(self.clock())
        peer_until = None

        def check() -> None:
            self.borrower.check()
            current()
            require(self.clock() < client_until and (peer_until is None or self.clock() < peer_until))

        check()
        async with httpx.AsyncClient(
            verify=tls, trust_env=False, follow_redirects=False, timeout=30
        ) as client:
            check()
            async with client.stream(
                "POST",
                self.tls.origin + route,
                content=raw,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
            ) as response:
                check()
                require(
                    response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    == "application/json"
                )
                stream = response.extensions.get("network_stream")
                ssl = stream.get_extra_info("ssl_object") if stream is not None else None
                require(ssl is not None)
                assert ssl is not None
                peer = x509.load_der_x509_certificate(ssl.getpeercert(binary_form=True))
                peer_until = peer.not_valid_after_utc
                require(
                    peer.not_valid_before_utc <= self.clock() < peer_until
                    and sha(peer.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo))
                    == self.tls.server_spki_sha256
                )
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    check()
                    require(len(data) + len(chunk) <= 1_048_576)
                    data.extend(chunk)
                result = response.status_code, bytes(data)
                check()
            check()
        check()
        return result


@dataclass(frozen=True, slots=True, repr=False)
class ProducerObservation:
    query: bytes = field(repr=False)
    result: bytes = field(repr=False)
    context: ProducerContext = field(repr=False)
    valid_until: int
    current: Callable[[], None] = field(repr=False)


class ProducerAcquisition:
    __slots__ = ("expected", "receipt", "row", "snapshot", "current")
    expected: PreparedFetch
    receipt: bytes
    row: bytes
    snapshot: bytes
    current: Callable[[], None]

    def __init__(self, acquired: AcquiredInputs) -> None:
        require(type(acquired) is AcquiredInputs and not acquired._consumed)
        acquired._guard()
        require(acquired._expected.profile.input_profile == "portal-auth-intake.v1")
        object.__setattr__(acquired, "_consumed", True)
        object.__setattr__(self, "expected", acquired._expected)
        object.__setattr__(self, "receipt", acquired.technical_receipt)
        object.__setattr__(self, "row", acquired._row)
        object.__setattr__(self, "snapshot", acquired._snapshot)
        object.__setattr__(self, "current", acquired._guard)

    def __setattr__(self, name: str, value: Any) -> NoReturn:
        raise TypeError("immutable acquisition")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        raise TypeError("local acquisition")


class ProducerContextClient:
    def __init__(
        self,
        tls: NativeTLS,
        authority: NativeAdmissionProvider,
        profile: FetchProfile,
        designation_digest: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        profile.__post_init__()
        native_digest(designation_digest)
        require(profile.input_profile == "portal-auth-intake.v1")
        require(identity(decode(tls.identity_document)) == profile.value()["identity"])
        require(isinstance(authority, NativeAdmissionProvider), "unavailable")
        self.profile, self.designation_digest, self.authority = profile, designation_digest, authority
        selection = encode(
            dict(
                schema="maezo.auth-document-producer-selection.v1",
                tls_selection_digest=tls.selection_digest,
                fetch_profile_digest=profile.digest,
                designation_digest=designation_digest,
            )
        )
        self.channel = NativeChannel(tls, selection, ((profile.document, profile.digest),), clock)

    async def observe(self, acquired: ProducerAcquisition) -> ProducerObservation:
        require(type(acquired) is ProducerAcquisition and acquired.expected.profile == self.profile)
        expected: PreparedFetch = acquired.expected

        async def perform() -> ProducerObservation:
            acquired.current()
            lease = await self.authority.acquire(self.channel.selection_digest, "fetch", expected.binding)

            def current() -> None:
                acquired.current()
                self.channel.guard(lease, expected.binding, "fetch")
                bound = command(decode(expected.binding))
                require(
                    lease.capability_digest == self.profile.digest
                    and self.profile.digest in lease.capabilities
                    and lease.activation_ref == bound["activation_ref"]
                    and lease.database_incarnation == bound["database_incarnation"]
                )

            current()
            snapshot, row = decode(acquired.snapshot), decode(acquired.row)
            query = dict(
                protocol="maezo.auth-document-producer-query.v1",
                designation_digest=self.designation_digest,
                query_id=base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode(),
                reader_activation_ref=lease.activation_ref,
                fetch_command=decode(expected.binding),
                resource_ref=row["id"],
                resource_acquisition={
                    "acquisition_ref": snapshot["acquisition_ref"],
                    "lease_revision": snapshot["lease_revision"],
                },
            )
            raw = encode(query)
            status, result = await self.channel.exchange(
                "/maezo-workload/v2/auth-document-request-context", raw, current
            )
            current()
            value = closed(
                decode(result),
                "protocol designation_digest query_id query_digest reader_activation_ref "
                "fetch_command resource_ref resource_acquisition observed_at valid_until context",
            )
            require(
                status == 200
                and value["protocol"] == "maezo.auth-document-producer-result.v1"
                and value["query_digest"] == sha(raw)
                and all(value[k] == v for k, v in query.items() if k != "protocol")
            )
            ref(value["query_id"])
            token(value["reader_activation_ref"])
            observed, until = integer(value["observed_at"], 0), integer(value["valid_until"], 0)
            require(
                observed < until <= snapshot["lock_expires_at"] and milliseconds(self.channel.clock()) < until
            )
            context = parse_model(ProducerContext, value["context"])
            require(
                context.producer_external_task_id == row["id"]
                and context.process_instance_id == row["process_instance_id"]
                and context.creator_execution_id == row["execution_id"]
                and context.definition.definition_id == row["definition_id"]
            )

            def final() -> None:
                current()
                require(milliseconds(self.channel.clock()) < until)

            final()
            return ProducerObservation(raw, result, context, until, final)

        observation = await self.channel.borrower.run_async(perform)
        observation.current()
        return observation
