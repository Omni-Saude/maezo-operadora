"""Always-enforcing D4 human authorization. No shadow/flip/test allowlist or agent gateway.

This service belongs inside the PHI boundary. Qualified decision ports preserve full
human basis in PHI and admit only a closed General projection. No source qualification
or production capability is inferred from port shape or successful unit substitution.
"""

import secrets
from collections.abc import Callable
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, TypeVar

from pydantic import TypeAdapter

from maezo.portal.api.session import HumanSessionResolver, ResolvedHumanSession
from maezo.portal.contracts.models import OpaqueRef, Revision, TaskDecision, TaskSnapshot
from maezo.portal.contracts.queues import (
    PublicTaskSnapshot,
    QueueFreshness,
    TaskQueueItem,
    TaskQueuePage,
    TaskQueueRequest,
    TaskReadResponse,
    utc,
)

from .completion import (
    COMPLETION_FORM_KEY,
    COMPLETION_OUTCOMES,
    COMPLETION_VARIABLES,
    CompletedTask,
    CompletionAck,
    CompletionAuditEntry,
    CompletionAuditSink,
    CompletionOutcome,
    CompletionRefusal,
    pseudonymize_notes,
)
from .credentials import HumanCommandCredentialPartition
from .decision import (
    AuthorizedDecision,
    BoundDecisionPorts,
    DecisionContext,
    DecisionCustodyRecord,
    PendingDecisionAdmission,
    QualifiedDecisionBinding,
    project_decision,
)
from .errors import GatewayRefusalError
from .models import (
    AssignmentCommand,
    AssignmentContext,
    AuthoritativeTask,
    AuthorizedAssignment,
    AuthorizedGovernedAssignment,
    CurrentTaskAuthority,
    GovernedAssignmentCandidates,
    GovernedAssignmentCommand,
    GovernedAssignmentContext,
    GovernedAssignmentReadContext,
    PendingAdmission,
    Scope,
)
from .ports import BoundGovernedAssignmentPorts, BoundHumanPorts
from .queue import (
    CandidateWindow,
    CatalogExpectation,
    CatalogExpectationSource,
    CatalogTrustAnchor,
    CursorCustody,
    CursorGrant,
    HumanTaskQuery,
    QueueBinding,
    ReadRefusalError,
    TaskDisclosureGrant,
    TaskDisclosureSource,
)
from .receipt import (
    BoundReceiptPorts,
    CurrentReceiptAuthority,
    PublicReceipt,
    ReceiptIdentity,
    parse_public_receipt,
)

_ReadResult = TypeVar("_ReadResult")

