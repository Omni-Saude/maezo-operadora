"""Closed trusted composition ports; no generic REST or memory fallback.

Engine completion used to be absent from this file by design, and the durable signed relay
(ADR-0049 D5/D6/D7) remains the destination. `HumanTaskTransport.complete_task` is the INTERIM
seam registered in `docs/decisions-log.md` DL-0049: it is not abstract, its default refuses,
and only an adapter that was handed an explicit completion capability answers it. Removing it
is the goal, not a regression — see `completion.py`.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from maezo.portal.contracts.models import HumanPrincipal

from .completion import CompletionAck, CompletionOutcome, PseudonymizedNotes
from .errors import GatewayRefusalError
from .models import (
    AuthoritativeTask,
    AuthorizedAssignment,
    AuthorizedGovernedAssignment,
    CurrentTaskAuthority,
    GovernedAssignmentCandidates,
    GovernedAssignmentReadContext,
    PendingAdmission,
    Scope,
)


class HumanTaskTransport(ABC):
    scope: Scope

    @abstractmethod
    async def read_task(self, task_id: str) -> AuthoritativeTask:
        """Read current tenant task and pinned catalog; reject missing/untrusted state."""
        raise NotImplementedError

    async def complete_task(
        self,
        task_id: str,
        *,
        resultado: CompletionOutcome,
        notes: PseudonymizedNotes,
        expected_task_revision: int,
    ) -> CompletionAck:
        """INTERIM (DL-0049): close an ESCALATION task the caller already read and was
        authorized for, in THIS request.

        NOT abstract, and refusing is the default: every adapter written before DL-0049 keeps
        working and keeps refusing. An adapter may only answer when a composition explicitly
        installed a `DirectTaskCompletion`; it must verify that `task_id` is the task it read
        in this request and that `expected_task_revision` is that snapshot's revision, so a
        completion can never ride a revision nobody authorized.

        `notes` arrives already pseudonymized (`completion.pseudonymize_notes`). An adapter
        never scrubs a second time, never accepts raw text and never sends a third variable.
        """
        raise GatewayRefusalError("production_capabilities_unavailable")


class AuthorityProjection(ABC):
    scope: Scope

    @abstractmethod
    async def current_authority(
        self,
        principal: HumanPrincipal,
        task: AuthoritativeTask,
    ) -> CurrentTaskAuthority:
        """Resolve current task/subject/consent authority for this authenticated principal.

        Never use a browser-supplied projection, group expression, or unverified external
        fact. Absence, expired source, or lag in engine authorization projection is error.
        """
        raise NotImplementedError


class DurableAdmission(ABC):
    scope: Scope

    @abstractmethod
    async def admit(self, command: AuthorizedAssignment) -> PendingAdmission:
        """Atomically commit tenant audit intent + dedicated human outbox, or raise.

        Must preserve immutable command id/revisions, idempotency/conflict and chain
        integrity. Must not dispatch engine REST here. No acknowledgement before commit;
        uncertain commit raises safely (same identity remains for reconciliation).
        """
        raise NotImplementedError


@dataclass(frozen=True)
class BoundHumanPorts:
    task: HumanTaskTransport
    authority: AuthorityProjection
    admission: DurableAdmission


class GovernedAssignmentContextTransport(ABC):
    scope: Scope

    @abstractmethod
    async def read_context(self, principal: HumanPrincipal, task_id: str) -> "GovernedAssignmentReadContext":
        raise NotImplementedError


class GovernedAssignmentAuthorityProjection(ABC):
    scope: Scope

    @abstractmethod
    async def current_authority(
        self,
        principal: HumanPrincipal,
        context: "GovernedAssignmentReadContext",
        requested_operation: str,
        target_ref: str | None,
        target_membership_revision: int | None,
    ) -> CurrentTaskAuthority:
        raise NotImplementedError


class GovernedAssignmentCandidateProjection(ABC):
    scope: Scope

    @abstractmethod
    async def list_candidates(
        self, principal: HumanPrincipal, context: "GovernedAssignmentReadContext"
    ) -> "GovernedAssignmentCandidates":
        raise NotImplementedError


class GovernedAssignmentAdmission(ABC):
    scope: Scope

    @abstractmethod
    async def admit(self, command: AuthorizedGovernedAssignment) -> PendingAdmission:
        raise NotImplementedError


@dataclass(frozen=True)
class BoundGovernedAssignmentPorts:
    context: GovernedAssignmentContextTransport
    authority: GovernedAssignmentAuthorityProjection
    candidates: GovernedAssignmentCandidateProjection
    admission: GovernedAssignmentAdmission
