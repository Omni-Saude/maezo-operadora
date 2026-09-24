"""SC1 real bounded mTLS transport and installed staff proof signing.

The caller supplies independently installed designation/key material. Publication
input is the issuer's immutable request; retries preserve it byte-for-byte and
native receipt recovery performs no admission replay. No automatic retry occurs.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ssl
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from maezo.gateway.external_cases.models import MAX, now_utc, timestamp
from maezo.gateway.human.auth_profile import Actor
from maezo.gateway.human.read_profile import digest, parse_model, utc, wire
from maezo.portal.contracts.models import HumanPrincipal
from maezo.portal.engine.profile import canonicalize, strict_loads

from .authority import InstalledStaffAuthority, fingerprint, source_matches
from .models import MembershipWitness, Proof, StaffCaseError, StaffPublication
from .postgres import NativeMembershipSource


class _ReadRefusalError(Exception):
    def __init__(self, code: Literal["invalid", "denied", "conflict", "unavailable"]):
        self.code = code
        super().__init__("staff_native_read_refused")


@dataclass(frozen=True, repr=False)
class StaffSigner:
    authority: InstalledStaffAuthority
    key: Ed25519PrivateKey = field(repr=False)
    role: str
    clock: Callable[[], datetime] = now_utc

    def guard(self, purpose: str) -> datetime:
        now = self.clock()
        entry = self.authority.entries.get(fingerprint(self.key.public_key()))
        if (
            entry is None
            or entry.role != self.role
            or purpose not in entry.purposes
            or entry.key_fingerprint in self.authority.revoked_fingerprints
            or not timestamp(entry.not_before)
            <= now
            < min(timestamp(entry.valid_until), self.authority.valid_until)
        ):
            raise StaffCaseError("unavailable")
        return now

    def until(self, purpose: str) -> datetime:
        self.guard(purpose)
        entry = self.authority.entries[fingerprint(self.key.public_key())]
        return min(timestamp(entry.valid_until), self.authority.valid_until)

    def sign(self, value: dict[str, Any], purpose: str) -> dict[str, Any]:
        self.guard(purpose)
        result = dict(value)
        if "signature" in result:
            raise StaffCaseError("invalid")
        result["signature"] = base64.b64encode(self.key.sign(canonicalize(result))).decode("ascii")
        self.guard(purpose)
        return result

    def proof(self, value: dict[str, Any], purpose: str, until: datetime) -> Proof:
        now = self.guard(purpose)
        end = min(until, self.until(purpose))
        if now >= end:
            raise StaffCaseError("unavailable")
        unsigned = dict(
            schema="staff-case-proof.v1",
            purpose=purpose,
            algorithm="Ed25519",
            key_fingerprint=fingerprint(self.key.public_key()),
            issued_at=utc(now),
            expires_at=utc(end),
            statement_digest=digest(value),
        )
        return parse_model(Proof, self.sign(unsigned, purpose))


class StaffWitnessSource:
    def __init__(self, source: NativeMembershipSource, signer: StaffSigner):
        if signer.role != "identity_verifier" or signer.authority.designation.scope != source.scope:
            raise StaffCaseError("unavailable")
        self.source, self.signer = source, signer

    async def observe(self, principal: HumanPrincipal, session_until: datetime) -> MembershipWitness:
        source, native, until = await self.source.observe(principal)
        now = self.signer.guard("membership_current")
        entry = self.signer.authority.entries[fingerprint(self.signer.key.public_key())]
        if not source_matches(entry.role, entry.source_ref, source.source_ref):
            raise StaffCaseError("denied")
        end = min(until, session_until, self.signer.until("membership_current"))
        value = dict(
            schema="staff-case-membership-witness.v1",
            scope=wire(self.source.scope),
            actor=wire(Actor.from_principal(principal, "staff")),
            session_ref=principal.session_ref,
            principal_record_revision=native["revision"],
            principal_record_digest=digest(native),
            source=wire(source),
            observed_at=utc(now),
            valid_until=utc(end),
        )
        value["proof"] = wire(self.signer.proof(value, "membership_current", end))
        return parse_model(MembershipWitness, value)


class StaffNativeClient:
    """Owns one fixed-origin HTTP pool with actual loaded client certificate pins.

    Route registration is a separately reviewed ROOT composition hook. No default
    URL, identity, key, authority provider or enabled production configuration exists.
    """

    def __init__(
        self,
        *,
        origin: str,
        ca_file: Path,
        certificate_file: Path,
        private_key_file: Path,
        server_spki_sha256: str,
        signer: StaffSigner,
        result_authority: InstalledStaffAuthority,
        configuration_digest: str,
        seconds: float = 5,
    ):
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
            or not 0 < seconds <= 10
            or signer.role not in {"read_requester", "publication_importer"}
            or signer.authority.installation_digest != result_authority.installation_digest
            or len(server_spki_sha256) != 64
            or any(c not in "0123456789abcdef" for c in server_spki_sha256)
            or len(configuration_digest) != 64
            or any(c not in "0123456789abcdef" for c in configuration_digest)
        ):
            raise StaffCaseError("unavailable")
        # Load and pin the SAME certificate passed to SSL, not an unrelated supplied fingerprint.
        pem = certificate_file.read_bytes()
        cert = x509.load_pem_x509_certificate(pem)
        actual = hashlib.sha256(
            cert.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        ).hexdigest()
        entry = signer.authority.entries.get(fingerprint(signer.key.public_key()))
        if entry is None or actual != entry.certificate_spki:
            raise StaffCaseError("unavailable")
        tls = ssl.create_default_context(cafile=str(ca_file))
        # SSL receives the exact already-pinned public certificate bytes. A source
        # path rotation cannot swap in a different certificate between read/load.
        with NamedTemporaryFile(prefix="maezo-staff-public-cert-", suffix=".pem") as retained:
            retained.write(pem)
            retained.flush()
            tls.load_cert_chain(retained.name, str(private_key_file))
        self.signer, self.authority, self.seconds = signer, result_authority, seconds
        self.origin, self.server_pin, self.configuration_digest = (
            origin.rstrip("/"),
            server_spki_sha256,
            configuration_digest,
        )
        self.http = httpx.AsyncClient(verify=tls, timeout=seconds, trust_env=False, follow_redirects=False)
        self.closed = False

    async def close(self) -> None:
        self.closed = True
        await self.http.aclose()

    def verify_result(self, value: dict[str, Any], *, until: datetime, historical: bool = False) -> None:
        proof = parse_model(Proof, value.get("proof"))
        unsigned = dict(value)
        unsigned.pop("proof")
        now = self.signer.clock()
        verification_time = timestamp(proof.issued_at) if historical else now
        # Historical proof authenticates a stored fact only. The surrounding exact
        # exchange still uses current importer credentials, pinned TLS and current
        # native installed authorization. Never use this time to admit product data.
        if now >= self.authority.valid_until or verification_time > now:
            raise StaffCaseError("unavailable")
        self.authority.proof(
            proof, unsigned, role="native_result", purpose="native_result", now=verification_time
        )
        if now >= until or (
            not historical and now >= min(timestamp(value["valid_until"]), timestamp(proof.expires_at))
        ):
            raise StaffCaseError("unavailable")

    async def _send(
        self, route: str, request: dict[str, Any], purpose: str, until: datetime, *, effect: bool
    ) -> dict[str, Any]:
        sent = False
        try:
            if self.closed:
                raise StaffCaseError("unavailable")
            self.signer.guard(purpose)
            body = canonicalize(request)
            if len(body) > MAX:
                raise StaffCaseError("invalid")
            async with asyncio.timeout(self.seconds):
                sent = True
                async with self.http.stream(
                    "POST",
                    self.origin + "/maezo-human/v1/" + route,
                    content=body,
                    headers={"Content-Type": "application/json", "Accept": "application/json"},
                ) as response:
                    stream = response.extensions.get("network_stream")
                    tls = stream.get_extra_info("ssl_object") if stream is not None else None
                    if tls is None:
                        raise StaffCaseError("unavailable")
                    cert = x509.load_der_x509_certificate(tls.getpeercert(binary_form=True))
                    peer = hashlib.sha256(
                        cert.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
                    ).hexdigest()
                    if peer != self.server_pin:
                        raise StaffCaseError("unavailable")
                    if response.status_code != 200:
                        if not effect:
                            # Only authenticated read refusals receive product status
                            # semantics. No 4xx publication implies remote non-effect.
                            if response.status_code == 404:
                                raise _ReadRefusalError("denied")
                            if response.status_code == 409:
                                raise _ReadRefusalError("conflict")
                            if response.status_code == 400:
                                raise _ReadRefusalError("invalid")
                        raise StaffCaseError("unavailable")
                    if response.headers.get("content-type", "").split(";")[0] != "application/json":
                        raise StaffCaseError("unavailable")
                    chunks = bytearray()
                    async for chunk in response.aiter_bytes():
                        chunks.extend(chunk)
                        if len(chunks) > MAX:
                            raise StaffCaseError("unavailable")
                    value = strict_loads(bytes(chunks))
                    if type(value) is not dict or canonicalize(value) != bytes(chunks):
                        raise StaffCaseError("invalid")
                # Last check follows the awaited HTTP response cleanup, not only body read.
                self.signer.guard(purpose)
                self.verify_result(value, until=until, historical=effect)
            self.signer.guard(purpose)
            if self.signer.clock() >= until:
                raise StaffCaseError("unavailable")
            return value
        except asyncio.CancelledError:
            raise
        except _ReadRefusalError as refusal:
            raise StaffCaseError(refusal.code) from None
        except Exception:
            raise StaffCaseError("uncertain" if effect and sent else "unavailable") from None

    async def read(
        self,
        *,
        principal: HumanPrincipal,
        witness: MembershipWitness,
        session_until: datetime,
        operation: Literal["detail", "list", "finalize"],
        query: dict[str, Any],
    ) -> tuple[dict[str, Any], str]:
        purpose = "staff-case-finalize.v1" if operation == "finalize" else "staff-case-read.v1"
        now = self.signer.guard(purpose)
        until = min(
            session_until,
            timestamp(witness.valid_until),
            self.signer.until(purpose),
            now + timedelta(seconds=self.seconds),
        )
        value = dict(
            schema="staff-case-native-read.v1",
            purpose=purpose,
            scope=wire(witness.scope),
            installation_digest=self.authority.installation_digest,
            request_id=str(uuid4()),
            principal=wire(principal),
            membership_witness=wire(witness),
            session_valid_until=utc(session_until),
            operation=operation,
            query=query,
            issued_at=utc(now),
            expires_at=utc(until),
            key_fingerprint=fingerprint(self.signer.key.public_key()),
        )
        signed = self.signer.sign(value, purpose)
        result = await self._send("staff-case-" + operation, signed, purpose, until, effect=False)
        if result.get("request_digest") != digest(signed):
            raise StaffCaseError("conflict")
        return result, digest(signed)

    async def publish(self, immutable_request: bytes) -> dict[str, Any]:
        request = strict_loads(immutable_request)
        parsed = parse_model(StaffPublication, request)
        if canonicalize(request) != immutable_request or parsed.scope != self.authority.designation.scope:
            raise StaffCaseError("invalid")
        purpose = "staff-case-publication.v1"
        now = self.signer.guard(purpose)
        until = min(self.signer.until(purpose), now + timedelta(seconds=self.seconds))
        envelope = dict(
            schema="staff-case-envelope.v1",
            purpose=purpose,
            scope=wire(parsed.scope),
            key_fingerprint=fingerprint(self.signer.key.public_key()),
            configuration_digest=self.configuration_digest,
            issued_at=utc(now),
            expires_at=utc(until),
            request=request,
        )
        result = await self._send(
            "staff-case-publication", self.signer.sign(envelope, purpose), purpose, until, effect=True
        )
        if (
            set(result)
            != {
                "schema",
                "publication_id",
                "request_digest",
                "scope",
                "source_ref",
                "source_revision",
                "payload_digest",
                "disposition",
                "committed_at",
                "native_receipt_ref",
                "valid_until",
                "proof",
            }
            or result["schema"] != "staff-case-publication-receipt.v1"
            or result["disposition"] != "committed"
            or result["publication_id"] != parsed.publication_id
            or result["request_digest"] != digest(request)
            or result["scope"] != wire(parsed.scope)
            or result["source_ref"] != parsed.source_ref
            or result["source_revision"] != parsed.source_revision
            or result["payload_digest"] != parsed.payload_digest
        ):
            raise StaffCaseError("uncertain")
        return result
