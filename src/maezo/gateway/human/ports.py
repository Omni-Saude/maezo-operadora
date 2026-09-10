"""Closed trusted composition ports; no generic REST, engine completion, or memory fallback."""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from maezo.portal.contracts.models import HumanPrincipal

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
