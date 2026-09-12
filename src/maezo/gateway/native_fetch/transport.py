"""Fixed native v2 mTLS transport, current D admission and retained local I/O.

Trace: native acquisition v2 §4–7; C third-repair local lifetime; D allocation.
The authority provider must authenticate D/controller/database admission; a value
lease or successful readiness response alone is never that provider.
"""

from __future__ import annotations

import ssl
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, SupportsIndex
from urllib.parse import urlsplit

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from maezo.gateway.engine_lifetime import CallerSelection, LocalLifetimeOwner, ProfileSelectionKey

from .models import (
    FetchProfile,
    FetchResult,
    FetchUnavailableError,
    PreparedFetch,
    closed,
    decode,
    digest,
    encode,
    identity,
    outcome,
    outcome_query,
    require,
    result,
    sha,
    token,
)


@dataclass(frozen=True, slots=True, repr=False)
class AdmissionLease:
    """An authenticated provider's immutable ceiling, guarded after every I/O.

    live must revalidate its retained authenticated observations and revocation;
    it may not renew these captured deadlines or replace the selected identity.
    Recovery purpose binds this exact historical command independently.
    """

    purpose: str
    selection_digest: str
    binding: bytes = field(repr=False)
    capability_digest: str
    activation_ref: str
    database_incarnation: str
    policy_digest: str
    schema_digest: str
    capabilities: tuple[str, ...]
    not_before: datetime
    valid_until: datetime
    live: Callable[[], None] = field(repr=False)

    def guard(self, clock: Callable[[], datetime]) -> None:
        self.live()
        require(self.purpose in {"fetch", "outcome"})
        for value in (self.selection_digest, self.capability_digest, self.policy_digest, self.schema_digest):
            digest(value)
        token(self.activation_ref)
        token(self.database_incarnation)
        require(type(self.capabilities) is tuple and (bool(self.capabilities) or self.purpose == "outcome"))
        require(tuple(sorted(set(self.capabilities))) == self.capabilities)
        for value in self.capabilities:
            digest(value)
        require(self.not_before.tzinfo is not None and self.valid_until.tzinfo is not None)
        require(self.not_before <= clock() < self.valid_until)


class NativeAdmissionProvider(ABC):
    @abstractmethod
    async def acquire(self, selection_digest: str, purpose: str, command_binding: bytes) -> AdmissionLease:
        """Current authenticated D ACTIVE and DB admission, exact peer and scope.

        Outcome acquisition also verifies the separately installed recovery
        capability admits this original command, including historical identity.
        Unavailable observations must raise; no production default is supplied.
        """
        raise NotImplementedError


@dataclass(frozen=True, slots=True, repr=False)
class NativeTLS:
    origin: str
    identity_document: bytes = field(repr=False)
    ca_file: Path = field(repr=False)
    ca_sha256: str
    certificate_file: Path = field(repr=False)
    certificate_sha256: str
    private_key_file: Path = field(repr=False)
    server_spki_sha256: str

    @property
    def selection_digest(self) -> str:
        return sha(
            encode(
                dict(
                    origin=self.origin,
                    identity=decode(self.identity_document),
                    ca_sha256=self.ca_sha256,
                    certificate_sha256=self.certificate_sha256,
                    server_spki_sha256=self.server_spki_sha256,
                )
            )
        )

    def material(self, now: datetime) -> tuple[ssl.SSLContext, datetime]:
        try:
            url = urlsplit(self.origin)
            require(url.scheme == "https" and bool(url.hostname) and url.port is not None)
            require(url.username is url.password is None and not url.query and not url.fragment)
            require(not url.path and self.origin == f"https://{url.netloc}")
            ident = identity(decode(self.identity_document))
            require(encode(ident) == self.identity_document)
            for value in (self.ca_sha256, self.certificate_sha256, self.server_spki_sha256):
                digest(value)
            for path in (self.ca_file, self.certificate_file, self.private_key_file):
                require(path.is_absolute() and path.resolve(strict=True) == path and path.is_file())
            ca, certificate, key = (
                self.ca_file.read_bytes(),
                self.certificate_file.read_bytes(),
                self.private_key_file.read_bytes(),
            )
            cert = x509.load_pem_x509_certificate(certificate)
            sans = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
            require(
                sha(ca) == self.ca_sha256
                and cert.fingerprint(hashes.SHA256()).hex() == self.certificate_sha256
            )
            require(cert.not_valid_before_utc <= now < cert.not_valid_after_utc)
            require(
                cert.issuer.rfc4514_string() == ident["issuer"] and ident["subject"].startswith("spiffe://")
            )
            require(sans.get_values_for_type(x509.UniformResourceIdentifier) == [ident["subject"]])
            require(x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH in eku)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_verify_locations(cadata=ca.decode("ascii"))
            with (
                tempfile.TemporaryDirectory(prefix="maezo-native-tls-") as directory,
                tempfile.NamedTemporaryFile(dir=directory) as cert_file,
                tempfile.NamedTemporaryFile(dir=directory) as key_file,
            ):
                cert_file.write(certificate)
                cert_file.flush()
                key_file.write(key)
                key_file.flush()
                context.load_cert_chain(cert_file.name, key_file.name)
            return context, cert.not_valid_after_utc
        except Exception:
            raise FetchUnavailableError() from None


