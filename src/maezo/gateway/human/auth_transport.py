"""Fixed human AUTH native routes, dedicated Ed25519 credentials and mTLS.

Trace: approved E04 native contract sections 2.3/4 and SCHEMAS sections 6/7.
Credential/source installation and PHI qualification remain owner dependencies.
"""

from __future__ import annotations

import base64
import hashlib
import math
import ssl
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from urllib.parse import urlsplit

import httpx
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from maezo.portal.engine.profile import canonicalize, strict_loads

from .auth_profile import (
    DocumentContextQuery,
    DocumentContextResult,
    HumanDocumentCommand,
    HumanReceiptQuery,
    HumanStartCommand,
    InputPublication,
    NativeEffectReceipt,
    NativeReceiptLookup,
    PublicationLookup,
    PublicationQuery,
    PublicationReceipt,
    Purpose,
    Request,
    Result,
    Scope,
)
from .read_profile import b64decode, digest, parse_model, wire


class AuthUnavailableError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("human_auth_dependency_unavailable")


@dataclass(frozen=True, slots=True, repr=False)
class AuthCredentialLease:
    scope: Scope
    purpose: Purpose
    workload_ref: str
    key_id: str
    audience: str
    public_key_sha256: str
    peer_spki_sha256: str
    not_before: datetime
    valid_until: datetime
    max_envelope_seconds: int
    key: Ed25519PrivateKey = field(repr=False)
    live: Callable[[], None] = field(repr=False)

    def guard(self, now: datetime) -> None:
        self.live()
        actual = hashlib.sha256(
            self.key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        ).hexdigest()
        if (
            actual != self.public_key_sha256
            or not self.not_before <= now < self.valid_until
            or not 0 < self.max_envelope_seconds <= 300
            or self.purpose == "human-auth-result"
        ):
            raise AuthUnavailableError()


@dataclass(frozen=True, slots=True, repr=False)
class AuthNativeTrustLease:
    scope: Scope
    issuer: str
    key_id: str
    audience: str
    server_spki_sha256: str
    not_before: datetime
    valid_until: datetime
    max_envelope_seconds: int
    key: Ed25519PublicKey = field(repr=False)
    live: Callable[[], None] = field(repr=False)

    def guard(self, now: datetime) -> None:
        self.live()
        if not self.not_before <= now < self.valid_until or not 0 < self.max_envelope_seconds <= 300:
            raise AuthUnavailableError()


class AuthCredentialProvider(ABC):
    @abstractmethod
    async def acquire(self, scope: Scope, purpose: Purpose) -> AuthCredentialLease:
        """Current installed purpose/key/peer identity, never a self-designating request."""
        raise NotImplementedError

    @abstractmethod
    async def native_trust(self, scope: Scope) -> AuthNativeTrustLease:
        """Current owner-installed native result signer and exact server SPKI."""
        raise NotImplementedError


_ROUTES: dict[type[Any], tuple[str, Purpose, type[Any]]] = {
    HumanStartCommand: ("auth-start", "human-auth-start", NativeEffectReceipt),
    HumanDocumentCommand: ("auth-documents", "human-auth-documents", NativeEffectReceipt),
    HumanReceiptQuery: ("auth-receipt", "human-auth-read", NativeReceiptLookup),
    DocumentContextQuery: ("auth-document-context", "human-auth-read", DocumentContextResult),
    InputPublication: ("auth-input-publication", "human-auth-input-publication", PublicationReceipt),
    PublicationQuery: ("auth-publication-receipt", "human-auth-publication-read", PublicationLookup),
}


def sign_request(request: Request, lease: AuthCredentialLease, now: datetime) -> bytes:
    lease.guard(now)
    route = _ROUTES.get(type(request))
    if (
        route is None
        or route[1] != lease.purpose
        or request.scope != lease.scope
        or request.workload_ref != lease.workload_ref
    ):
        raise AuthUnavailableError()
    expires = int(min(lease.valid_until, now + timedelta(seconds=lease.max_envelope_seconds)).timestamp())
    issued = int(now.timestamp())
    if expires <= issued:
        raise AuthUnavailableError()
    outer = dict(
        schema="human-auth-envelope.v1",
        purpose=lease.purpose,
        algorithm="Ed25519",
        audience=lease.audience,
        issuer=lease.workload_ref,
        tenant=lease.scope.tenant,
        key_id=lease.key_id,
        issued_at=str(issued),
        expires_at=str(expires),
        digest=digest(request),
        command=wire(request),
    )
    outer["signature"] = (
        base64.urlsafe_b64encode(lease.key.sign(canonicalize(outer))).rstrip(b"=").decode("ascii")
    )
    raw = canonicalize(outer)
    if len(raw) > 65536:
        raise AuthUnavailableError()
    lease.guard(now)
    return raw


