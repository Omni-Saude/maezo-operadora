"""Always-enforcing D4 human authorization. No shadow/flip/test allowlist or agent gateway.

This service belongs inside the PHI boundary. Current decision DTOs can contain mandatory
raw human justification; no classified projection contract exists for these bindings.
They are refused intact before admission; no fields are dropped, scrubbed or remapped.
"""

import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TypeVar

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

from .credentials import HumanCommandCredentialPartition
from .errors import GatewayRefusalError
from .models import (
    AssignmentCommand,
    AuthoritativeTask,
    AuthorizedAssignment,
    CurrentTaskAuthority,
    PendingAdmission,
    Scope,
)
from .ports import BoundHumanPorts
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
from .receipt import BoundReceiptPorts, CurrentReceiptAuthority, PublicReceipt, ReceiptIdentity

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
        ports: BoundHumanPorts,
        credentials: HumanCommandCredentialPartition,
        receipt_ports: BoundReceiptPorts | None = None,
        query: HumanTaskQuery | None = None,
        catalog_anchor: CatalogTrustAnchor | None = None,
        catalog_source: CatalogExpectationSource | None = None,
        cursor_custody: CursorCustody | None = None,
        disclosure_source: TaskDisclosureSource | None = None,
    ) -> None:
        self._scope = Scope.model_validate(scope)
        self._resolver = resolver
        self._ports = ports
        self._credentials = credentials
        self._query = query
        self._catalog_anchor = catalog_anchor
        self._catalog_source = catalog_source
        self._cursor_custody = cursor_custody
        self._disclosure_source = disclosure_source
        self._receipt_ports = receipt_ports
        self._check_scope()

    def _check_scope(self) -> None:
        self._credentials.for_workload(self._scope)
        if self._resolver.settings.tenant != self._scope.tenant or any(
            port.scope != self._scope
            for port in (self._ports.task, self._ports.authority, self._ports.admission)
        ):
            raise GatewayRefusalError("credential_scope_mismatch")
        if self._receipt_ports is not None and any(
            port.scope != self._scope for port in (self._receipt_ports.store, self._receipt_ports.authority)
        ):
            raise GatewayRefusalError("credential_scope_mismatch")

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
            result = PublicReceipt.model_validate(
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
        try:
            task = AuthoritativeTask.model_validate(await self._ports.task.read_task(task_id))
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
                await self._ports.authority.current_authority(principal, task)
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

    @staticmethod
    def _expectations(
        command: AssignmentCommand | TaskDecision,
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
        if snap.form_source_status != "BPMN_FORMDATA":
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
                await self._ports.admission.admit(
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

    async def submit_decision(
        self,
        *,
        session_secret: str,
        csrf_token: str,
        origin: str,
        decision: TaskDecision,
        expected_authority_revision: int,
    ) -> None:
        resolved = await self._session(session_secret, csrf=csrf_token, origin=origin, mutation=True)
        try:
            decision = TaskDecision.model_validate(decision)
            revision = TypeAdapter(Revision).validate_python(expected_authority_revision)
        except Exception:
            raise GatewayRefusalError("operation_forbidden") from None
        task, authority = await self._authorized(resolved, decision.task_id)
        self._expectations(decision, task, resolved, revision)
        self._operation("decision", task, authority, resolved)
        if not set(decision.inputs.model_fields_set - {"kind"}).issubset(task.snapshot.allowed_inputs):
            raise GatewayRefusalError("operation_forbidden")
        # ADR-0049 D3 explicit dependency, not permission to drop mandatory justification.
        # All current AUTH/ESC bindings lack a validated PHI->General projection contract.
        # PAGTO DRAFT is rejected above. Future binding reconciliation must supply a closed
        # projection and PHI custody/reference contract before any decision admission exists.
        raise GatewayRefusalError("form_projection_unavailable")


def create_production_gateway(*, resolver: HumanSessionResolver, scope: Scope) -> HumanGateway:
    """Fail closed until verified authority and dedicated credentials are provisioned.

    No mode flag, supplied arbitrary ports, in-memory persistence or noop credentials can
    make this factory activate. A later reviewed package must compose the D6 adapters
    with verified authority, key isolation and classified form contracts. Configuration refusal is
    not proof of engine enforcement, network isolation or durable acceptance.
    """
    raise GatewayRefusalError("production_capabilities_unavailable")