@dataclass(frozen=True, slots=True, repr=False)
class Attempt:
    """Technical local record only; no restart reconciliation claim."""

    command_binding: bytes
    state: str
    receipt: bytes | None = None


class NativeFetchClient:
    def __init__(
        self,
        tls: NativeTLS,
        profiles: tuple[FetchProfile, ...],
        authority: NativeAdmissionProvider,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        require(type(tls) is NativeTLS and type(profiles) is tuple and bool(profiles))
        require(isinstance(authority, NativeAdmissionProvider))
        require(len({p.digest for p in profiles}) == len(profiles))
        for profile in profiles:
            profile.__post_init__()
            require(encode(profile.value()["identity"]) == tls.identity_document)
        self._tls, self._profiles, self._authority, self._clock = tls, profiles, authority, clock
        self._selection_document = encode(
            {
                "tls_selection_digest": tls.selection_digest,
                "profiles": [
                    {
                        "document": p.value(),
                        "classifications": [[name, list(tags)] for name, tags in p.classifications],
                        "input_profile": p.input_profile,
                    }
                    for p in profiles
                ],
            }
        )
        self._selection_digest = sha(self._selection_document)
        selection = CallerSelection(
            tls.identity_document,
            self._selection_digest,
            tuple(ProfileSelectionKey(p.document, p.digest, self._selection_digest) for p in profiles),
        )
        self._lifetime = LocalLifetimeOwner(selection)
        self._borrower = self._lifetime.borrow(tuple(p.digest for p in profiles))
        self._attempts: dict[str, Attempt] = {}
        self._results: dict[str, tuple[FetchResult, AdmissionLease]] = {}
        self._blocked = False

    def __reduce_ex__(self, protocol: SupportsIndex) -> Any:
        raise FetchUnavailableError()

    def __copy__(self) -> Any:
        raise FetchUnavailableError()

    def __deepcopy__(self, memo: Any) -> Any:
        raise FetchUnavailableError()

    @property
    def selection_document(self) -> bytes:
        """Exact immutable selection D must authenticate, including classifications."""
        return self._selection_document

    @property
    def attempts(self) -> tuple[Attempt, ...]:
        return tuple(self._attempts.values())

    async def close(self, timeout: float | None = None) -> bool:  # noqa: ASYNC109
        self._blocked = True
        return await self._lifetime.close(timeout)

    def _guard(self, lease: AdmissionLease, expected: PreparedFetch, purpose: str) -> None:
        try:
            self._guard_current(lease, expected, purpose)
        except Exception:
            raise FetchUnavailableError() from None

    def _guard_current(self, lease: AdmissionLease, expected: PreparedFetch, purpose: str) -> None:
        self._borrower.check()
        lease.guard(self._clock)
        require(lease.selection_digest == self._selection_digest and lease.binding == expected.binding)
        require(lease.purpose == purpose)
        if purpose == "fetch":
            bound = decode(expected.binding)
            require(
                lease.capability_digest == expected.profile.digest
                and lease.capability_digest in lease.capabilities
            )
            require(
                lease.activation_ref == bound["activation_ref"]
                and lease.database_incarnation == bound["database_incarnation"]
            )

    async def _exchange(
        self, method: str, route: str, body: bytes | None, guard: Callable[[], None]
    ) -> tuple[int, bytes]:
        require(
            (method, route)
            in {
                ("GET", "/maezo-workload/v2/readiness"),
                ("POST", "/maezo-workload/v2/operations"),
                ("POST", "/maezo-workload/v2/outcomes"),
            }
        )
        guard()
        context, certificate_until = self._tls.material(self._clock())
        peer_until: datetime | None = None

        def current() -> None:
            guard()
            require(self._clock() < certificate_until)
            if peer_until is not None:
                require(self._clock() < peer_until)

        current()
        async with httpx.AsyncClient(
            verify=context, trust_env=False, follow_redirects=False, timeout=30
        ) as client:
            current()
            async with client.stream(
                method,
                self._tls.origin + route,
                content=body,
                headers={
                    "Accept": "application/json",
                    **({"Content-Type": "application/json"} if body is not None else {}),
                },
            ) as response:
                current()
                require(
                    response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    == "application/json"
                )
                stream = response.extensions.get("network_stream")
                ssl_object = stream.get_extra_info("ssl_object") if stream is not None else None
                require(ssl_object is not None)
                assert ssl_object is not None
                peer = x509.load_der_x509_certificate(ssl_object.getpeercert(binary_form=True))
                peer_until = peer.not_valid_after_utc
                require(peer.not_valid_before_utc <= self._clock() < peer_until)
                public = peer.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
                require(sha(public) == self._tls.server_spki_sha256)
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    current()
                    require(len(data) + len(chunk) <= 1_048_576)
                    data.extend(chunk)
                status, frozen = response.status_code, bytes(data)
                current()
            current()
        current()
        return status, frozen

    async def _readiness(self, lease: AdmissionLease, guard: Callable[[], None]) -> None:
        status, raw = await self._exchange("GET", "/maezo-workload/v2/readiness", None, guard)
        guard()
        ready = closed(
            decode(raw),
            "protocol ready policy_digest config_digest schema_digest capabilities "
            "activation_ref database_incarnation purpose",
        )
        require(
            status == 200
            and ready
            == dict(
                protocol="maezo.engine-readiness.v2",
                ready=True,
                policy_digest=lease.policy_digest,
                config_digest=lease.policy_digest,
                schema_digest=lease.schema_digest,
                capabilities=list(lease.capabilities),
                activation_ref=lease.activation_ref,
                database_incarnation=lease.database_incarnation,
                purpose="runtime",
            )
        )
        require(ready["ready"] is True)
        guard()

    async def fetch(self, expected: PreparedFetch) -> FetchResult:
        """At most one send per local command, no retry even after not_observed.

        Callers needing restart handling must persist the immutable command and
        technical result externally before invoking this transport.
        """
        expected.__post_init__()
        require(not self._blocked and expected.profile in self._profiles)
        key = decode(expected.binding)["command_id"]
        require(key not in self._attempts)
        self._attempts[key] = Attempt(expected.binding, "prepared")

        async def perform() -> tuple[FetchResult, AdmissionLease]:
            lease = await self._authority.acquire(self._selection_digest, "fetch", expected.binding)

            def guard() -> None:
                self._guard(lease, expected, "fetch")

            guard()
            await self._readiness(lease, guard)
            guard()
            self._attempts[key] = Attempt(expected.binding, "possibly_sent")
            status, raw = await self._exchange("POST", "/maezo-workload/v2/operations", expected.body, guard)
            guard()
            parsed = result(raw, status, expected)
            guard()
            self._attempts[key] = Attempt(expected.binding, parsed.status, parsed.receipt)
            if parsed.status == "executed":
                self._results[key] = (parsed, lease)
            if parsed.status == "unavailable":
                self._blocked = True
            return parsed, lease

        try:
            parsed, lease = await self._borrower.run_async(perform)
            # Terminal delivery itself awaits. Recheck this exact original lease,
            # without renewing it or changing the already-accounted remote result.
            self._guard(lease, expected, "fetch")
            return parsed
        except BaseException as exc:
            old = self._attempts[key]
            if old.state == "possibly_sent":
                self._attempts[key] = Attempt(expected.binding, "ambiguous")
            self._blocked = True
            if isinstance(exc, Exception):
                raise FetchUnavailableError() from None
            raise

    def claim(self, expected: PreparedFetch, initial: FetchResult) -> Callable[[], None]:
        """Transfer one authentic first projection to its in-process adapter.

        Technical receipts (including duplicate/recovery) cannot reopen inputs.
        The guard checks the original admission ceiling, never a renewed lease.
        """
        key = decode(expected.binding)["command_id"]
        found = self._results.get(key)
        require(found is not None)
        assert found is not None
        parsed, lease = found
        require(parsed is initial and initial.status == "executed")
        self._guard(lease, expected, "fetch")
        del self._results[key]

        def current() -> None:
            self._guard(lease, expected, "fetch")
            require(not self._blocked)

        current()
        return current

    async def recover(self, expected: PreparedFetch) -> FetchResult:
        """Receipt-only read; never clears ambiguity, retries, or returns task inputs."""
        expected.__post_init__()

        async def perform() -> tuple[FetchResult, AdmissionLease]:
            lease = await self._authority.acquire(self._selection_digest, "outcome", expected.binding)

            def guard() -> None:
                self._guard(lease, expected, "outcome")

            guard()
            # A recovery-only reader need not possess the original operation's
            # readiness capabilities. Its dedicated native outcome gate is decisive.
            query = outcome_query(expected, lease.capability_digest, lease.activation_ref)
            status, raw = await self._exchange("POST", "/maezo-workload/v2/outcomes", query, guard)
            guard()
            parsed = outcome(raw, status, expected, query)
            guard()
            return parsed, lease

        try:
            parsed, lease = await self._borrower.run_async(perform)
            # Outcome admission is independent and may expire during handoff too.
            self._guard(lease, expected, "outcome")
            return parsed
        except Exception:
            raise FetchUnavailableError() from None