def bind_receipt(receipt: NativeEffectReceipt, command: HumanStartCommand | HumanDocumentCommand) -> None:
    if (
        receipt.scope,
        receipt.command_id,
        receipt.command_digest,
        receipt.actor_principal_ref,
        receipt.admission_ref,
        receipt.admitted_digest,
    ) != (
        command.scope,
        command.command_id,
        digest(command),
        command.actor.principal_ref,
        command.admission.intent_ref,
        command.admission.admitted_digest,
    ):
        raise AuthUnavailableError()
    if isinstance(command, HumanStartCommand):
        if (receipt.operation, receipt.intake_ref, receipt.guide_identity_ref, receipt.definition) != (
            "auth.start",
            command.intake_ref,
            command.guide_identity_ref,
            command.definition,
        ):
            raise AuthUnavailableError()
    else:
        b = command.occurrence
        if (
            receipt.operation,
            receipt.case_ref,
            receipt.request_ref,
            receipt.occurrence_generation,
            receipt.subscription_id,
            receipt.document_set_digest,
            receipt.process_instance_id,
            receipt.definition,
        ) != (
            "auth.documents.respond",
            command.case_ref,
            b.request_ref,
            b.generation,
            b.subscription_id,
            command.document_set_digest,
            b.process_instance_id,
            b.definition,
        ):
            raise AuthUnavailableError()


def bind_result(result: Result, request: Request, now: datetime) -> None:
    if result.scope != request.scope:
        raise AuthUnavailableError()
    if isinstance(request, (HumanStartCommand, HumanDocumentCommand)) and isinstance(
        result, NativeEffectReceipt
    ):
        bind_receipt(result, request)
    elif isinstance(request, HumanReceiptQuery) and isinstance(result, NativeReceiptLookup):
        if (result.query_id, result.query_digest) != (
            request.query_id,
            digest(request),
        ) or result.observed_at > now:
            raise AuthUnavailableError()
        r = result.receipt
        if r is not None and (
            (r.scope, r.command_id, r.command_digest, r.operation)
            != (request.scope, request.command_id, request.expected_command_digest, request.operation)
            or (r.intake_ref if request.operation == "auth.start" else r.case_ref)
            != request.intake_or_case_ref
        ):
            raise AuthUnavailableError()
    elif isinstance(request, DocumentContextQuery) and isinstance(result, DocumentContextResult):
        o = result.occurrence
        if (result.query_id, result.query_digest, result.actor, o.scope, o.case_ref, o.request_ref) != (
            request.query_id,
            digest(request),
            request.actor,
            request.scope,
            request.case_ref,
            request.request_ref,
        ) or now >= result.valid_until:
            raise AuthUnavailableError()
    elif isinstance(request, InputPublication) and isinstance(result, PublicationReceipt):
        if (
            result.publication_id,
            result.request_digest,
            result.kind,
            result.resource_ref,
            result.previous_generation,
            result.head_generation,
            result.state,
            result.payload_digest,
        ) != (
            request.publication_id,
            digest(request),
            request.kind,
            request.resource_ref,
            request.expected_generation,
            request.expected_generation + 1,
            request.state,
            request.payload_digest,
        ):
            raise AuthUnavailableError()
    elif isinstance(request, PublicationQuery) and isinstance(result, PublicationLookup):
        if (result.query_id, result.query_digest) != (
            request.query_id,
            digest(request),
        ) or result.observed_at > now:
            raise AuthUnavailableError()
        if result.receipt is not None and (
            result.receipt.scope,
            result.receipt.publication_id,
            result.receipt.request_digest,
        ) != (request.scope, request.publication_id, request.expected_digest):
            raise AuthUnavailableError()
    else:
        raise AuthUnavailableError()
    if isinstance(result, (NativeEffectReceipt, PublicationReceipt)) and result.committed_at > now:
        raise AuthUnavailableError()


