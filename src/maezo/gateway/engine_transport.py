"""D7-C workload HTTP boundary, consuming the frozen D7-B v1 interface.

Configuration is deployment-owned, never agent-supplied. Readiness proves the server's
current profile/definition grants; source authority is checked by B in its transaction.
No client-created EngineAuthority, raw REST fallback, or automatic retries of mutations.
"""

from __future__ import annotations

import copy
import hashlib
import math
import re
import ssl
import tempfile
from dataclasses import asdict, dataclass, field
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
from maezo.gateway.engine_lifetime import (
    CallerSelection,
    LifetimeBorrower,
    LocalLifetimeOwner,
    ProfileSelectionKey,
    Submission,
    SubmissionSnapshot,
    _Opaque,
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


class EngineOperationsClient(_Opaque):
    """Only the two fixed workload routes. Each request uses a freshly validated TLS identity."""

    def __init__(self, config: EngineTLSConfig) -> None:
        if type(config) is not EngineTLSConfig:
            raise EngineTransportUnavailableError()
        self._config = copy.deepcopy(config)
        self._config.context()
        document = asdict(self._config)
        for name in ("ca_file", "certificate_file", "private_key_file"):
            document[name] = str(document[name])
        # Profile documents are the original v1 canonical codec, including schema,
        # identity, target and source target; no local ID changes those bytes.
        document["profiles"] = [p.document() for p in self._config.profiles]
        document["identity"] = self._config.profiles[0].document()["identity"]
        config_digest = hashlib.sha256(canonical_json(document)).hexdigest()
        selection = CallerSelection(
            canonical_json(document["identity"]),
            config_digest,
            tuple(
                ProfileSelectionKey(canonical_json(p.document()), p.digest, config_digest)
                for p in self._config.profiles
            ),
        )
        self._lifetime = LocalLifetimeOwner(selection)
        self._direct = self._lifetime.borrow(tuple(p.digest for p in self._config.profiles))

    @property
    def config(self) -> EngineTLSConfig:
        """Independent snapshot: neither input nor returned nested objects are shared."""
        return copy.deepcopy(self._config)

    @property
    def selection(self) -> CallerSelection:
        return self._lifetime.selection

    @property
    def submissions(self) -> tuple[SubmissionSnapshot, ...]:
        return self._lifetime.submissions

    def borrow(self, process_key: str) -> EngineOperationBorrower:
        self._direct.check()
        profiles = tuple(p for p in self._config.profiles if p.target.process_key == process_key)
        if not profiles:
            raise EngineCapabilityError(EngineRefusalCode.PROFILE_UNAVAILABLE)
        admitted = tuple(
            p.digest
            for p in profiles
            if p.schema.operation in _SUPPORTED
            and not p.schema.source_process_key
            and p.source_target is None
        )
        if not admitted:
            raise EngineCapabilityError(EngineRefusalCode.EVIDENCE_UNAVAILABLE)
        return EngineOperationBorrower(self, self._lifetime.borrow(admitted), process_key)

    def profile(
        self, operation: EngineOperation, process_key: str, message: str = ""
    ) -> EngineCapabilityProfile:
        self._direct.check()
        return self._profile(operation, process_key, message)

    def _profile(
        self, operation: EngineOperation, process_key: str, message: str = ""
    ) -> EngineCapabilityProfile:
        if operation not in _SUPPORTED:
            raise EngineCapabilityError(EngineRefusalCode.OPERATION_DENIED)
        for profile in self._config.profiles:
            if (
                profile.schema.operation is operation
                and profile.target.process_key == process_key
                and profile.target.message == message
            ):
                if profile.schema.source_process_key or profile.source_target is not None:
                    raise EngineCapabilityError(EngineRefusalCode.EVIDENCE_UNAVAILABLE)
                return copy.deepcopy(profile)
        raise EngineCapabilityError(EngineRefusalCode.PROFILE_UNAVAILABLE)

    def project(
        self, request: EngineRequest, *, source_ref: str = ""
    ) -> tuple[EngineCapabilityProfile, bytes]:
        self._direct.check()
        return self._project(request, source_ref=source_ref)

    def _project(
        self, request: EngineRequest, *, source_ref: str = ""
    ) -> tuple[EngineCapabilityProfile, bytes]:
        if type(request) is not EngineRequest:
            raise EngineCapabilityError(EngineRefusalCode.INVALID_BODY)
        request.__post_init__()
        profile = self._profile(request.operation, request.process_key, request.message)
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
        # A bare pointer cannot provide the reviewed native acquisition lifetime.
        # Source-required calls await authentic D/native adapters in later stages.
        if type(source_ref) is not str or source_ref:
            raise EngineCapabilityError(EngineRefusalCode.EVIDENCE_UNAVAILABLE)
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
        try:
            context = self._config.context()
            async with (
                httpx.AsyncClient(
                    verify=context, trust_env=False, follow_redirects=False, timeout=self._config.timeout
                ) as client,
                client.stream(
                    method,
                    self._config.endpoint + route,
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
            self._config.validate()
            data = parse_json(bytes(raw))
        except EngineCapabilityError:
            # Invalid/unreadable response bytes are not a native refusal receipt.
            raise EngineTransportUnavailableError() from None
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
        await self._direct.run_async(self._readiness)

    async def _readiness(self) -> None:
        data = await self._exchange("GET", "/maezo/v1/readiness")
        capabilities = data.get("capabilities")
        if (
            set(data) != {"protocol", "ready", "policy_digest", "capabilities"}
            or data["protocol"] != "maezo.engine-readiness.v1"
            or data["ready"] is not True
            or data["policy_digest"] != self._config.policy_digest
            or type(capabilities) is not list
            or any(type(item) is not str for item in capabilities)
            or len(capabilities) != len(set(capabilities))
            or set(capabilities) != {p.digest for p in self._config.profiles}
        ):
            raise EngineTransportUnavailableError()

    async def execute(self, request: EngineRequest, *, source_ref: str = "") -> Any:
        return await self._execute(self._direct, request, source_ref=source_ref)

    async def _execute(
        self, borrower: LifetimeBorrower, request: EngineRequest, *, source_ref: str = ""
    ) -> Any:
        self._lifetime._check(borrower)
        borrower.check(selection=self.selection)
        if type(request) is not EngineRequest:
            raise EngineCapabilityError(EngineRefusalCode.INVALID_BODY)
        frozen = copy.deepcopy(request)
        profile, body = self._project(frozen, source_ref=source_ref)
        borrower.check(profile.digest)
        submission = self._lifetime.reserve(
            borrower, profile.digest, frozen.operation.value, hashlib.sha256(body).hexdigest()
        )
        try:
            return await borrower.run_async(
                lambda: self._perform(borrower, submission, frozen, profile, body)
            )
        except BaseException:
            # Cancellation may race a known terminal acknowledgement. Transition
            # that exact record only; never erase it or retry the mutation.
            submission.ambiguous()
            raise

    async def _perform(
        self,
        borrower: LifetimeBorrower,
        submission: Submission,
        request: EngineRequest,
        profile: EngineCapabilityProfile,
        body: bytes,
    ) -> Any:
        try:
            # GET/POST is not atomic policy binding; native B retains its existing
            # independent transaction-time checks. No local selector replaces them.
            await self._readiness()
            borrower.check(profile.digest)
            submission.submitted()
            data = await self._exchange("POST", "/maezo/v1/operations", body)
            value = self._result(request, profile, data)
        except EngineTransportUnavailableError:
            submission.ambiguous()
            raise
        except EngineCapabilityError:
            submission.refused()
            raise
        except BaseException:
            submission.ambiguous()
            raise
        submission.acknowledged(hashlib.sha256(canonical_json({"result": value})).hexdigest())
        return value

    def _result(self, request: EngineRequest, profile: EngineCapabilityProfile, data: dict[str, Any]) -> Any:
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

    async def close(self, timeout: float | None = None) -> bool:  # noqa: ASYNC109 - drain, not cancellation
        return await self._lifetime.close(timeout)


class EngineOperationBorrower(_Opaque):
    """Exact-process local borrower; owns no client, TLS credentials or native grant."""

    __slots__ = ("_client", "_borrower", "_process_key")

    def __init__(self, client: EngineOperationsClient, borrower: LifetimeBorrower, process_key: str) -> None:
        client._lifetime._check(borrower)
        self._client = client
        self._borrower = borrower
        self._process_key = process_key

    def profile(
        self, operation: EngineOperation, process_key: str, message: str = ""
    ) -> EngineCapabilityProfile:
        self._client._lifetime._check(self._borrower)
        self._borrower.check(selection=self._client.selection)
        if process_key != self._process_key:
            raise EngineCapabilityError(EngineRefusalCode.RESOURCE_MISMATCH)
        profile = self._client._profile(operation, process_key, message)
        self._borrower.check(profile.digest)
        return profile

    def project(
        self, request: EngineRequest, *, source_ref: str = ""
    ) -> tuple[EngineCapabilityProfile, bytes]:
        self.profile(request.operation, request.process_key, request.message)
        return self._client._project(request, source_ref=source_ref)

    async def readiness(self) -> None:
        self._client._lifetime._check(self._borrower)
        self._borrower.check(selection=self._client.selection)
        await self._borrower.run_async(self._client._readiness)

    async def execute(self, request: EngineRequest, *, source_ref: str = "") -> Any:
        self.profile(request.operation, request.process_key, request.message)
        return await self._client._execute(self._borrower, request, source_ref=source_ref)

    async def close(self, timeout: float | None = None) -> bool:  # noqa: ASYNC109 - drain, not cancellation
        return await self._borrower.close(timeout)
