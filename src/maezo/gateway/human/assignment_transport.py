"""Fixed native E03 routes with current purpose-specific keys and authenticated TLS."""

from __future__ import annotations

import base64
import hashlib
import ssl
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from sqlalchemy.ext.asyncio import AsyncEngine

from maezo.portal.contracts.models import HumanPrincipal
from maezo.portal.engine.profile import canonicalize, strict_loads

from .credentials import AssignmentSigningLease
from .errors import GatewayRefusalError
from .models import (
    AssignmentCandidate,
    AuthoritativeTask,
    AuthorizedGovernedAssignment,
    CurrentTaskAuthority,
    GovernedAssignmentCandidates,
    GovernedAssignmentContext,
    GovernedAssignmentReadContext,
    Scope,
)
from .ports import (
    GovernedAssignmentAuthorityProjection,
    GovernedAssignmentCandidateProjection,
    GovernedAssignmentContextTransport,
)
from .projection import EvidenceReference, GovernedEvidenceReferenceSource
from .read_profile import digest, parse_model, wire


def unavailable() -> GatewayRefusalError:
    return GatewayRefusalError("production_capabilities_unavailable")


class AssignmentPrivateTransport:
    """One owned HTTP pool per qualified read/publication credential; no arbitrary URL."""

    def __init__(
        self,
        *,
        origin: str,
        tls_context: ssl.SSLContext,
        server_spki_sha256: str,
        signing: AssignmentSigningLease,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
            or not tls_context.check_hostname
            or tls_context.verify_mode != ssl.CERT_REQUIRED
            or timeout_seconds <= 0
            or signing.purpose not in ("human-authority", "human-assignment-read")
            or len(server_spki_sha256) != 64
            or any(c not in "0123456789abcdef" for c in server_spki_sha256)
        ):
            raise unavailable()
        signing.guard()
        self.scope, self.purpose = signing.scope, signing.purpose
        self._signing, self._origin, self._server_pin = signing, origin.rstrip("/"), server_spki_sha256
        self._http = httpx.AsyncClient(
            verify=tls_context,
            transport=transport,
            timeout=timeout_seconds,
            trust_env=False,
            follow_redirects=False,
        )
        self._closed = False

    def _peer(self, response: httpx.Response) -> None:
        stream = response.extensions.get("network_stream")
        tls = stream.get_extra_info("ssl_object") if stream is not None else None
        if tls is None:
            raise unavailable()
        cert = x509.load_der_x509_certificate(tls.getpeercert(binary_form=True))
        raw = cert.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        if hashlib.sha256(raw).hexdigest() != self._server_pin:
            raise unavailable()

    async def publish(self, raw: bytes) -> dict[str, Any]:
        if self.purpose != "human-authority":
            raise unavailable()
        return await self._send("authority", raw)

    async def query(self, request: dict[str, Any]) -> dict[str, Any]:
        if self.purpose != "human-assignment-read" or request.get("operation") not in (
            "context",
            "candidates",
            "authority",
        ):
            raise unavailable()
        return await self._send("assignment-" + request["operation"], canonicalize(request))

    async def receipt_authority(self, request: dict[str, Any]) -> dict[str, Any]:
        if self.purpose != "human-assignment-read":
            raise unavailable()
        return await self._send("assignment-receipt-authority", canonicalize(request))

    async def _send(self, route: str, raw: bytes) -> dict[str, Any]:
        try:
            if self._closed:
                raise unavailable()
            request = strict_loads(raw)
            if (
                canonicalize(request) != raw
                or request.get("tenant") != self.scope.tenant
                or request.get("workload_ref") != self.scope.workload_ref
            ):
                raise unavailable()
            now = self._signing.guard()
            issued = int(now.timestamp())
            expires = min(
                issued + self._signing.max_envelope_seconds, int(self._signing.not_after.timestamp())
            )
            if expires <= issued:
                raise unavailable()
            outer = dict(
                schema="human-envelope.v1",
                purpose=self.purpose,
                algorithm="Ed25519",
                audience=self._signing.audience,
                issuer=self.scope.workload_ref,
                tenant=self.scope.tenant,
                key_id=self._signing.key_id,
                issued_at=str(issued),
                expires_at=str(expires),
                digest=digest(request),
                command=request,
            )
            outer["signature"] = (
                base64.urlsafe_b64encode(self._signing.sign(canonicalize(outer))).rstrip(b"=").decode("ascii")
            )
            body = canonicalize(outer)
            if len(body) > 65536:
                raise unavailable()
            async with self._http.stream(
                "POST",
                self._origin + "/maezo-human/v1/" + route,
                content=body,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            ) as response:
                self._peer(response)
                if (
                    "set-cookie" in response.headers
                    or response.headers.get("content-type", "").split(";")[0] != "application/json"
                    or response.headers.get("cache-control") != "no-store"
                ):
                    raise unavailable()
                result = bytearray()
                async for chunk in response.aiter_bytes():
                    result.extend(chunk)
                    if len(result) > 65536:
                        raise unavailable()
                    self._signing.guard()
                parsed = strict_loads(bytes(result))
                if response.status_code != 200:
                    if parsed in ({"error": "REVISION_CONFLICT"}, {"error": "COMMAND_CONFLICT"}):
                        raise GatewayRefusalError("revision_conflict")
                    if parsed == {"error": "FORBIDDEN"}:
                        raise GatewayRefusalError("operation_forbidden")
                    raise unavailable()
                if not isinstance(parsed, dict):
                    raise unavailable()
                if parsed.get("request_digest") != digest(request):
                    raise unavailable()
                self._signing.guard()
                return parsed
        except GatewayRefusalError:
            raise
        except Exception:
            raise unavailable() from None

    async def aclose(self) -> None:
        self._closed = True
        await self._http.aclose()


