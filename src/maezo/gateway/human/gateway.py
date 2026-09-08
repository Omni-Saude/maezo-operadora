"""Always-enforcing D4 human authorization. No shadow/flip/test allowlist or agent gateway.

This service belongs inside the PHI boundary. Current decision DTOs can contain mandatory
raw human justification; no classified projection contract exists for these bindings.
They are refused intact before admission; no fields are dropped, scrubbed or remapped.
"""

import secrets
from datetime import UTC, datetime

from pydantic import TypeAdapter

from maezo.portal.api.session import HumanSessionResolver, ResolvedHumanSession
from maezo.portal.contracts.models import OpaqueRef, Revision, TaskDecision, TaskSnapshot

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

    Concrete production adapters are absent; create_production_gateway refuses startup.
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
    ) -> None:
        self._scope = Scope.model_validate(scope)
        self._resolver = resolver
        self._ports = ports
        self._credentials = credentials
        self._check_scope()

    def _check_scope(self) -> None:
        self._credentials.for_workload(self._scope)
        if self._resolver.settings.tenant != self._scope.tenant or any(
            port.scope != self._scope
            for port in (self._ports.task, self._ports.authority, self._ports.admission)
        ):
            raise GatewayRefusalError("credential_scope_mismatch")

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
        self, resolved: ResolvedHumanSession, task_id: str
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
    """Fail closed: D5 signed transport/receipt and D6 durable adapters are not implemented.

    No mode flag, supplied arbitrary ports, in-memory persistence or noop credentials can
    make this factory activate. A later reviewed package must implement and wire verified
    adapters plus key isolation and classified form contracts. Configuration refusal is
    not proof of engine enforcement, network isolation or durable acceptance.
    """
    raise GatewayRefusalError("production_capabilities_unavailable")
