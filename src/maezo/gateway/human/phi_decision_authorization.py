"""PHI-zone custody authorization (inventory #24) — a recheck, never a DTO grant.

`CurrentPhiDecisionAuthorization.authorize` must "authenticate current sources,
refuse unknown/stale/revoked access; no DTO grant", and it is called separately for
`preserve` and for `resolve`. This provider therefore performs a fresh authoritative
engine read on EVERY call: membership revoked, task rebound or consent withdrawn
between the two calls makes the second one fail, which is the property the custody
plane depends on.

Deliberate limit: a `resolve` is authorized only while the engine still returns a
current read context for the task. No consumer contract has been ratified that
permits resolving after task completion, so this provider refuses that case rather
than inventing the permission (ADR-0049 D3; the refusal is fail-closed).
"""

from __future__ import annotations

from datetime import UTC, datetime

from .assignment_transport import NativeAssignmentClient
from .decision_custody import CurrentPhiDecisionAuthorization, CustodyAccess, CustodyAuthorization
from .decision_custody_connection import DecisionCustodyError, PhiPostgresConnection


class EngineBackedPhiDecisionAuthorization(CurrentPhiDecisionAuthorization):
    """#24 — re-authorize each PHI custody operation against the live engine."""

    def __init__(self, *, client: NativeAssignmentClient, connection: PhiPostgresConnection) -> None:
        if not isinstance(client, NativeAssignmentClient):
            raise DecisionCustodyError()
        scope = connection.deployment.scope
        if (client.scope.tenant, client.scope.environment) != (scope.tenant, scope.environment):
            raise DecisionCustodyError()
        self.scope = scope
        self._client = client
        self._connection = connection

    async def authorize(self, access: CustodyAccess) -> CustodyAuthorization:
        value = CustodyAccess.model_validate(access)
        record = value.record
        if (
            record.scope != self.scope
            or value.principal.tenant != self.scope.tenant
            or record.principal_ref != value.principal.principal_ref
        ):
            raise DecisionCustodyError()
        # Deployment assurance first: an expired PHI deployment authorizes nothing.
        deployment_until = self._connection.current()
        try:
            context = await self._client.read_context(value.principal, record.task_id)
        except Exception:
            raise DecisionCustodyError() from None
        if (
            context.principal.principal_ref != value.principal.principal_ref
            or context.principal.issuer != value.principal.issuer
            or context.principal.subject != value.principal.subject
            or context.context.task_id != record.task_id
        ):
            raise DecisionCustodyError()
        valid_until = min(
            deployment_until, record.valid_until, context.task.valid_until, context.context.valid_until
        )
        if valid_until <= datetime.now(UTC):
            raise DecisionCustodyError()
        return CustodyAuthorization(access=value, valid_until=valid_until)