PINS = (
    "task_id",
    "process_definition_key",
    "process_definition_version",
    "process_definition_id",
    "process_definition_digest",
    "task_definition_key",
    "form_key",
    "form_version",
    "form_digest",
)


def validate_context(
    principal: HumanPrincipal, scope: Scope, task: AuthoritativeTask, context: GovernedAssignmentContext
) -> None:
    s = task.snapshot
    now = datetime.now(UTC)
    if (
        principal.tenant != scope.tenant
        or task.tenant != scope.tenant
        or not task.active
        or min(task.valid_until, context.valid_until) <= now
        or s.snapshot_at > now
        or any(str(getattr(s, k)) != str(getattr(context, k)) for k in PINS)
        or str(s.task_revision) != context.expected_task_revision
        or str(s.evidence_revision) != context.expected_evidence_revision
        or s.evidence_digest != context.expected_evidence_digest
        or str(principal.membership_revision) != context.expected_membership_revision
        or str(task.authority_revision) != context.expected_authority_revision
        or s.assignee_ref != context.assignee_ref
        or tuple(s.allowed_actions) != context.allowed_operations
        or not s.eligible_candidate_groups
        or any("${" in g or "#{" in g for g in s.eligible_candidate_groups)
        or not any(
            set(task.required_roles).issubset(m.roles)
            and set(s.eligible_candidate_groups).intersection(m.groups)
            for m in principal.memberships
        )
        or not set(task.required_subject_bindings).issubset(principal.subject_bindings)
    ):
        raise unavailable()


def validate_authority(
    principal: HumanPrincipal,
    context: GovernedAssignmentReadContext,
    authority: CurrentTaskAuthority,
    requested_operation: str,
) -> None:
    t, s = context.task, context.task.snapshot
    if (
        authority.tenant != context.scope.tenant
        or any(getattr(authority, k) != getattr(s, k) for k in PINS)
        or (authority.issuer, authority.subject, authority.principal_ref, authority.membership_revision)
        != (principal.issuer, principal.subject, principal.principal_ref, principal.membership_revision)
        or authority.authority_revision != t.authority_revision
        or authority.task_revision != s.task_revision
        or authority.evidence_revision != s.evidence_revision
        or authority.evidence_digest != s.evidence_digest
        or not authority.read_permitted
        or authority.permitted_operations != (requested_operation,)
        or requested_operation not in context.context.allowed_operations
        or not set(t.required_consent_scopes).issubset(authority.consent_scopes)
        or authority.valid_until <= datetime.now(UTC)
    ):
        raise unavailable()


