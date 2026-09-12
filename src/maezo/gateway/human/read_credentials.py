"""Separate source-free Q2 credentials. Providers are admitted dependencies, not DTO grants."""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import InitVar, dataclass, field
from datetime import UTC, datetime
from typing import Literal, Never

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCMSIV
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from maezo.portal.contracts.models import OpaqueRef, Revision, Sha256Digest

from .models import Closed, Scope
from .queue import ReadRefusalError
from .read_profile import Requester


def unavailable() -> ReadRefusalError:
    return ReadRefusalError("read_dependency_unavailable")


class ReadAdmission(Closed):
    scope: Scope
    engine_name: OpaqueRef
    database_incarnation: OpaqueRef
    read_deployment_ref: OpaqueRef
    read_deployment_digest: Sha256Digest
    runtime_admission_generation: Revision
    capability_digest: Sha256Digest
    observed_at: datetime
    valid_until: datetime
    provider_ref: OpaqueRef
    provider_revision: Revision


@dataclass(frozen=True, slots=True, repr=False)
class ReadAdmissionLease:
    record: ReadAdmission
    live: Callable[[], None] = field(repr=False)

    def require_current(self, now: datetime) -> None:
        self.live()
        if not self.record.observed_at <= now < self.record.valid_until:
            raise unavailable()

    def __reduce__(self) -> Never:
        raise TypeError("read lease cannot be serialized")


class ReadDeploymentAdmission(ABC):
    @abstractmethod
    async def current(self, scope: Scope, engine_name: str) -> ReadAdmissionLease:
        """Authenticated current read capability; controller DTO alone is insufficient."""
        raise NotImplementedError


@dataclass(frozen=True, slots=True, repr=False)
class ReadSigningLease:
    scope: Scope
    purpose: Literal["portal-task-read", "portal-read-publication"]
    requester: Requester
    audience: str
    not_before: datetime
    not_after: datetime
    max_envelope_seconds: int
    generation: int
    _key: Ed25519PrivateKey = field(repr=False)
    _live: Callable[[], None] = field(repr=False)

    def require_current(self, now: datetime) -> None:
        self._live()
        fingerprint = hashlib.sha256(
            self._key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        ).hexdigest()
        if (
            fingerprint != self.requester.public_key_sha256
            or not self.not_before <= now < self.not_after
            or self.max_envelope_seconds <= 0
        ):
            raise unavailable()

    def sign(self, raw: bytes, now: datetime) -> bytes:
        self.require_current(now)
        return self._key.sign(raw)

    def __reduce__(self) -> Never:
        raise TypeError("read signing lease cannot be serialized")


class ReadCredentialProvider(ABC):
    @abstractmethod
    async def acquire(self, scope: Scope, purpose: str, key_id: str) -> ReadSigningLease:
        raise NotImplementedError


@dataclass(frozen=True, slots=True, repr=False)
class CursorKeyLease:
    scope: Scope
    key_id: str
    key_tag: bytes = field(repr=False)
    not_before: datetime
    not_after: datetime
    generation: int
    _key_material: InitVar[bytes]
    _live: Callable[[], None] = field(repr=False)
    _cipher: AESGCMSIV = field(init=False, repr=False)

    def __post_init__(self, _key_material: bytes) -> None:
        if type(_key_material) is not bytes or len(_key_material) != 32:
            raise unavailable()
        object.__setattr__(self, "_cipher", AESGCMSIV(_key_material))

    def require_current(self, now: datetime) -> None:
        self._live()
        if len(self.key_tag) != 16 or not self.not_before <= now < self.not_after:
            raise unavailable()

    def encrypt(self, nonce: bytes, data: bytes, aad: bytes, now: datetime) -> bytes:
        self.require_current(now)
        return self._cipher.encrypt(nonce, data, aad)

    def decrypt(self, nonce: bytes, data: bytes, aad: bytes, now: datetime) -> bytes:
        self.require_current(now)
        return self._cipher.decrypt(nonce, data, aad)

    def __reduce__(self) -> Never:
        raise TypeError("cursor lease cannot be serialized")


@dataclass(frozen=True, slots=True, repr=False)
class CursorKeySet:
    current: CursorKeyLease
    accepted: tuple[CursorKeyLease, ...]
    live: Callable[[], None] = field(repr=False)

    def require_current(self, now: datetime) -> None:
        self.live()
        self.current.require_current(now)
        if (
            self.current not in self.accepted
            or len({key.key_tag for key in self.accepted}) != len(self.accepted)
            or any(key.scope != self.current.scope for key in self.accepted)
        ):
            raise unavailable()


class QueueCursorKeyProvider(ABC):
    @abstractmethod
    async def acquire(self, scope: Scope, key_id: str) -> CursorKeySet:
        raise NotImplementedError


class ReadCredentialPartition:
    """One request's monotonic local lifetime; never acquires command/worker rights."""

    def __init__(
        self,
        *,
        scope: Scope,
        engine_name: str,
        key_id: str,
        credentials: ReadCredentialProvider,
        admission: ReadDeploymentAdmission,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.scope = Scope.model_validate(scope)
        self.engine_name, self.key_id = engine_name, key_id
        self._credentials, self._admission, self.clock = credentials, admission, clock
        self.signing: ReadSigningLease | None = None
        self.admission: ReadAdmissionLease | None = None
        self._last: datetime | None = None
        self._acquiring = False
        self._closed = False

    async def prepare(self, purpose: str = "portal-task-read") -> None:
        if self._closed or self._acquiring:
            raise unavailable()
        if self.signing is not None:
            if self.signing.purpose != purpose:
                raise unavailable()
            self.guard()
            return
        self._acquiring = True
        try:
            admission = await self._admission.current(self.scope, self.engine_name)
            admission.require_current(self.clock())
            signing = await self._credentials.acquire(self.scope, purpose, self.key_id)
            if (
                admission.record.scope != self.scope
                or admission.record.engine_name != self.engine_name
                or signing.scope != self.scope
                or signing.purpose != purpose
                or signing.requester.key_id != self.key_id
                or signing.requester.issuer != self.scope.workload_ref
            ):
                raise unavailable()
            self.admission, self.signing = admission, signing
            self.guard()
        except BaseException:
            self.close()
            raise
        finally:
            self._acquiring = False

    def guard(self) -> datetime:
        now = self.clock()
        if (
            self._closed
            or self.admission is None
            or self.signing is None
            or (self._last is not None and now < self._last)
        ):
            raise unavailable()
        self.admission.require_current(now)
        self.signing.require_current(now)
        self._last = now
        return now

    def close(self) -> None:
        self._closed = True
        self.signing = None
        self.admission = None
