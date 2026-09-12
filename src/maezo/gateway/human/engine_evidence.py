"""Engine-backed evidence reference for the legacy assignment plane (inventory #7).

`EvidenceReferenceSource.current_reference` must "resolve the existing authoritative
engine evidence binding, never from browser input". The governed twin is already
implemented by `NativeAssignmentClient`; this adapter gives the legacy
`AuthorizedAssignment` shape the same engine read, and then cross-checks the answer
against the task snapshot the caller presented. A constant, a cached value or a
browser-supplied DTO cannot satisfy it: the reference is produced by the native
query and is refused unless it matches the engine's own revision and digest.
"""

from __future__ import annotations

from .assignment_transport import NativeAssignmentClient
from .models import AuthorizedAssignment
from .projection import EvidenceReference, EvidenceReferenceSource, ProjectionError


class EngineEvidenceReferenceSource(EvidenceReferenceSource):
    """#7 — one native read per admission; no cache, no default, no fallback."""

    def __init__(self, client: NativeAssignmentClient) -> None:
        if not isinstance(client, NativeAssignmentClient):
            raise ProjectionError("assignment projection unavailable")
        self._client = client
        self.scope = client.scope

    async def current_reference(self, assignment: AuthorizedAssignment) -> EvidenceReference:
        value = AuthorizedAssignment.model_validate(assignment)
        if value.scope != self.scope:
            raise ProjectionError("assignment projection unavailable")
        try:
            # "context" is the read that returns the engine's current evidence binding
            # for this task without requesting an operation authority.
            _, evidence, _, _, _ = await self._client._query(
                value.principal, value.command.task_id, "context"
            )
        except ProjectionError:
            raise
        except Exception:
            raise ProjectionError("assignment projection unavailable") from None
        reference = EvidenceReference.model_validate(evidence)
        snapshot = value.snapshot
        if (
            reference.tenant != self.scope.tenant
            or reference.task_id != value.command.task_id
            or reference.revision != snapshot.evidence_revision
            or reference.digest != snapshot.evidence_digest
        ):
            # The caller's snapshot and the engine disagree: refuse rather than
            # admit a command against evidence the engine does not currently hold.
            raise ProjectionError("assignment projection unavailable")
        return reference
