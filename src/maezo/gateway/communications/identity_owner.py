"""Private, mTLS-bound conditional session revocation for the PHI resolver."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import secrets
import ssl
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import urlsplit

import httpx
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pydantic import Field

from maezo.portal.api.auth import AuthenticationError
from maezo.portal.api.postgres import PostgresIdentityStore

from .production_config import Closed, Digest, PhiProductionError, Ref, fixed_origin

MAX_BODY = 8192


class IdentityMismatchRevocation(Closed):
    schema_: Literal["identity-mismatch-revocation.v1"] = Field(alias="schema")
    request_id: Ref
    identity_source_ref: Ref
    tenant: Ref
    session_hash: Digest = Field(repr=False)
    observed_session_digest: Digest = Field(repr=False)
    observed_membership_digest: Digest = Field(repr=False)
    observed_at: datetime
    valid_until: datetime


class IdentityMismatchResult(Closed):
    schema_: Literal["identity-mismatch-revocation-result.v1"] = Field(alias="schema")
    request_id: Ref
    request_digest: Digest
    identity_source_ref: Ref
    disposition: Literal["revoked", "absent"]
    observed_at: datetime
    valid_until: datetime


def _canonical(value: object) -> bytes:
    try:
        if isinstance(value, Closed):
            value = value.model_dump(mode="json", by_alias=True)
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    except Exception:
        raise PhiProductionError() from None


def identity_record_digest(value: object) -> str:
    model_dump = getattr(value, "model_dump", None)
    if not callable(model_dump):
        raise PhiProductionError()
    return hashlib.sha256(_canonical(model_dump(mode="json"))).hexdigest()


def request_digest(request: IdentityMismatchRevocation) -> str:
    return hashlib.sha256(_canonical(request)).hexdigest()


class IdentityOwnerClient:
    """One fixed private owner endpoint; TLS peer identity comes from the socket."""

    def __init__(
        self,
        *,
        origin: str,
        tenant: str,
        identity_source_ref: str,
        server_spki_sha256: str,
        tls_context: ssl.SSLContext,
        valid_until: datetime,
        seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        parsed = urlsplit(fixed_origin(origin))
        if (
            parsed.path not in ("", "/")
            or not tls_context.check_hostname
            or tls_context.verify_mode != ssl.CERT_REQUIRED
            or valid_until.tzinfo is None
            or not math.isfinite(seconds)
            or not 0 < seconds <= 10
        ):
            raise PhiProductionError()
        self.origin = origin.rstrip("/")
        self.tenant = tenant
        self.identity_source_ref = identity_source_ref
        self.server_spki_sha256 = server_spki_sha256
        self.valid_until = valid_until
        self.seconds = seconds
        self.clock = clock
        self.http = httpx.AsyncClient(
            verify=tls_context,
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            timeout=seconds,
        )
        self.closed = False

    async def revoke(
        self,
        *,
        session_hash: str,
        observed_session_digest: str,
        observed_membership_digest: str,
        observed_at: datetime,
    ) -> IdentityMismatchResult:
        try:
            now = self.clock()
            deadline = min(
                self.valid_until,
                now + timedelta(seconds=self.seconds),
                observed_at + timedelta(seconds=self.seconds),
            )
            if self.closed or observed_at.tzinfo is None or not observed_at <= now < deadline:
                raise PhiProductionError()
            request = IdentityMismatchRevocation(
                schema="identity-mismatch-revocation.v1",
                request_id=secrets.token_hex(16),
                identity_source_ref=self.identity_source_ref,
                tenant=self.tenant,
                session_hash=session_hash,
                observed_session_digest=observed_session_digest,
                observed_membership_digest=observed_membership_digest,
                observed_at=observed_at,
                valid_until=deadline,
            )
            raw = _canonical(request)
            async with asyncio.timeout(self.seconds):
                async with self.http.stream(
                    "POST",
                    self.origin + "/internal/v1/identity/revoke-mismatched-session",
                    content=raw,
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                ) as response:
                    stream = response.extensions.get("network_stream")
                    tls = stream.get_extra_info("ssl_object") if stream is not None else None
                    if tls is None:
                        raise PhiProductionError()
                    certificate = x509.load_der_x509_certificate(tls.getpeercert(binary_form=True))
                    peer = hashlib.sha256(
                        certificate.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
                    ).hexdigest()
                    if (
                        peer != self.server_spki_sha256
                        or response.status_code != 200
                        or response.headers.get("content-type", "").split(";", 1)[0] != "application/json"
                        or response.headers.get("cache-control") != "no-store"
                        or "set-cookie" in response.headers
                    ):
                        raise PhiProductionError()
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_BODY or self.clock() >= deadline:
                            raise PhiProductionError()
            result = IdentityMismatchResult.model_validate_json(bytes(body))
            if (
                result.request_id != request.request_id
                or result.request_digest != request_digest(request)
                or result.identity_source_ref != self.identity_source_ref
                or not now <= result.observed_at < result.valid_until <= deadline
                or self.clock() >= deadline
            ):
                raise PhiProductionError()
            return result
        except asyncio.CancelledError:
            raise
        except Exception:
            raise AuthenticationError() from None

    async def close(self) -> None:
        if not self.closed:
            self.closed = True
            await self.http.aclose()


class IdentityMismatchOwner:
    """Owner core; private listener authenticates the peer before calling this method."""

    def __init__(
        self,
        store: PostgresIdentityStore,
        *,
        tenant: str,
        identity_source_ref: str,
        requester_spki_sha256: str,
        requester_valid_until: datetime,
        seconds: float,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if (
            type(store) is not PostgresIdentityStore
            or store.tenant != tenant
            or not math.isfinite(seconds)
            or not 0 < seconds <= 10
            or requester_valid_until.tzinfo is None
        ):
            raise PhiProductionError()
        self.store = store
        self.tenant = tenant
        self.identity_source_ref = identity_source_ref
        self.requester_spki_sha256 = requester_spki_sha256
        self.requester_valid_until = requester_valid_until
        self.seconds = seconds
        self.clock = clock

    async def revoke(
        self,
        request: IdentityMismatchRevocation,
        *,
        peer_spki_sha256: str,
        purpose: str,
    ) -> IdentityMismatchResult:
        now = self.clock()
        if (
            peer_spki_sha256 != self.requester_spki_sha256
            or purpose != "identity-mismatch-revocation.v1"
            or request.tenant != self.tenant
            or request.identity_source_ref != self.identity_source_ref
            or request.observed_at > now
            or now >= self.requester_valid_until
            or not now < request.valid_until <= request.observed_at + timedelta(seconds=self.seconds)
        ):
            raise AuthenticationError()
        try:
            disposition = await self.store.revoke_mismatched_session(  # type: ignore[attr-defined]
                request.session_hash,
                expected_session_digest=request.observed_session_digest,
                expected_membership_digest=request.observed_membership_digest,
            )
        except AuthenticationError:
            raise
        except Exception:
            raise PhiProductionError() from None
        completed = self.clock()
        if disposition not in {"revoked", "absent"} or completed >= min(
            request.valid_until, self.requester_valid_until
        ):
            raise AuthenticationError()
        return IdentityMismatchResult(
            schema="identity-mismatch-revocation-result.v1",
            request_id=request.request_id,
            request_digest=request_digest(request),
            identity_source_ref=self.identity_source_ref,
            disposition=disposition,
            observed_at=completed,
            valid_until=request.valid_until,
        )
