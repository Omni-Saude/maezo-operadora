"""D7-C workload HTTP boundary, consuming the frozen D7-B v1 interface.

Configuration is deployment-owned, never agent-supplied. Readiness proves the server's
current profile/definition grants; source authority is checked by B in its transaction.
No client-created EngineAuthority, raw REST fallback, or automatic retries of mutations.
"""

from __future__ import annotations

import hashlib
import math
import re
import ssl
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes

from maezo.gateway.engine_contracts import (
    EngineCapabilityError,
    EngineCapabilityProfile,
    EngineIdentity,
    EngineOperation,
    EngineRefusalCode,
    EngineRequest,
    FieldOrigin,
    canonical_json,
    parse_json,
)

_SUPPORTED = frozenset(
    {
        EngineOperation.START,
        EngineOperation.CORRELATE,
        EngineOperation.READ_ACTIVE,
        EngineOperation.READ_HISTORY,
    }
)
_LIMIT = 1_048_576


class EngineTransportUnavailableError(EngineCapabilityError):
    """Unanswered authority/read or ambiguous effect; never authoritative absence."""

    def __init__(self) -> None:
        super().__init__(EngineRefusalCode.IDENTITY_UNAVAILABLE)


class EngineRevisionConflictError(EngineCapabilityError):
    """Native optimistic conflict, reported without replaying a mutation."""

    def __init__(self) -> None:
        super().__init__(EngineRefusalCode.RESOURCE_MISMATCH)


@dataclass(frozen=True, slots=True)
class EngineTLSConfig:
    """Explicit workload identity and reviewed server policy; paths stay in the gateway.

    CA bytes and client certificate are pinned independently of operating-system roots.
    D owns provisioning these files and the matching B manifest; this is not a grant loader.
    """

    endpoint: str
    identity: EngineIdentity
    profiles: tuple[EngineCapabilityProfile, ...]
    policy_digest: str
    ca_file: Path = field(repr=False)
    ca_sha256: str
    certificate_file: Path = field(repr=False)
    certificate_sha256: str
    private_key_file: Path = field(repr=False)
    timeout: float = 15.0

    def validate(self) -> bytes:
        return self._validated_material()[0]

    def _validated_material(self) -> tuple[bytes, bytes, bytes]:
        """Capture once; validate and load only this material, never reopened input paths."""
        try:
            url = urlsplit(self.endpoint)
            if (
                url.scheme != "https"
                or not url.hostname
                or url.username is not None
                or url.password is not None
                or url.query
                or url.fragment
                or url.path != "/engine-rest"
                or url.port is None
                or self.endpoint != f"https://{url.netloc}/engine-rest"
                or type(self.timeout) not in (float, int)
                or not math.isfinite(self.timeout)
                or self.timeout <= 0
            ):
                raise EngineTransportUnavailableError()
            if type(self.identity) is not EngineIdentity:
                raise EngineTransportUnavailableError()
            self.identity.__post_init__()
            if type(self.profiles) is not tuple or not self.profiles:
                raise EngineTransportUnavailableError()
            keys = set()
            for profile in self.profiles:
                if type(profile) is not EngineCapabilityProfile or profile.identity != self.identity:
                    raise EngineTransportUnavailableError()
                profile.__post_init__()
                key = (profile.schema.operation, profile.target.process_key, profile.target.message)
                if key in keys:
                    raise EngineTransportUnavailableError()
                keys.add(key)
            for digest in (self.policy_digest, self.ca_sha256, self.certificate_sha256):
                if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                    raise EngineTransportUnavailableError()
            for path in (self.ca_file, self.certificate_file, self.private_key_file):
                if not path.is_absolute() or path.resolve(strict=True) != path or not path.is_file():
                    raise EngineTransportUnavailableError()
            ca = self.ca_file.read_bytes()
            certificate = self.certificate_file.read_bytes()
            private_key = self.private_key_file.read_bytes()
            cert = x509.load_pem_x509_certificate(certificate)
            now = datetime.now(UTC)
            sans = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
            if (
                hashlib.sha256(ca).hexdigest() != self.ca_sha256
                or cert.fingerprint(hashes.SHA256()).hex() != self.certificate_sha256
                or not cert.not_valid_before_utc <= now < cert.not_valid_after_utc
                or cert.issuer.rfc4514_string() != self.identity.issuer
                or sans.get_values_for_type(x509.UniformResourceIdentifier) != [self.identity.subject]
                or not self.identity.subject.startswith("spiffe://")
                or x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH not in eku
            ):
                raise EngineTransportUnavailableError()
            return ca, certificate, private_key
        except Exception:
            raise EngineTransportUnavailableError() from None

    def context(self) -> ssl.SSLContext:
        ca, certificate, private_key = self._validated_material()
        try:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_verify_locations(cadata=ca.decode("ascii"))
            # Python's native loader requires paths. Use exclusively created 0600 files
            # in a private 0700 directory, independent of mutable deployment paths. The
            # native loader still verifies certificate/key matching. Delete both files
            # before returning the context (also on any failure); never cache key bytes.
            with (
                tempfile.TemporaryDirectory(prefix="maezo-engine-tls-") as directory,
                tempfile.NamedTemporaryFile(dir=directory) as cert_file,
                tempfile.NamedTemporaryFile(dir=directory) as key_file,
            ):
                cert_file.write(certificate)
                cert_file.flush()
                key_file.write(private_key)
                key_file.flush()
                context.load_cert_chain(cert_file.name, key_file.name)
            return context
        except Exception:
            raise EngineTransportUnavailableError() from None


