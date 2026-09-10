"""Concrete pinned AMH reads, composed only by gateway.tool_registry (ADR-0037).

Runtime bindings are deployment inputs, not authorization grants. Capability and
ratification still pass through the existing effect gateway on every call. The
canonical consent producer and tenant-bound durable audit sink are mandatory.
No human principal, caller-selected destination, credential or redirect is used.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit

import httpx
import yaml
from jsonschema import Draft4Validator

from maezo.adapters.amh.subject_context import (
    OPERATIONS,
    READS,
    AmhSubjectContextAdapter,
    GovernedSubjectContextExecutor,
    GovernedSubjectContextRequest,
    GovernedSubjectContextResponse,
)
from maezo.gateway.action_execution import REASON_APPROVED
from maezo.gateway.audit import AuditRecord, hash_input
from maezo.gateway.audit_postgres import PostgresAuditSink
from maezo.gateway.credential_vault import AgentCredentialView, CredentialVault
from maezo.gateway.effect_pep import PHI_ZONE_PHI
from maezo.gateway.rate_limit import REASON_RATE_LIMITED
from maezo.gateway.seams._base import EffectDeniedError, GatedSeam, SeamContext, gate
from maezo.ports.consent import ConsentDecision, ConsentDecisionSource
from maezo.ports.errors import PortFailureReason as Reason
from maezo.ports.errors import PortResult

_MAX_RESPONSE_BYTES = 1_048_576
_MAX_TIMEOUT_SECONDS = 30.0  # Technical IO cap; no business/consent expiry is inferred.
_PREFIX = "/interop/subject-context/v1/subjects/"
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")


class AmhCompositionError(ValueError):
    def __init__(self) -> None:
        super().__init__("amh_runtime_binding_invalid")


@dataclass(frozen=True, slots=True, repr=False)
class AmhRuntime:
    """Explicit server composition; credentials never come from graph call arguments.

    The registry obtains an agent-only vault view. Token rotation requires a new
    runtime composition; the upstream still authenticates that token on each read.
    ConsentSource must be the current publisher for this tenant/legal entity.
    Its existing contract owns revocation/expiry semantics; no local TTL is made up.
    """

    tenant: str
    legal_entity_ref: str
    principal: str
    agent_version: str
    origin: str
    purpose_of_use: str
    openapi_bytes: bytes = field(repr=False)
    credentials: CredentialVault = field(repr=False)
    consent: ConsentDecisionSource = field(repr=False)
    audit: PostgresAuditSink = field(repr=False)
    pin_path: Path | None = None

    def __post_init__(self) -> None:
        try:
            for value in (self.tenant, self.legal_entity_ref, self.principal, self.agent_version):
                if type(value) is not str or not _TOKEN.fullmatch(value):
                    raise ValueError
            origin = urlsplit(self.origin)
            if (
                origin.scheme != "https"
                or not origin.hostname
                or not self.origin.isascii()
                or origin.username is not None
                or origin.password is not None
                or origin.path
                or origin.query
                or origin.fragment
                or origin.port == 0
                or self.origin != "https://" + origin.netloc
            ):
                raise ValueError
            if not isinstance(self.credentials, CredentialVault):
                raise ValueError
            if not isinstance(self.consent, ConsentDecisionSource):
                raise ValueError
            # Existing durable sink has no public tenant accessor. Verify its actual
            # constructor-bound tenant rather than trusting an arbitrary emit callback.
            if not isinstance(self.audit, PostgresAuditSink) or self.audit._tenant_id != self.tenant:
                raise ValueError
        except Exception:
            raise AmhCompositionError() from None

    @property
    def credential_key(self) -> str:
        """A deployment-scoped agent credential name; never a grant or PHI identifier."""
        binding = hash_input([self.tenant, self.legal_entity_ref, self.origin])
        return "amh_subject_context_" + binding


class GatedAmhContext(AmhSubjectContextAdapter, GatedSeam):
    """ClinicalContextPort plus the existing readiness marker; no FHIR alias."""

    def __init__(
        self, *, runtime: AmhRuntime, seam: SeamContext, executor: AmhSubjectContextExecutor
    ) -> None:
        AmhSubjectContextAdapter.__init__(
            self, runtime.openapi_bytes, executor=executor, pin_path=runtime.pin_path
        )
        GatedSeam.__init__(self, executor, seam=seam)

    async def aclose(self) -> None:
        """Stop accepting reads; every request owns and closes its HTTP transport."""
        await self._inner.aclose()


class AmhSubjectContextExecutor(GovernedSubjectContextExecutor):
    """Actual gateway -> durable audit -> fixed-origin streamed GET implementation."""

    registered_operations = frozenset(OPERATIONS.values())

    def __init__(self, *, runtime: AmhRuntime, seam: SeamContext, credentials: AgentCredentialView) -> None:
        if runtime.tenant != seam.tenant or runtime.principal != seam.principal:
            raise AmhCompositionError()
        self._seam = seam
        self._tenant = runtime.tenant
        self._principal = runtime.principal
        self._version = runtime.agent_version
        self._legal_entity = runtime.legal_entity_ref
        self._origin = runtime.origin
        self._purpose = runtime.purpose_of_use
        self._credential_key = runtime.credential_key
        self._consent = runtime.consent
        self._audit = runtime.audit
        self._credentials = credentials
        self._closed = False
        self._active: set[asyncio.Task[Any]] = set()
        self.context = GatedAmhContext(runtime=runtime, seam=seam, executor=self)
        # The existing adapter just verified these exact bytes with the immutable pin.
        # Reuse canonical parameter schemas; do not copy their regex/purpose vocabulary.
        document = yaml.safe_load(runtime.openapi_bytes)
        parameters = document["components"]["parameters"]
        self._parameters: dict[str, tuple[dict[str, Any], ...]] = {}
        for name, (suffix, _) in READS.items():
            operation = document["paths"]["/subjects/{portable_subject_ref}/context" + suffix]["get"]
            self._parameters[OPERATIONS[name]] = tuple(
                parameters[ref["$ref"].rsplit("/", 1)[1]] for ref in operation["parameters"]
            )
        purpose_schema = parameters["PurposeOfUse"]["schema"]
        if not Draft4Validator(purpose_schema).is_valid(runtime.purpose_of_use):
            raise AmhCompositionError()

    async def aclose(self) -> None:
        self._closed = True
        current = asyncio.current_task()
        pending = [task for task in self._active if task is not current]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    def _request(self, request: GovernedSubjectContextRequest) -> tuple[str, str]:
        if type(request) is not GovernedSubjectContextRequest or request.method != "GET":
            raise ValueError
        name = next((name for name, op in OPERATIONS.items() if op == request.operation), None)
        if name is None or not request.path.startswith(_PREFIX):
            raise ValueError
        suffix = "/context" + READS[name][0]
        if not request.path.endswith(suffix):
            raise ValueError
        encoded = request.path[len(_PREFIX) : -len(suffix)]
        subject = unquote(encoded, errors="strict")
        if not encoded or quote(subject, safe="") != encoded:
            raise ValueError
        if type(request.query) is not tuple:
            raise ValueError
        query = dict(request.query)
        if len(query) != len(request.query):
            raise ValueError
        expected_names = {p["name"] for p in self._parameters[request.operation] if p["in"] == "query"}
        if set(query) - expected_names:
            raise ValueError
        for parameter in self._parameters[request.operation]:
            value = subject if parameter["in"] == "path" else query.get(parameter["name"])
            if value is None and not parameter.get("required", False):
                continue
            if not Draft4Validator(parameter["schema"]).is_valid(value):
                raise ValueError
        if (
            type(request.timeout_seconds) not in (int, float)
            or not math.isfinite(request.timeout_seconds)
            or not 0 < request.timeout_seconds <= _MAX_TIMEOUT_SECONDS
            or type(request.consent_decision_ref) is not str
            or not request.consent_decision_ref.strip()
            or len(request.consent_decision_ref) > 4096
        ):
            raise ValueError
        return subject, str(query["purpose_of_use"])

    async def execute(
        self, request: GovernedSubjectContextRequest
    ) -> PortResult[GovernedSubjectContextResponse]:
        if self._closed:
            return PortResult.refused(Reason.UPSTREAM_UNAVAILABLE)
        task = asyncio.current_task()
        if task is None:
            return PortResult.refused(Reason.UPSTREAM_UNAVAILABLE)
        self._active.add(task)
        try:
            return await self._execute(request)
        finally:
            self._active.discard(task)

    async def _execute(
        self, request: GovernedSubjectContextRequest
    ) -> PortResult[GovernedSubjectContextResponse]:
        try:
            subject, purpose = self._request(request)
        except Exception:
            return PortResult.refused(Reason.INVALID_REQUEST)
        if self._closed:
            return PortResult.refused(Reason.UPSTREAM_UNAVAILABLE)
        if self._seam.phi_zone != PHI_ZONE_PHI:
            return PortResult.refused(Reason.SCOPE_NOT_SUPPORTED)
        if purpose != self._purpose:
            return PortResult.refused(Reason.PURPOSE_DENIED)
        try:
            async with asyncio.timeout(request.timeout_seconds):
                decision = await gate(self._seam, request.operation)
                # This is a newly protected endpoint: shadow denial is not permission,
                # and an environment shadow override is not a ratified approval.
                if not decision.allow or decision.reason != REASON_APPROVED:
                    return PortResult.refused(Reason.SCOPE_NOT_SUPPORTED)
                try:
                    token = self._credentials.get(self._credential_key)
                    if (
                        type(token) is not str
                        or not token
                        or not token.isascii()
                        or any(char.isspace() or ord(char) < 33 or ord(char) == 127 for char in token)
                    ):
                        raise ValueError
                except Exception:
                    return PortResult.refused(Reason.NOT_AUTHENTICATED)
                consent = await self._consent.latest_decision(
                    subject, purpose_of_use=purpose, timeout_seconds=request.timeout_seconds
                )
                if not isinstance(consent, PortResult) or consent.succeeded is not True:
                    return PortResult.refused(Reason.CONSENT_REQUIRED)
                granted = consent.value
                if (
                    not isinstance(granted, ConsentDecision)
                    or granted.granted is not True
                    or granted.portable_subject_ref != subject
                    or granted.purpose_of_use != purpose
                    or granted.consent_decision_ref != request.consent_decision_ref
                    or type(granted.consent_revision) is not int
                    or granted.consent_revision < 0
                ):
                    return PortResult.refused(Reason.CONSENT_REQUIRED)
                record = AuditRecord(
                    tenant_id=self._tenant,
                    agent_id=self._principal,
                    agent_version=self._version,
                    action=request.operation,
                    decision="ALLOW",
                    details={
                        "phase": "READ_AUTHORIZED_BEFORE_DISPATCH",
                        "purpose_of_use": self._purpose,
                        "request_sha256": hash_input([request.operation, request.path, request.query]),
                        "consent_decision_sha256": hash_input(request.consent_decision_ref),
                        "consent_revision": granted.consent_revision,
                        "legal_entity_sha256": hash_input(self._legal_entity),
                        "origin_sha256": hashlib.sha256(self._origin.encode()).hexdigest(),
                    },
                )
                receipt = await self._audit.emit(record)
                if type(receipt) is not str or not re.fullmatch(r"[0-9a-f]{64}", receipt):
                    return PortResult.refused(Reason.UPSTREAM_UNAVAILABLE)
                if self._closed:
                    return PortResult.refused(Reason.UPSTREAM_UNAVAILABLE)
                # Direct public transport API avoids AsyncClient's URL-bearing INFO
                # request log. No cookie jar, environment proxy, redirect or retry layer.
                if any(
                    logging.getLogger(name).isEnabledFor(logging.DEBUG)
                    for name in ("httpcore.connection", "httpcore.http11", "httpcore.http2", "httpcore.proxy")
                ):
                    # httpcore DEBUG traces include raw response headers. Refuse this
                    # PHI transport rather than change global logging configuration.
                    return PortResult.refused(Reason.UPSTREAM_UNAVAILABLE)
                async with httpx.AsyncHTTPTransport(retries=0, trust_env=False) as transport:
                    http_request = httpx.Request(
                        "GET",
                        self._origin + request.path,
                        params=request.query,
                        headers={
                            "Authorization": "Bearer " + token,
                            "Accept": "application/json",
                            "Accept-Encoding": "identity",
                        },
                        extensions={
                            "timeout": dict.fromkeys(
                                ("connect", "read", "write", "pool"), request.timeout_seconds
                            )
                        },
                    )
                    response = await transport.handle_async_request(http_request)
                    try:
                        if response.status_code in (400, 401, 404, 429) or response.status_code >= 500:
                            reason = {
                                400: Reason.INVALID_REQUEST,
                                401: Reason.NOT_AUTHENTICATED,
                                404: Reason.NOT_FOUND,
                                429: Reason.RATE_LIMITED,
                            }.get(response.status_code, Reason.UPSTREAM_UNAVAILABLE)
                            return PortResult.refused(reason)
                        if response.headers.get("content-encoding", "identity") != "identity":
                            return PortResult.refused(Reason.CONTRACT_VIOLATION)
                        if 300 <= response.status_code < 400:
                            return PortResult.refused(Reason.UPSTREAM_UNAVAILABLE)
                        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
                        if content_type != "application/json":
                            return PortResult.refused(Reason.CONTRACT_VIOLATION)
                        raw = bytearray()
                        async for chunk in response.aiter_raw(chunk_size=65_536):
                            raw.extend(chunk)
                            if len(raw) > _MAX_RESPONSE_BYTES:
                                return PortResult.refused(Reason.CONTRACT_VIOLATION)
                        return PortResult.ok(GovernedSubjectContextResponse(response.status_code, bytes(raw)))
                    finally:
                        await response.aclose()
        except asyncio.CancelledError:
            raise
        except (TimeoutError, httpx.TimeoutException):
            return PortResult.refused(Reason.TIMEOUT)
        except EffectDeniedError as denied:
            return PortResult.refused(
                Reason.RATE_LIMITED
                if denied.decision.reason == REASON_RATE_LIMITED
                else Reason.SCOPE_NOT_SUPPORTED
            )
        except Exception:
            return PortResult.refused(Reason.UPSTREAM_UNAVAILABLE)