def verify_result(raw: bytes, request: Request, trust: AuthNativeTrustLease, now: datetime) -> Result:
    trust.guard(now)
    if len(raw) > 65536:
        raise AuthUnavailableError()
    outer = strict_loads(raw)
    keys = {
        "schema",
        "purpose",
        "algorithm",
        "audience",
        "issuer",
        "tenant",
        "key_id",
        "issued_at",
        "expires_at",
        "digest",
        "command",
        "signature",
    }
    if type(outer) is not dict or outer.keys() != keys:
        raise AuthUnavailableError()
    if (
        outer["schema"],
        outer["purpose"],
        outer["algorithm"],
        outer["audience"],
        outer["issuer"],
        outer["tenant"],
        outer["key_id"],
    ) != (
        "human-auth-envelope.v1",
        "human-auth-result",
        "Ed25519",
        trust.audience,
        trust.issuer,
        trust.scope.tenant,
        trust.key_id,
    ) or request.scope != trust.scope:
        raise AuthUnavailableError()

    def epoch(value: object) -> int:
        if (
            type(value) is not str
            or not value.isascii()
            or not value.isdecimal()
            or len(value) > 19
            or (value != "0" and value.startswith("0"))
        ):
            raise AuthUnavailableError()
        return int(value)

    issued, expires = epoch(outer["issued_at"]), epoch(outer["expires_at"])
    if (
        not trust.not_before.timestamp()
        <= issued
        <= now.timestamp()
        < expires
        <= trust.valid_until.timestamp()
        or not 0 < expires - issued <= trust.max_envelope_seconds
    ):
        raise AuthUnavailableError()
    signature = b64decode(outer.pop("signature"), url=True, size=64)
    trust.key.verify(signature, canonicalize(outer))
    result = cast(Result, parse_model(_ROUTES[type(request)][2], outer["command"]))
    if outer["digest"] != digest(result):
        raise AuthUnavailableError()
    bind_result(result, request, now)
    trust.guard(now)
    return result


class AuthNativeClient:
    """One installed peer. No generic URL, command purpose or redirect supplied by caller."""

    def __init__(
        self,
        *,
        origin: str,
        tls_context: ssl.SSLContext,
        credentials: AuthCredentialProvider,
        client_certificate_der: bytes,
        timeout_seconds: float = 5,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        p = urlsplit(origin)
        if (
            p.scheme != "https"
            or not p.hostname
            or p.path not in ("", "/")
            or p.query
            or p.fragment
            or p.username
            or p.password
            or not tls_context.check_hostname
            or tls_context.verify_mode != ssl.CERT_REQUIRED
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 10
        ):
            raise AuthUnavailableError()
        cert = x509.load_der_x509_certificate(client_certificate_der)
        self._client_pin = hashlib.sha256(
            cert.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        ).hexdigest()
        self._origin, self._credentials, self.clock = origin.rstrip("/"), credentials, clock
        self._http = httpx.AsyncClient(
            verify=tls_context,
            transport=transport,
            follow_redirects=False,
            trust_env=False,
            timeout=timeout_seconds,
        )

    async def execute(self, request: Request, *, current: Callable[[], None] = lambda: None) -> Result:
        try:
            request = parse_model(type(request), wire(request))
            route, purpose, _ = _ROUTES[type(request)]
            signing = await self._credentials.acquire(request.scope, purpose)
            trust = await self._credentials.native_trust(request.scope)

            def guard() -> datetime:
                current()
                now = self.clock()
                signing.guard(now)
                trust.guard(now)
                if signing.peer_spki_sha256 != self._client_pin:
                    raise AuthUnavailableError()
                return now

            raw = sign_request(request, signing, guard())
            guard()
            async with self._http.stream(
                "POST",
                self._origin + "/maezo-human/v1/" + route,
                content=raw,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            ) as response:
                stream = response.extensions.get("network_stream")
                tls = stream.get_extra_info("ssl_object") if stream else None
                if tls is None:
                    raise AuthUnavailableError()
                cert = x509.load_der_x509_certificate(tls.getpeercert(binary_form=True))
                pin = hashlib.sha256(
                    cert.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
                ).hexdigest()
                if (
                    pin != trust.server_spki_sha256
                    or response.status_code != 200
                    or response.headers.get("content-type", "").split(";")[0] != "application/json"
                    or "set-cookie" in response.headers
                    or response.headers.get("cache-control") != "no-store"
                ):
                    raise AuthUnavailableError()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 65536:
                        raise AuthUnavailableError()
                    guard()
                result = verify_result(bytes(body), request, trust, guard())
            # Pool/stream release is potentially blocking; retain all prior ceilings.
            final_now = guard()
            if final_now.timestamp() >= int(strict_loads(bytes(body))["expires_at"]):
                raise AuthUnavailableError()
            bind_result(result, request, final_now)
            guard()
            return result
        except Exception:
            raise AuthUnavailableError() from None

    async def close(self) -> None:
        await self._http.aclose()