_PINS = (
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


class HumanGateway:
    """Trusted DI service, not an authentication endpoint accepting a principal DTO.

    Production authority/credential composition is absent; its factory refuses startup.
    Port substitution is useful for unit proofs, never evidence of a real durable outbox
    or atomic engine execution. Every public entry point resolves session/membership anew.
    """

    def __init__(
        self,
        *,
        resolver: HumanSessionResolver,
        scope: Scope,
        ports: BoundHumanPorts | None,
        credentials: HumanCommandCredentialPartition,
        governed_assignment_ports: BoundGovernedAssignmentPorts | None = None,
        receipt_ports: BoundReceiptPorts | None = None,
        decision_ports: BoundDecisionPorts | None = None,
        query: HumanTaskQuery | None = None,
        catalog_anchor: CatalogTrustAnchor | None = None,
        catalog_source: CatalogExpectationSource | None = None,
        cursor_custody: CursorCustody | None = None,
        disclosure_source: TaskDisclosureSource | None = None,
        completion_audit: CompletionAuditSink | None = None,
    ) -> None:
        self._scope = Scope.model_validate(scope)
        self._resolver = resolver
        self._ports = ports
        self._governed_assignment_ports = governed_assignment_ports
        self._credentials = credentials
        self._query = query
        self._catalog_anchor = catalog_anchor
        self._catalog_source = catalog_source
        self._cursor_custody = cursor_custody
        self._disclosure_source = disclosure_source
        self._receipt_ports = receipt_ports
        self._decision_ports = decision_ports
        # INTERIM (DL-0049): `None` keeps `complete_task` refusing, which is `main`'s behaviour.
        self._completion_audit = completion_audit
        self._check_scope()

    def _check_scope(self) -> None:
        self._credentials.for_workload(self._scope)
        if self._ports is None and self._governed_assignment_ports is None:
            raise GatewayRefusalError("production_capabilities_unavailable")
        provided: list[Any] = []
        if self._ports is not None:
            provided.extend((self._ports.task, self._ports.authority, self._ports.admission))
        if self._governed_assignment_ports is not None:
            g = self._governed_assignment_ports
            provided.extend((g.context, g.authority, g.candidates, g.admission))
        if self._resolver.settings.tenant != self._scope.tenant or any(
            port.scope != self._scope for port in provided
        ):
            raise GatewayRefusalError("credential_scope_mismatch")
        if self._receipt_ports is not None and any(
            port.scope != self._scope for port in (self._receipt_ports.store, self._receipt_ports.authority)
        ):
            raise GatewayRefusalError("credential_scope_mismatch")

        if self._decision_ports is not None and any(
            port.scope != self._scope
            for port in (
                self._decision_ports.binding,
                self._decision_ports.custody,
                self._decision_ports.admission,
            )
        ):
            raise GatewayRefusalError("credential_scope_mismatch")

    def _legacy_ports(self) -> BoundHumanPorts:
        if self._ports is None:
            raise GatewayRefusalError("production_capabilities_unavailable")
        return self._ports

    def _governed_ports(self) -> BoundGovernedAssignmentPorts:
        if self._governed_assignment_ports is None:
            raise GatewayRefusalError("production_capabilities_unavailable")
        return self._governed_assignment_ports

    async def read_receipt(self, *, session_secret: str, task_id: str, command_id: str) -> PublicReceipt:
        """Current resource authority permits historical receipts after task completion.

        The original task's active state and admission grant do not substitute for the
        receipt projector's current role/subject/consent policy. No browser principal
        or generic administrator override enters this read boundary.
        """
        resolved = await self._session(session_secret)
        try:
            task_id = TypeAdapter(OpaqueRef).validate_python(task_id)
            command_id = TypeAdapter(OpaqueRef).validate_python(command_id)
            if self._receipt_ports is None:
                raise ValueError("receipt ports unavailable")
            result = parse_public_receipt(
                await self._receipt_ports.store.read_owned(resolved.principal, task_id, command_id)
            )
            if (
                result.tenant != self._scope.tenant
                or result.task_id != task_id
                or result.command_id != command_id
                or result.principal_ref != resolved.principal.principal_ref
                or result.workload_ref != self._scope.workload_ref
            ):
                raise ValueError("receipt scope mismatch")
            identity = ReceiptIdentity.model_validate(
                result.model_dump(include=set(ReceiptIdentity.model_fields))
            )
            authority = CurrentReceiptAuthority.model_validate(
                await self._receipt_ports.authority.current_authority(resolved.principal, identity)
            )
            if (
                not authority.read_permitted
                or authority.identity != identity
                or authority.issuer != resolved.principal.issuer
                or authority.subject != resolved.principal.subject
                or authority.membership_revision != resolved.principal.membership_revision
            ):
                raise ValueError("receipt authority unavailable")
        except Exception:
            raise GatewayRefusalError("authority_unavailable") from None
        current = await self._session(session_secret)
        if current.principal != resolved.principal:
            raise GatewayRefusalError("revision_conflict")
        if authority.valid_until <= datetime.now(UTC):
            raise GatewayRefusalError("authority_unavailable")
        return result

    async def _session(
        self, secret: str, *, csrf: str | None = None, origin: str | None = None, mutation: bool = False
    ) -> ResolvedHumanSession:
        try:
            self._check_scope()
            resolved = await self._resolver.resolve(secret)
            if (
                resolved.membership.audience != "staff"
                or resolved.principal.tenant != self._scope.tenant
                or resolved.principal.principal_ref == self._scope.workload_ref
            ):
                raise ValueError("human scope required")
            if mutation and (
                origin != self._resolver.settings.public_origin
                or not csrf
                or not secrets.compare_digest(csrf, resolved.record.csrf_token)
            ):
                raise ValueError("csrf required")
            return resolved
        except Exception:
            raise GatewayRefusalError("authentication_unavailable") from None

    async def _authorized(
        self, resolved: ResolvedHumanSession, task_id: str, *, _read_taxonomy: bool = False
    ) -> tuple[AuthoritativeTask, CurrentTaskAuthority]:
        ports = self._legacy_ports()
        try:
            task = AuthoritativeTask.model_validate(await ports.task.read_task(task_id))
            snap = task.snapshot
            if (
                task.tenant != self._scope.tenant
                or snap.task_id != task_id
                or not task.active
                or task.valid_until <= datetime.now(UTC)
                or snap.snapshot_at > datetime.now(UTC)
            ):
                raise ValueError("task not current")
            # Unresolved JUEL is never a group, including for dynamic contract groups.
            if not snap.eligible_candidate_groups or any(
                "${" in group or "#{" in group for group in snap.eligible_candidate_groups
            ):
                raise ValueError("unresolved group")
        except ReadRefusalError:
            if _read_taxonomy:
                raise
            raise GatewayRefusalError("task_unavailable") from None
        except Exception:
            raise GatewayRefusalError("task_unavailable") from None
        principal = resolved.principal
        if not any(
            set(task.required_roles).issubset(m.roles)
            and set(snap.eligible_candidate_groups).intersection(m.groups)
            for m in principal.memberships
        ):
            raise GatewayRefusalError("operation_forbidden")
        if not set(task.required_subject_bindings).issubset(principal.subject_bindings):
            raise GatewayRefusalError("operation_forbidden")
        try:
            authority = CurrentTaskAuthority.model_validate(
                await ports.authority.current_authority(principal, task)
            )
            if (
                not authority.read_permitted
                or authority.tenant != self._scope.tenant
                or any(getattr(authority, pin) != getattr(snap, pin) for pin in _PINS)
                or authority.issuer != principal.issuer
                or authority.subject != principal.subject
                or authority.principal_ref != principal.principal_ref
                or authority.membership_revision != principal.membership_revision
                or authority.authority_revision != task.authority_revision
                or authority.task_revision != snap.task_revision
                or authority.evidence_revision != snap.evidence_revision
                or authority.evidence_digest != snap.evidence_digest
                or authority.valid_until <= datetime.now(UTC)
                or not set(task.required_consent_scopes).issubset(authority.consent_scopes)
            ):
                raise ValueError("authority not current")
        except ReadRefusalError:
            if _read_taxonomy:
                raise
            raise GatewayRefusalError("authority_unavailable") from None
        except Exception:
            raise GatewayRefusalError("authority_unavailable") from None
        return task, authority

    async def read_assignment_context(self, *, session_secret: str, task_id: str) -> AssignmentContext:
        first = await self._session(session_secret)
        try:
            task_id = TypeAdapter(OpaqueRef).validate_python(task_id)
        except Exception:
            raise GatewayRefusalError("operation_forbidden") from None
        task, authority = await self._authorized(first, task_id)
        actions = []
        for operation in ("claim", "release"):
            try:
                self._operation(operation, task, authority, first)
            except GatewayRefusalError:
                continue
            actions.append(operation)
        final = await self._session(session_secret)
        if final.principal != first.principal:
            raise GatewayRefusalError("revision_conflict")
        deadline = min(
            task.valid_until,
            authority.valid_until,
            first.record.expires_at,
            first.membership.reviewed_until,
            final.record.expires_at,
            final.membership.reviewed_until,
        )
        if deadline <= datetime.now(UTC):
            raise GatewayRefusalError("authority_unavailable")
        return AssignmentContext(
            snapshot=TaskSnapshot.model_validate(
                task.snapshot.model_copy(update={"allowed_actions": tuple(actions)})
            ),
            expected_membership_revision=first.principal.membership_revision,
            expected_authority_revision=task.authority_revision,
            valid_until=deadline,
        )

    async def read_task(self, *, session_secret: str, task_id: str) -> TaskSnapshot:
        resolved = await self._session(session_secret)
        try:
            task_id = TypeAdapter(OpaqueRef).validate_python(task_id)
        except Exception:
            raise GatewayRefusalError("task_unavailable") from None
        task, authority = await self._authorized(resolved, task_id)
        current = await self._session(session_secret)
        if current.principal != resolved.principal:
            raise GatewayRefusalError("revision_conflict")
        # Expose only operations this slice can admit under current assignment/authority.
        # The source snapshot stays immutable; unavailable decision bindings never appear active.
        actions = []
        for operation in task.snapshot.allowed_actions:
            if operation == "decision":
                continue
            try:
                self._operation(operation, task, authority, resolved)
            except GatewayRefusalError:
                continue
            actions.append(operation)
        # Both grants must survive authority I/O and the final session resolution.
        if min(task.valid_until, authority.valid_until) <= datetime.now(UTC):
            raise GatewayRefusalError("authority_unavailable")
        return TaskSnapshot.model_validate(
            task.snapshot.model_copy(update={"allowed_actions": tuple(actions)})
        )

    async def _read_session(self, secret: str) -> ResolvedHumanSession:
        # Additive read taxonomy; the original command/read entry points are unchanged.
        try:
            self._check_scope()
            result = await self._resolver.resolve(secret)
            if (
                result.principal.tenant != self._scope.tenant
                or result.principal.principal_ref == self._scope.workload_ref
            ):
                raise ValueError("identity scope")
            self._identity_deadlines((result,), datetime.now(UTC))
        except Exception:
            raise ReadRefusalError("session_unavailable") from None
        if result.membership.audience != "staff":
            raise ReadRefusalError("employee_access_required")
        return result

    @staticmethod
    def _identity_deadlines(
        sessions: tuple[ResolvedHumanSession, ...], now: datetime
    ) -> tuple[datetime, ...]:
        try:
            deadlines = tuple(
                utc(value)
                for session in sessions
                for value in (session.record.expires_at, session.membership.reviewed_until)
            )
            if any(value <= now for value in deadlines):
                raise ValueError("identity expired")
            return deadlines
        except Exception:
            raise ReadRefusalError("session_unavailable") from None

    async def _read_authorized(
        self, resolved: ResolvedHumanSession, task_id: str, *, queue: bool
    ) -> tuple[AuthoritativeTask, CurrentTaskAuthority]:
        try:
            return await self._authorized(resolved, task_id, _read_taxonomy=True)
        except ReadRefusalError as exc:
            if exc.code == "resource_unavailable":
                raise ReadRefusalError("refresh_required" if queue else "resource_unavailable") from None
            if exc.code in ("refresh_required", "read_dependency_unavailable"):
                raise
            raise ReadRefusalError("read_dependency_unavailable") from None
        except GatewayRefusalError as exc:
            if exc.code == "operation_forbidden":
                raise ReadRefusalError("refresh_required" if queue else "resource_unavailable") from None
            # The original source errors conflate denial, absence and uncertainty.
            raise ReadRefusalError("read_dependency_unavailable") from None

    async def _catalog(self) -> CatalogExpectation:
        if self._catalog_source is None or self._catalog_anchor is None:
            raise ReadRefusalError("read_dependency_unavailable")
        try:
            expected = CatalogExpectation.model_validate(
                await self._catalog_source.current_catalog(anchor=self._catalog_anchor)
            )
            if expected.anchor != self._catalog_anchor or expected.anchor.scope != self._scope:
                raise ValueError("catalog provenance")
            self._catalog_alive(expected, datetime.now(UTC))
            return expected
        except Exception:
            raise ReadRefusalError("read_dependency_unavailable") from None

    @staticmethod
    def _catalog_alive(expected: CatalogExpectation, now: datetime) -> None:
        if not expected.source_observed_at <= now < expected.valid_until:
            raise ReadRefusalError("read_dependency_unavailable")

    @staticmethod
    def _same_catalog(first: CatalogExpectation, second: CatalogExpectation) -> None:
        if first.anchor != second.anchor:
            raise ReadRefusalError("read_dependency_unavailable")
        if first.catalog_revision != second.catalog_revision:
            raise ReadRefusalError("refresh_required")
        if (
            first.catalog_digest != second.catalog_digest
            or second.source_observed_at < first.source_observed_at
        ):
            raise ReadRefusalError("read_dependency_unavailable")

    def _binding(
        self, session: ResolvedHumanSession, request: TaskQueueRequest, catalog: CatalogExpectation
    ) -> QueueBinding:
        return QueueBinding(
            scope=self._scope,
            principal=session.principal,
            queue=request.queue,
            limit=request.limit,
            catalog_revision=catalog.catalog_revision,
            catalog_ref=catalog.anchor.catalog_ref,
            publisher_ref=catalog.anchor.publisher_ref,
            catalog_digest=catalog.catalog_digest,
        )

    @staticmethod
    def _window_binding(actual: QueueBinding, expected: QueueBinding) -> None:
        if (
            actual.scope != expected.scope
            or actual.catalog_ref != expected.catalog_ref
            or actual.publisher_ref != expected.publisher_ref
        ):
            raise ReadRefusalError("read_dependency_unavailable")
        if actual.catalog_revision != expected.catalog_revision:
            raise ReadRefusalError("refresh_required")
        if actual.catalog_digest != expected.catalog_digest:
            raise ReadRefusalError("read_dependency_unavailable")
        if actual != expected:
            raise ReadRefusalError("refresh_required")

    def _read_guard(
        self,
        *,
        sessions: tuple[ResolvedHumanSession, ...],
        rows: tuple[tuple[AuthoritativeTask, CurrentTaskAuthority], ...],
        catalogs: tuple[CatalogExpectation, ...] = (),
        window: CandidateWindow | None = None,
        cursors: tuple[CursorGrant, ...] = (),
        disclosure: TaskDisclosureGrant | None = None,
    ) -> tuple[datetime, datetime]:
        now = datetime.now(UTC)
        deadlines = list(self._identity_deadlines(sessions, now))
        for expected in catalogs:
            self._catalog_alive(expected, now)
            deadlines.append(expected.valid_until)
        if window is not None:
            if not window.source_observed_at <= now < window.valid_until:
                raise ReadRefusalError("read_dependency_unavailable")
            deadlines.append(window.valid_until)
        for task, authority in rows:
            if task.snapshot.snapshot_at > now or min(task.valid_until, authority.valid_until) <= now:
                raise ReadRefusalError("read_dependency_unavailable")
            deadlines.extend((task.valid_until, authority.valid_until))
        for cursor in cursors:
            if cursor.valid_until <= now:
                raise ReadRefusalError("refresh_required")
            deadlines.append(cursor.valid_until)
        if disclosure is not None:
            if disclosure.valid_until <= now:
                raise ReadRefusalError("read_dependency_unavailable")
            deadlines.append(disclosure.valid_until)
        return now, min(deadlines)

    async def list_tasks(
        self,
        *,
        session_secret: str,
        request: TaskQueueRequest,
        render: Callable[[TaskQueuePage], _ReadResult],
    ) -> _ReadResult:
        """Q1 bounded live read. Render synchronously, then guard before HTTP handoff.

        The renderer must construct the complete response bytes/HTTP response without I/O.
        Callers must immediately return it; a delayed cache handoff is not authorized.
        """
        try:
            request = TaskQueueRequest.model_validate(request)
        except Exception:
            raise ReadRefusalError("invalid_request") from None
        first = await self._read_session(session_secret)
        try:
            if (
                self._query is None
                or self._cursor_custody is None
                or self._query.scope != self._scope
                or self._cursor_custody.scope != self._scope
            ):
                raise ReadRefusalError("read_dependency_unavailable")
            e0 = await self._catalog()
            binding = self._binding(first, request, e0)
            cursors: tuple[CursorGrant, ...] = ()
            if request.cursor is not None:
                incoming = CursorGrant.model_validate(
                    await self._cursor_custody.resolve(request.cursor, binding=binding)
                )
                if incoming.binding != binding or incoming.cursor != request.cursor:
                    raise ReadRefusalError("refresh_required")
                if incoming.valid_until <= datetime.now(UTC):
                    raise ReadRefusalError("refresh_required")
                cursors = (incoming,)
            window = CandidateWindow.model_validate(
                await self._query.discover(first.principal, request, expected_catalog=e0)
            )
            self._window_binding(window.binding, binding)
            if cursors and any(task_id <= cursors[0].after_task_id for task_id in window.task_ids):
                raise ReadRefusalError("refresh_required")
            candidates = []
            for task_id in window.task_ids:
                row = await self._read_authorized(first, task_id, queue=True)
                if request.queue == "mine" and row[0].snapshot.assignee_ref != first.principal.principal_ref:
                    raise ReadRefusalError("refresh_required")
                candidates.append(row)
            rows = tuple(candidates)
            e1 = await self._catalog()
            self._same_catalog(e0, e1)
            final = await self._read_session(session_secret)  # LAST dependency I/O.
            if final.principal != first.principal:
                raise ReadRefusalError("refresh_required")
            observed_at, valid_until = self._read_guard(
                sessions=(first, final), rows=rows, catalogs=(e0, e1), window=window, cursors=cursors
            )
            next_cursor = None
            if window.next_cursor is not None:
                outgoing = CursorGrant.model_validate(
                    self._cursor_custody.finalize(
                        window.next_cursor,
                        binding=binding,
                        after_task_id=window.task_ids[request.limit - 1],
                        valid_until=valid_until,
                    )
                )
                if (
                    outgoing.binding != binding
                    or outgoing.after_task_id != window.task_ids[request.limit - 1]
                    or outgoing.valid_until != valid_until
                ):
                    raise ReadRefusalError("read_dependency_unavailable")
                cursors += (outgoing,)
                next_cursor = outgoing.cursor
            items = tuple(
                TaskQueueItem(
                    task_id=task.snapshot.task_id,
                    process_definition_key=task.snapshot.process_definition_key,
                    task_definition_key=task.snapshot.task_definition_key,
                    task_revision=str(task.snapshot.task_revision),
                    ownership=(
                        "unassigned"
                        if task.snapshot.assignee_ref is None
                        else "self"
                        if task.snapshot.assignee_ref == first.principal.principal_ref
                        else "other"
                    ),
                    engine_due_at=task.snapshot.engine_due_at,
                    snapshot_at=task.snapshot.snapshot_at,
                )
                for task, _ in rows[: request.limit]
            )
            page = TaskQueuePage(
                queue=request.queue,
                items=items,
                next_cursor=next_cursor,
                freshness=QueueFreshness(
                    observed_at=observed_at,
                    source_observed_at=window.source_observed_at,
                    valid_until=valid_until,
                ),
            )
            result = render(page)
            self._read_guard(
                sessions=(first, final), rows=rows, catalogs=(e0, e1), window=window, cursors=cursors
            )  # Includes first row AND lookahead after serialization.
            return result
        except ReadRefusalError:
            raise
        except Exception:
            raise ReadRefusalError("read_dependency_unavailable") from None

    async def read_task_envelope(
        self,
        *,
        session_secret: str,
        task_id: str,
        render: Callable[[TaskReadResponse], _ReadResult],
    ) -> _ReadResult:
        try:
            task_id = TypeAdapter(OpaqueRef).validate_python(task_id)
        except Exception:
            raise ReadRefusalError("invalid_request") from None
        first = await self._read_session(session_secret)
        try:
            task, authority = await self._read_authorized(first, task_id, queue=False)
            if self._disclosure_source is None or self._disclosure_source.scope != self._scope:
                raise ReadRefusalError("read_dependency_unavailable")
            disclosure = TaskDisclosureGrant.model_validate(
                await self._disclosure_source.classify(first.principal, task, authority)
            )
            if (
                disclosure.scope != self._scope
                or disclosure.principal != first.principal
                or disclosure.snapshot != task.snapshot
                or disclosure.authority_revision != task.authority_revision
            ):
                raise ReadRefusalError("read_dependency_unavailable")
            final = await self._read_session(session_secret)  # LAST dependency I/O.
            if final.principal != first.principal:
                raise ReadRefusalError("refresh_required")
            observed_at, valid_until = self._read_guard(
                sessions=(first, final),
                rows=((task, authority),),
                disclosure=disclosure,
            )
            response = TaskReadResponse(
                task=PublicTaskSnapshot.from_snapshot(task.snapshot),
                freshness=QueueFreshness(
                    observed_at=observed_at,
                    source_observed_at=task.snapshot.snapshot_at,
                    valid_until=valid_until,
                ),
            )
            result = render(response)
            self._read_guard(sessions=(first, final), rows=((task, authority),), disclosure=disclosure)
            return result
        except ReadRefusalError:
            raise
        except Exception:
            raise ReadRefusalError("read_dependency_unavailable") from None

    async def complete_task(
        self, *, session_secret: str, csrf_token: str, task_id: str, resultado: str, notas_resolucao: str
    ) -> CompletedTask:
        """INTERIM (DL-0049): authorize a human, scrub the note, claim the revision, complete.

        The authorization is the EXISTING one, not a cheaper copy of it. `_read_session` proves
        the opaque session, the `staff` audience and both identity deadlines; `_authorized`
        reads the authoritative task through the trusted transport, refuses an inactive,
        stale-snapshot or unresolved-JUEL task, requires the caller's REQUIRED ROLES AND a
        candidate group to coincide in the SAME membership, checks subject bindings, then reads
        the independent authority projection and compares every pin/revision against the
        snapshot. Nothing here reads a group, a role or a revision from the request.

        Order is the contract, and it is `submit_assignment`'s order:

        1. session + CSRF (double-submit against the server-side token; the ORIGIN allowlist is
           the route's job, because CORS policy is deployment configuration, not authorization);
        2. the two remote reads (task, authority);
        3. the session AGAIN — after the remote calls, before the effect. A membership that
           moved under us is a revision conflict, and refusing here costs nothing because
           nothing has happened yet;
        4. the audit INTENT link. Its dedup claim is keyed on task + consumed revision, so two
           callers racing the same completion produce one claim and the loser is refused;
        5. the completion itself, LAST — the only step that changes the world;
        6. the audit RESULT link.

        After step 5 this method never vetoes: a deadline that lapsed during the engine call
        does not unmake a committed completion (`portal/engine/README.md`: a transport timeout
        "is uncertain, not evidence of rollback"). If step 6 fails the caller sees a refusal
        with the effect UNCERTAIN, and the committed intent link plus the engine's own state
        are what reconcile it — the same posture `HumanCommandRelay` documents.
        """
        try:
            task_id = TypeAdapter(OpaqueRef).validate_python(task_id)
        except Exception:
            raise ReadRefusalError("invalid_request") from None
        if resultado not in COMPLETION_OUTCOMES or not notas_resolucao.strip():
            # Belt: the route's closed contract already refused this with 422.
            raise ReadRefusalError("invalid_request")
        outcome: CompletionOutcome = resultado  # type: ignore[assignment]
        first = await self._read_session(session_secret)
        if not csrf_token or not secrets.compare_digest(csrf_token, first.record.csrf_token):
            raise ReadRefusalError("session_unavailable")
        audit = self._completion_audit
        if audit is None or audit.scope != self._scope:
            raise GatewayRefusalError("production_capabilities_unavailable")
        notes = pseudonymize_notes(notas_resolucao)
        task, authority = await self._authorized(first, task_id)
        self._completion_contract(task, first)
        current = await self._read_session(session_secret)
        if current.principal != first.principal:
            raise GatewayRefusalError("revision_conflict")
        snap = task.snapshot
        if min(task.valid_until, authority.valid_until, current.record.expires_at) <= datetime.now(
            UTC
        ) or current.membership.reviewed_until <= datetime.now(UTC):
            raise GatewayRefusalError("authority_unavailable")
        entry = CompletionAuditEntry(
            scope=self._scope,
            phase="intent",
            principal_ref=current.principal.principal_ref,
            session_ref=current.record.session_ref,
            membership_revision=current.principal.membership_revision,
            snapshot=snap,
            authority_revision=task.authority_revision,
            resultado=outcome,
            notes_digest=notes.digest,
        )
        try:
            intent_ref, deduped = await audit.record(entry)
        except GatewayRefusalError:
            raise
        except Exception:
            raise GatewayRefusalError("admission_unavailable") from None
        if deduped:
            # Someone already committed to completing THIS revision. Never complete twice.
            raise GatewayRefusalError("revision_conflict")
        await self._revalidate_before_effect(
            session_secret, first=first, current=current, task=task, authority=authority, entry=entry
        )
        acknowledgement = await self._legacy_ports().task.complete_task(
            task_id,
            resultado=outcome,
            notes=notes,
            expected_task_revision=snap.task_revision,
        )
        if (
            type(acknowledgement) is not CompletionAck
            or acknowledgement.task_id != task_id
            or acknowledgement.resultado != outcome
            or acknowledgement.consumed_task_revision != snap.task_revision
        ):
            raise GatewayRefusalError("admission_unavailable")
        try:
            result_ref, result_deduped = await audit.record(
                CompletionAuditEntry(
                    scope=self._scope,
                    phase="result",
                    principal_ref=current.principal.principal_ref,
                    session_ref=current.record.session_ref,
                    membership_revision=current.principal.membership_revision,
                    snapshot=snap,
                    authority_revision=task.authority_revision,
                    resultado=outcome,
                    notes_digest=notes.digest,
                    outcome="completed",
                )
            )
            if result_deduped:
                raise ValueError("orphaned completion result")
        except GatewayRefusalError:
            raise
        except Exception:
            # The completion IS committed; only its result link is missing. Say "uncertain".
            raise GatewayRefusalError("admission_unavailable") from None
        return CompletedTask(
            snapshot=snap,
            resultado=outcome,
            authority_revision=task.authority_revision,
            consumed_task_revision=acknowledgement.consumed_task_revision,
            completed_at=acknowledgement.completed_at,
            audit_intent_ref=intent_ref,
            audit_result_ref=result_ref,
        )

    async def _revalidate_before_effect(
        self,
        session_secret: str,
        *,
        first: ResolvedHumanSession,
        current: ResolvedHumanSession,
        task: AuthoritativeTask,
        authority: CurrentTaskAuthority,
        entry: CompletionAuditEntry,
    ) -> None:
        """Ultima cerca antes do unico passo que muda o mundo (DL-0049 review, P1).

        O QUE ESTAVA ERRADO. A revalidacao (passo 3) acontecia ANTES de gravar o elo de intent
        (passo 4), e o intent e' um INSERT sob advisory lock por tenant: sob contencao ele espera.
        Se a autoridade fosse revogada ou o prazo lapsasse durante essa espera, o efeito ainda
        seguia para o motor — e este caminho e' o REST cru, sem a cerca otimista do rele duravel.

        O QUE ELE CHECA, e o que ele DELIBERADAMENTE nao checa. Ele releia a sessao para pegar
        REVOGACAO (principal trocado, revisao de membership movida, `reviewed_until` lapsado) e
        recheca os prazos ABSOLUTOS da tarefa e da projecao de autoridade. Ele NAO recusa por
        `record.expires_at` — o cookie da sessao — e a assimetria e' escolhida: a pessoa clicou
        com sessao valida, e um cookie que expira durante um INSERT nao torna a decisao dela
        invalida; recusar ali transformaria um evento benigno numa tarefa travada (ver abaixo).

        O QUE A RECUSA CUSTA, declarado porque nao e' de graca. A claim de intent JA foi gravada,
        e o `dedup_key` dela e' tarefa + revisao consumida — nao inclui quem pediu. Entao, depois
        desta recusa, NINGUEM completa aquela revisao por este caminho: a proxima tentativa cai
        em `deduped` e recebe `revision_conflict`. Isso e' conservador na direcao certa (nunca
        completar duas vezes, nunca completar sem autoridade) e e' o preco de nao deixar passar um
        efeito sem lastro. Para nao virar um pendurado indecifravel, a claim e' FECHADA aqui com
        `outcome="refused_authority_lapsed"`: quem auditar le' "o intent existe, o efeito NAO
        aconteceu, e o motivo foi autoridade", em vez de ter de adivinhar entre isso e um timeout.

        POR QUE O MOTIVO NAO VAI PARA UM LOG. Este modulo nao tem logger, e a ausencia e'
        deliberada: ele levanta recusa TIPADA e quem loga e' a rota (`portal/api/tasks.py`), que
        e' onde a requisicao existe. O motivo desta recusa em particular precisa sobreviver ao
        mes, nao ao buffer do CloudWatch — entao ele vira o `outcome` do elo de result, que e'
        registro duravel e encadeado.

        Se o proprio elo de recusa falhar, a recusa ao chamador continua sendo a mesma: o efeito
        nao aconteceu de qualquer forma, e mentir sobre a auditoria seria pior que perde-la.
        """
        try:
            antes_do_efeito: ResolvedHumanSession | None = await self._read_session(session_secret)
        except ReadRefusalError:
            # O COOKIE venceu (ou foi embora) durante a gravacao do intent. Isto NAO recusa, e a
            # assimetria e' a razao de ser deste bloco: a pessoa clicou com sessao valida, e a
            # claim de intent tem `dedup_key` de tarefa + revisao — sem quem pediu. Recusar aqui
            # travaria aquela revisao para TODO MUNDO por um evento benigno. O que se perde e' a
            # deteccao de REVOGACAO neste turno; os prazos absolutos abaixo continuam valendo.
            antes_do_efeito = None
        # `agora` DEPOIS da releitura, de proposito: a releitura e' a unica coisa que demora aqui,
        # e o instante que importa e' o mais proximo possivel do efeito.
        agora = datetime.now(UTC)
        revogado = antes_do_efeito is not None and (
            antes_do_efeito.principal != first.principal
            or antes_do_efeito.principal.membership_revision != current.principal.membership_revision
            or antes_do_efeito.membership.reviewed_until <= agora
        )
        if not revogado and min(task.valid_until, authority.valid_until) > agora:
            return
        audit = self._completion_audit
        if audit is not None:
            desfecho: CompletionRefusal = (
                "refused_membership_revoked" if revogado else "refused_deadline_lapsed"
            )
            # `suppress` com o motivo no docstring: nada foi enviado ao motor, entao a recusa ao
            # chamador e' a mesma com ou sem o elo. Perder o elo e' ruim; mentir sobre ele, pior.
            with suppress(Exception):
                await audit.record(replace(entry, phase="result", outcome=desfecho))
        raise GatewayRefusalError("authority_unavailable")

    @staticmethod
    def _completion_contract(task: AuthoritativeTask, resolved: ResolvedHumanSession) -> None:
        """Only an ESCALATION human task whose form contract declares these two outputs.

        The form key and `allowed_inputs` come from the authoritative snapshot, which
        `portal/contracts/models.py::_INPUTS_BY_FORM` maps to exactly
        `("resultado", "notas_resolucao")` for `escalation`. A task with another contract is
        refused instead of being completed with variables it never declared — there is no
        free-variable map on this path.

        The assignment check is NOT in the mandate's list and is deliberately stricter than it:
        a task already held by another colleague is not closed by a candidate-group peer. It
        cannot narrow the mandate's scenario (the battery opens unassigned tasks) and it closes
        the obvious way a portal completion could steal someone else's case.
        """
        snap = task.snapshot
        if (
            snap.form_key != COMPLETION_FORM_KEY
            or snap.form_source_status != "BPMN_FORMDATA"
            or not set(COMPLETION_VARIABLES).issubset(snap.allowed_inputs)
            or (snap.assignee_ref is not None and snap.assignee_ref != resolved.principal.principal_ref)
        ):
            raise GatewayRefusalError("operation_forbidden")

    @staticmethod
    def _expectations(
        command: AssignmentCommand | GovernedAssignmentCommand | TaskDecision,
        task: AuthoritativeTask,
        resolved: ResolvedHumanSession,
        revision: int,
    ) -> None:
        snap = task.snapshot
        if (
            any(getattr(command, pin) != getattr(snap, pin) for pin in _PINS)
            or command.expected_task_revision != snap.task_revision
            or command.expected_evidence_revision != snap.evidence_revision
            or command.expected_evidence_digest != snap.evidence_digest
            or command.expected_membership_revision != resolved.principal.membership_revision
            or revision != task.authority_revision
        ):
            raise GatewayRefusalError("revision_conflict")

    @staticmethod
    def _operation(
        operation: str,
        task: AuthoritativeTask,
        authority: CurrentTaskAuthority,
        resolved: ResolvedHumanSession,
        *,
        qualified_decision: bool = False,
    ) -> None:
        snap = task.snapshot
        if (
            operation not in snap.allowed_actions
            or operation not in authority.permitted_operations
            or (operation == "claim" and snap.assignee_ref is not None)
            or (
                operation in ("release", "decision") and snap.assignee_ref != resolved.principal.principal_ref
            )
        ):
            raise GatewayRefusalError("operation_forbidden")
        if snap.form_source_status != "BPMN_FORMDATA" and not (
            operation == "decision" and qualified_decision
        ):
            raise GatewayRefusalError("form_contract_unavailable")

    async def submit_assignment(
        self, *, session_secret: str, csrf_token: str, origin: str, command: AssignmentCommand
    ) -> PendingAdmission:
        resolved = await self._session(session_secret, csrf=csrf_token, origin=origin, mutation=True)
        try:
            command = AssignmentCommand.model_validate(command)
        except Exception:
            raise GatewayRefusalError("operation_forbidden") from None
        task, authority = await self._authorized(resolved, command.task_id)
        self._expectations(command, task, resolved, command.expected_authority_revision)
        self._operation(command.operation, task, authority, resolved)
        current = await self._session(session_secret, csrf=csrf_token, origin=origin, mutation=True)
        if current.principal != resolved.principal:
            raise GatewayRefusalError("revision_conflict")
        if min(task.valid_until, authority.valid_until) <= datetime.now(UTC):
            raise GatewayRefusalError("authority_unavailable")
        try:
            result = PendingAdmission.model_validate(
                await self._legacy_ports().admission.admit(
                    AuthorizedAssignment(
                        scope=self._scope,
                        workload_ref=self._scope.workload_ref,
                        principal=resolved.principal,
                        snapshot=task.snapshot,
                        authority_revision=task.authority_revision,
                        command=command,
                    )
                )
            )
            if (
                result.tenant != self._scope.tenant
                or result.task_id != command.task_id
                or result.command_id != command.command_id
                or result.principal_ref != resolved.principal.principal_ref
                or result.workload_ref != self._scope.workload_ref
                or result.committed_at > datetime.now(UTC)
            ):
                raise ValueError("invalid acknowledgement")
            return result
        except Exception:
            raise GatewayRefusalError("admission_unavailable") from None

    @staticmethod
    def _governed_deadline(
        sessions: tuple[ResolvedHumanSession, ...],
        contexts: tuple[GovernedAssignmentReadContext, ...],
        extra: tuple[datetime, ...] = (),
    ) -> datetime:
        limits = [
            v for session in sessions for v in (session.record.expires_at, session.membership.reviewed_until)
        ]
        limits.extend(
            v for context in contexts for v in (context.task.valid_until, context.context.valid_until)
        )
        limits.extend(extra)
        deadline = min(limits)
        if deadline <= datetime.now(UTC):
            raise GatewayRefusalError("authority_unavailable")
        return deadline

    async def read_governed_assignment_context(
        self, *, session_secret: str, task_id: str
    ) -> GovernedAssignmentContext:
        from .assignment_transport import validate_context

        ports = self._governed_ports()
        first = await self._session(session_secret)
        task_id = TypeAdapter(OpaqueRef).validate_python(task_id)
        bound = GovernedAssignmentReadContext.model_validate(
            await ports.context.read_context(first.principal, task_id)
        )
        if (
            bound.principal != first.principal
            or bound.scope != self._scope
            or bound.context.task_id != task_id
        ):
            raise GatewayRefusalError("authority_unavailable")
        validate_context(first.principal, self._scope, bound.task, bound.context)
        final = await self._session(session_secret)
        if final.principal != first.principal:
            raise GatewayRefusalError("revision_conflict")
        deadline = self._governed_deadline((first, final), (bound,))
        return GovernedAssignmentContext.model_validate(
            {**bound.context.model_dump(), "valid_until": deadline}
        )

    async def list_governed_assignment_candidates(
        self, *, session_secret: str, task_id: str
    ) -> GovernedAssignmentCandidates:
        ports = self._governed_ports()
        first = await self._session(session_secret)
        task_id = TypeAdapter(OpaqueRef).validate_python(task_id)
        bound = GovernedAssignmentReadContext.model_validate(
            await ports.context.read_context(first.principal, task_id)
        )
        if (
            bound.principal != first.principal
            or bound.scope != self._scope
            or bound.context.task_id != task_id
        ):
            raise GatewayRefusalError("authority_unavailable")
        if "reassign" not in bound.context.allowed_operations:
            raise GatewayRefusalError("operation_forbidden")
        result = GovernedAssignmentCandidates.model_validate(
            await ports.candidates.list_candidates(first.principal, bound)
        )
        if result.context.model_dump(exclude={"valid_until"}) != bound.context.model_dump(
            exclude={"valid_until"}
        ):
            raise GatewayRefusalError("revision_conflict")
        final = await self._session(session_secret)
        if final.principal != first.principal:
            raise GatewayRefusalError("revision_conflict")
        deadline = self._governed_deadline(
            (first, final), (bound,), (result.valid_until, result.context.valid_until)
        )
        return GovernedAssignmentCandidates.model_validate({**result.model_dump(), "valid_until": deadline})

    async def submit_governed_assignment(
        self, *, session_secret: str, csrf_token: str, origin: str, command: GovernedAssignmentCommand
    ) -> PendingAdmission:
        from .assignment_transport import validate_authority, validate_context

        ports = self._governed_ports()
        first = await self._session(session_secret, csrf=csrf_token, origin=origin, mutation=True)
        command = GovernedAssignmentCommand.model_validate(command)
        bound = GovernedAssignmentReadContext.model_validate(
            await ports.context.read_context(first.principal, command.task_id)
        )
        if bound.principal != first.principal or bound.scope != self._scope:
            raise GatewayRefusalError("authority_unavailable")
        validate_context(first.principal, self._scope, bound.task, bound.context)
        self._expectations(command, bound.task, first, command.expected_authority_revision)
        c = bound.context
        if command.expected_assignee_ref != c.assignee_ref or any(
            str(getattr(command, "expected_" + name)) != str(getattr(c, name))
            for name in (
                "binding_ref",
                "binding_version",
                "binding_digest",
                "policy_ref",
                "policy_version",
                "policy_digest",
                "source_revision",
                "generation_digest",
            )
        ):
            raise GatewayRefusalError("revision_conflict")
        authority = CurrentTaskAuthority.model_validate(
            await ports.authority.current_authority(
                first.principal,
                bound,
                command.operation,
                command.target_ref,
                command.expected_target_membership_revision,
            )
        )
        validate_authority(first.principal, bound, authority, command.operation)
        final = await self._session(session_secret, csrf=csrf_token, origin=origin, mutation=True)
        if final.principal != first.principal:
            raise GatewayRefusalError("revision_conflict")
        deadline = self._governed_deadline((first, final), (bound,), (authority.valid_until,))
        result = PendingAdmission.model_validate(
            await ports.admission.admit(
                AuthorizedGovernedAssignment(
                    scope=self._scope,
                    principal=first.principal,
                    read_context=bound,
                    authority=authority,
                    command=command,
                    valid_until=deadline,
                )
            )
        )
        if (
            result.tenant != self._scope.tenant
            or result.task_id != command.task_id
            or result.command_id != command.command_id
            or result.principal_ref != first.principal.principal_ref
            or result.workload_ref != self._scope.workload_ref
            or result.committed_at > datetime.now(UTC)
            or result.committed_at >= deadline
        ):
            raise GatewayRefusalError("admission_unavailable")
        return result

    async def _decision_binding(
        self, resolved: ResolvedHumanSession, task: AuthoritativeTask, authority: CurrentTaskAuthority
    ) -> QualifiedDecisionBinding:
        try:
            if self._decision_ports is None or task.snapshot.form_key not in (
                "auth_decisao",
                "auth_junta",
                "auth_pendencia",
                "escalation",
                "pagto_admissibilidade",
            ):
                raise ValueError("decision ports unavailable")
            result = QualifiedDecisionBinding.model_validate(
                await self._decision_ports.binding.qualify(resolved.principal, task, authority)
            )
            if (
                result.scope != self._scope
                or result.principal != resolved.principal
                or result.task != task
                or result.authority != authority
                or result.valid_until <= datetime.now(UTC)
            ):
                raise ValueError("decision qualification unavailable")
            return result
        except Exception:
            raise GatewayRefusalError("form_projection_unavailable") from None

    @staticmethod
    def _decision_deadline(
        sessions: tuple[ResolvedHumanSession, ...],
        bindings: tuple[QualifiedDecisionBinding, ...],
        custody: DecisionCustodyRecord | None = None,
    ) -> datetime:
        limits = [
            limit
            for session in sessions
            for limit in (session.record.expires_at, session.membership.reviewed_until)
        ] + [
            limit
            for binding in bindings
            for limit in (binding.valid_until, binding.task.valid_until, binding.authority.valid_until)
        ]
        if custody is not None:
            limits.append(custody.valid_until)
        deadline = min(limits)
        if deadline <= datetime.now(UTC):
            raise GatewayRefusalError("authority_unavailable")
        return deadline

    async def read_decision_context(self, *, session_secret: str, task_id: str) -> DecisionContext:
        """Current assigned-task mutation context, separate from the immutable Q1 read API."""
        first = await self._session(session_secret)
        try:
            task_id = TypeAdapter(OpaqueRef).validate_python(task_id)
        except Exception:
            raise GatewayRefusalError("operation_forbidden") from None
        task, authority = await self._authorized(first, task_id)
        binding = await self._decision_binding(first, task, authority)
        self._operation("decision", task, authority, first, qualified_decision=True)
        final = await self._session(session_secret)
        if final.principal != first.principal:
            raise GatewayRefusalError("revision_conflict")
        return DecisionContext(
            schema_version=1,
            snapshot=task.snapshot,
            expected_membership_revision=first.principal.membership_revision,
            expected_authority_revision=task.authority_revision,
            binding_digest=binding.binding_digest,
            valid_until=self._decision_deadline((first, final), (binding,)),
        )

    async def submit_decision(
        self,
        *,
        session_secret: str,
        csrf_token: str,
        origin: str,
        decision: TaskDecision,
        expected_authority_revision: int,
        expected_binding_digest: str | None = None,
    ) -> PendingDecisionAdmission:
        resolved = await self._session(session_secret, csrf=csrf_token, origin=origin, mutation=True)
        try:
            decision = TaskDecision.model_validate(decision)
            revision = TypeAdapter(Revision).validate_python(expected_authority_revision)
        except Exception:
            raise GatewayRefusalError("operation_forbidden") from None
        task, authority = await self._authorized(resolved, decision.task_id)
        self._expectations(decision, task, resolved, revision)
        # Preserve the historical refusal for callers without any qualified composition.
        if self._decision_ports is None:
            self._operation("decision", task, authority, resolved)
            raise GatewayRefusalError("form_projection_unavailable")
        binding = await self._decision_binding(resolved, task, authority)
        self._operation("decision", task, authority, resolved, qualified_decision=True)
        if binding.binding_digest != expected_binding_digest:
            raise GatewayRefusalError("revision_conflict")
        if not set(decision.inputs.model_fields_set - {"kind"}).issubset(task.snapshot.allowed_inputs):
            raise GatewayRefusalError("operation_forbidden")
        self._decision_deadline((resolved,), (binding,))
        try:
            request = AuthorizedDecision(
                scope=self._scope,
                principal=resolved.principal,
                decision=decision,
                authority_revision=revision,
                binding_digest=binding.binding_digest,
                evidence_ref=binding.evidence_ref,
            )
            custody = DecisionCustodyRecord.model_validate(
                await self._decision_ports.custody.preserve(request)
            )
            classified = project_decision(request, custody)
        except Exception:
            raise GatewayRefusalError("form_projection_unavailable") from None
        # PHI custody may have awaited I/O. Refresh task, authority, qualification and
        # session before General admission; D5 still serializes current state at execution.
        latest_task, latest_authority = await self._authorized(resolved, decision.task_id)
        self._expectations(decision, latest_task, resolved, revision)
        self._operation("decision", latest_task, latest_authority, resolved, qualified_decision=True)
        latest_binding = await self._decision_binding(resolved, latest_task, latest_authority)
        if (
            latest_binding.binding_digest != binding.binding_digest
            or latest_binding.evidence_ref != binding.evidence_ref
        ):
            raise GatewayRefusalError("revision_conflict")
        current = await self._session(session_secret, csrf=csrf_token, origin=origin, mutation=True)
        if current.principal != resolved.principal:
            raise GatewayRefusalError("revision_conflict")
        deadline = self._decision_deadline((resolved, current), (binding, latest_binding), custody)
        try:
            result = PendingDecisionAdmission.model_validate(
                await self._decision_ports.admission.admit(classified, valid_until=deadline)
            )
            if (
                result.tenant != self._scope.tenant
                or result.task_id != decision.task_id
                or result.command_id != decision.command_id
                or result.principal_ref != resolved.principal.principal_ref
                or result.workload_ref != self._scope.workload_ref
                or result.payload_digest != classified.digest
                or result.request_digest != classified.request_digest
                or result.audit_intent_ref != classified.audit_intent_ref
                or result.committed_at > datetime.now(UTC)
                or result.committed_at >= deadline
            ):
                raise ValueError("invalid acknowledgement")
            return result
        except Exception:
            # May be committed already. Caller retains immutable identity for recovery.
            raise GatewayRefusalError("admission_unavailable") from None


def create_production_gateway(*, resolver: HumanSessionResolver, scope: Scope) -> HumanGateway:
    """Fail closed until verified authority and dedicated credentials are provisioned.

    No mode flag, supplied arbitrary ports, in-memory persistence or noop credentials can
    make this factory activate. A later reviewed package must compose the D6 adapters
    with verified authority, key isolation and classified form contracts. Configuration refusal is
    not proof of engine enforcement, network isolation or durable acceptance.
    """
    raise GatewayRefusalError("production_capabilities_unavailable")