class EngineOperationsClient:
    """Only the two fixed workload routes. Each request uses a freshly validated TLS identity."""

    def __init__(self, config: EngineTLSConfig) -> None:
        config.context()
        self.config = config
        self._closed = False

    def profile(
        self, operation: EngineOperation, process_key: str, message: str = ""
    ) -> EngineCapabilityProfile:
        if operation not in _SUPPORTED:
            raise EngineCapabilityError(EngineRefusalCode.OPERATION_DENIED)
        for profile in self.config.profiles:
            if (
                profile.schema.operation is operation
                and profile.target.process_key == process_key
                and profile.target.message == message
            ):
                return profile
        raise EngineCapabilityError(EngineRefusalCode.PROFILE_UNAVAILABLE)

    def project(
        self, request: EngineRequest, *, source_ref: str = ""
    ) -> tuple[EngineCapabilityProfile, bytes]:
        request.__post_init__()
        profile = self.profile(request.operation, request.process_key, request.message)
        schema = profile.schema
        variables, correlation = parse_json(request.variables_json), parse_json(request.correlation_json)
        schema.validate(variables)
        if (
            request.topic
            or request.worker_id
            or request.error_code
            or parse_json(request.parameters_json)
            or request.all_matching is not schema.all_matching
            or set(correlation) != set(schema.correlation_fields)
        ):
            raise EngineCapabilityError(EngineRefusalCode.FIELD_DENIED)
        bound = {
            "tenant_id": profile.identity.tenant,
            "source_agent_id": profile.identity.workload,
            "source_agent_version": profile.identity.workload_version,
        }
        for rule in schema.fields:
            if (
                rule.name in variables
                and rule.origin is FieldOrigin.BOUND
                and variables[rule.name] != bound.get(rule.name)
            ):
                raise EngineCapabilityError(EngineRefusalCode.IDENTITY_MISMATCH)
        for key, value in correlation.items():
            if type(value) is not str or not value:
                raise EngineCapabilityError(EngineRefusalCode.INVALID_BODY)
            if key == "tenant_id" and value != profile.identity.tenant:
                raise EngineCapabilityError(EngineRefusalCode.IDENTITY_MISMATCH)
        # A source reference is a pointer only. B resolves current lock / committed human receipt.
        if type(source_ref) is not str or bool(source_ref) != bool(schema.source_process_key):
            raise EngineCapabilityError(EngineRefusalCode.EVIDENCE_UNAVAILABLE)
        if source_ref:
            EngineRequest(EngineOperation.READ_HISTORY, request.process_key, source_ref, b"{}")
        return profile, canonical_json(
            dict(
                protocol="maezo.engine-operation.v1",
                capability_digest=profile.digest,
                operation=request.operation.value,
                process_key=request.process_key,
                resource_ref=request.resource_ref,
                variables=variables,
                correlation=correlation,
                all_matching=request.all_matching,
                error_code="",
                topic="",
                message=request.message,
                worker_id="",
                parameters={},
                source_ref=source_ref,
            )
        )

    async def _exchange(self, method: str, route: str, body: bytes | None = None) -> dict[str, Any]:
        if self._closed:
            raise EngineTransportUnavailableError()
        try:
            context = self.config.context()
            async with (
                httpx.AsyncClient(
                    verify=context, trust_env=False, follow_redirects=False, timeout=self.config.timeout
                ) as client,
                client.stream(
                    method,
                    self.config.endpoint + route,
                    content=body,
                    headers={"Content-Type": "application/json", "Accept": "application/json"},
                ) as response,
            ):
                if (
                    response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    != "application/json"
                ):
                    raise EngineTransportUnavailableError()
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > _LIMIT:
                        raise EngineTransportUnavailableError()
                status = response.status_code
            self.config.validate()
            data = parse_json(bytes(raw))
        except EngineCapabilityError:
            raise
        except Exception:
            raise EngineTransportUnavailableError() from None
        if status == 200:
            return data
        if set(data) == {"error"} and type(data["error"]) is str:
            refusal = {
                (400, "engine_invalid_body"): EngineRefusalCode.INVALID_BODY,
                (403, "engine_operation_denied"): EngineRefusalCode.OPERATION_DENIED,
                (403, "engine_resource_mismatch"): EngineRefusalCode.RESOURCE_MISMATCH,
                (503, "engine_profile_unavailable"): EngineRefusalCode.PROFILE_UNAVAILABLE,
            }
            if (status, data["error"]) == (409, "engine_revision_conflict"):
                raise EngineRevisionConflictError()
            if type(data["error"]) is str and (status, data["error"]) in refusal:
                raise EngineCapabilityError(refusal[(status, data["error"])])
        raise EngineTransportUnavailableError()

    async def readiness(self) -> None:
        data = await self._exchange("GET", "/maezo/v1/readiness")
        capabilities = data.get("capabilities")
        if (
            set(data) != {"protocol", "ready", "policy_digest", "capabilities"}
            or data["protocol"] != "maezo.engine-readiness.v1"
            or data["ready"] is not True
            or data["policy_digest"] != self.config.policy_digest
            or type(capabilities) is not list
            or any(type(item) is not str for item in capabilities)
            or len(capabilities) != len(set(capabilities))
            or set(capabilities) != {p.digest for p in self.config.profiles}
        ):
            raise EngineTransportUnavailableError()

    async def execute(self, request: EngineRequest, *, source_ref: str = "") -> Any:
        profile, body = self.project(request, source_ref=source_ref)
        # Validate locally before wire, then check current configured B policy/grants on
        # every operation. Start authorization also checks before durable claim/audit.
        # B v1 uses two requests: this is NOT atomic GET/POST policy binding; B retains
        # its independent transaction-time authorization and source checks.
        await self.readiness()
        data = await self._exchange("POST", "/maezo/v1/operations", body)
        if (
            set(data) != {"protocol", "capability_digest", "result"}
            or data["protocol"] != "maezo.engine-result.v1"
            or data["capability_digest"] != profile.digest
        ):
            raise EngineTransportUnavailableError()
        result = data["result"]
        if request.operation is EngineOperation.CORRELATE:
            if (
                type(result) is not dict
                or set(result) != {"correlated"}
                or type(result["correlated"]) is not int
                or result["correlated"] < 0
                or (not request.all_matching and result["correlated"] > 1)
            ):
                raise EngineTransportUnavailableError()
        else:
            rows = [result] if request.operation is EngineOperation.START else result
            if type(rows) is not list:
                raise EngineTransportUnavailableError()
            seen = set()
            for row in rows:
                start = request.operation is EngineOperation.START
                if (
                    type(row) is not dict
                    or set(row) != {"id", "definition_id", "tenant" if start else "state"}
                    or type(row["id"]) is not str
                    or not row["id"]
                    or row["id"] in seen
                    or row["definition_id"] != profile.target.definition_id
                    or (start and row["tenant"] != profile.identity.tenant)
                    or (not start and type(row["state"]) is not str)
                    or (
                        not start
                        and row["state"]
                        not in {
                            "ACTIVE",
                            "SUSPENDED",
                            "COMPLETED",
                            "EXTERNALLY_TERMINATED",
                            "INTERNALLY_TERMINATED",
                        }
                    )
                    or (request.operation is EngineOperation.READ_ACTIVE and row["state"] != "ACTIVE")
                ):
                    raise EngineTransportUnavailableError()
                seen.add(row["id"])
        return result

    async def close(self) -> None:
        self._closed = True