class NativeAssignmentClient(
    GovernedAssignmentContextTransport,
    GovernedAssignmentAuthorityProjection,
    GovernedAssignmentCandidateProjection,
    GovernedEvidenceReferenceSource,
):
    def __init__(
        self,
        *,
        transport: AssignmentPrivateTransport,
        source_engine: AsyncEngine,
        command_scope: Scope,
        engine_name: str,
        database_incarnation: str,
    ) -> None:
        if transport.purpose != "human-assignment-read" or source_engine.dialect.name != "postgresql":
            raise unavailable()
        scope = Scope.model_validate(command_scope)
        if (scope.tenant, scope.environment) != (
            transport.scope.tenant,
            transport.scope.environment,
        ) or scope.workload_ref == transport.scope.workload_ref:
            raise unavailable()
        self.scope, self._transport = scope, transport
        self._source_engine = source_engine
        self.engine_name, self.database_incarnation = engine_name, database_incarnation

    async def _active(self, expected: GovernedAssignmentContext | None = None) -> None:
        from sqlalchemy import text

        from maezo.gateway.audit_postgres import schema_for_tenant

        async with self._source_engine.begin() as db:
            schema = schema_for_tenant(self.scope.tenant)
            await db.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            if (await db.execute(text("SELECT current_schema()"))).scalar_one() != schema:
                raise unavailable()
            row = (
                (
                    await db.execute(
                        text(
                            "SELECT "
                            "state,source_revision,active_generation_digest,pending_publication_id "
                            "FROM portal_assignment_source WHERE tenant=:tenant"
                        ),
                        {"tenant": self.scope.tenant},
                    )
                )
                .mappings()
                .first()
            )
            if row is None or row["state"] != "active" or row["pending_publication_id"] is not None:
                raise unavailable()
            if expected is not None and (
                str(row["source_revision"]) != expected.source_revision
                or row["active_generation_digest"] != expected.generation_digest
            ):
                raise GatewayRefusalError("revision_conflict")

    def _bound(self, principal: HumanPrincipal, context: GovernedAssignmentReadContext) -> None:
        if context.principal != principal or context.scope != self.scope:
            raise unavailable()
        validate_context(principal, self.scope, context.task, context.context)

    async def _query(
        self,
        principal: HumanPrincipal,
        task_id: str,
        operation: str,
        context: GovernedAssignmentReadContext | None = None,
        requested_operation: str | None = None,
        target_ref: str | None = None,
        target_membership_revision: int | None = None,
    ) -> tuple[
        GovernedAssignmentReadContext, EvidenceReference, CurrentTaskAuthority | None, object, datetime
    ]:
        if context is not None:
            self._bound(principal, context)  # full actor/scope equality BEFORE any I/O
        if principal.tenant != self.scope.tenant:
            raise unavailable()
        await self._active(None if context is None else context.context)
        request = dict(
            schema="human-assignment-query.v1",
            operation=operation,
            tenant=self.scope.tenant,
            workload_ref=self._transport.scope.workload_ref,
            principal_ref=principal.principal_ref,
            principal_issuer=principal.issuer,
            principal_subject=principal.subject,
            task_id=task_id,
            expected_context=wire(context.context) if operation == "authority" and context else None,
            requested_operation=requested_operation,
            target_ref=target_ref,
            target_membership_revision=None
            if target_membership_revision is None
            else str(target_membership_revision),
        )
        result = await self._transport.query(request)
        if (
            set(result)
            != {
                "schema",
                "request_digest",
                "context",
                "candidates",
                "authority",
                "task",
                "evidence",
                "valid_until",
            }
            or result["schema"] != "human-assignment-query-result.v1"
        ):
            raise unavailable()
        public = parse_model(GovernedAssignmentContext, result["context"])
        task = parse_model(AuthoritativeTask, result["task"])
        evidence = parse_model(EvidenceReference, result["evidence"])
        # Timestamps retain Q2's existing UTC representation; integers use the shared codec.
        from .models import Timed

        deadline = parse_model(Timed, {"valid_until": result["valid_until"]}).valid_until
        bound = GovernedAssignmentReadContext(
            principal=principal, scope=self.scope, task=task, context=public
        )
        self._bound(principal, bound)
        if (
            public.task_id != task_id
            or evidence.tenant != self.scope.tenant
            or evidence.task_id != task_id
            or evidence.revision != task.snapshot.evidence_revision
            or evidence.digest != task.snapshot.evidence_digest
            or evidence.valid_until > min(deadline, public.valid_until, task.valid_until)
        ):
            raise unavailable()
        if min(deadline, evidence.valid_until) <= datetime.now(UTC):
            raise unavailable()
        if context is not None and context.context.model_dump(exclude={"valid_until"}) != public.model_dump(
            exclude={"valid_until"}
        ):
            raise GatewayRefusalError("revision_conflict")
        authority = None
        if operation == "authority":
            if result["candidates"] is not None or requested_operation is None:
                raise unavailable()
            authority = parse_model(CurrentTaskAuthority, result["authority"])
            validate_authority(principal, bound, authority, requested_operation)
        elif result["authority"] is not None or (operation == "context" and result["candidates"] is not None):
            raise unavailable()
        await self._active(public)
        self._bound(principal, bound)
        if min(deadline, evidence.valid_until) <= datetime.now(UTC):
            raise unavailable()
        return bound, evidence, authority, result["candidates"], deadline

    async def read_context(self, principal: HumanPrincipal, task_id: str) -> GovernedAssignmentReadContext:
        return (await self._query(principal, task_id, "context"))[0]

    async def current_authority(
        self,
        principal: HumanPrincipal,
        context: GovernedAssignmentReadContext,
        requested_operation: str,
        target_ref: str | None,
        target_membership_revision: int | None,
    ) -> CurrentTaskAuthority:
        if requested_operation not in ("claim", "release", "reassign") or (
            requested_operation == "reassign"
        ) != (target_ref is not None and target_membership_revision is not None):
            raise unavailable()
        if requested_operation != "reassign" and (
            target_ref is not None or target_membership_revision is not None
        ):
            raise unavailable()
        result = await self._query(
            principal,
            context.context.task_id,
            "authority",
            context,
            requested_operation,
            target_ref,
            target_membership_revision,
        )
        assert result[2] is not None
        return result[2]

    async def list_candidates(
        self, principal: HumanPrincipal, context: GovernedAssignmentReadContext
    ) -> GovernedAssignmentCandidates:
        self._bound(principal, context)
        if "reassign" not in context.context.allowed_operations:
            raise GatewayRefusalError("operation_forbidden")
        bound, _, _, raw, deadline = await self._query(
            principal, context.context.task_id, "candidates", context
        )
        if not isinstance(raw, list):
            raise unavailable()
        candidates = tuple(parse_model(AssignmentCandidate, c) for c in raw)
        return GovernedAssignmentCandidates(
            schema_version="portal-assignment-candidates.v1",
            context=bound.context,
            candidates=candidates,
            candidate_count=str(len(candidates)),
            candidate_digest=digest(candidates),
            valid_until=min(deadline, bound.context.valid_until),
        )

    async def current_reference(self, assignment: AuthorizedGovernedAssignment) -> EvidenceReference:
        a = AuthorizedGovernedAssignment.model_validate(assignment)
        self._bound(a.principal, a.read_context)
        if a.scope != self.scope or a.valid_until <= datetime.now(UTC):
            raise unavailable()
        c = a.command
        _, evidence, authority, _, _ = await self._query(
            a.principal,
            c.task_id,
            "authority",
            a.read_context,
            c.operation,
            c.target_ref,
            c.expected_target_membership_revision,
        )
        if authority is None or authority.model_dump(exclude={"valid_until"}) != a.authority.model_dump(
            exclude={"valid_until"}
        ):
            raise GatewayRefusalError("revision_conflict")
        return evidence
