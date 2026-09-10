"""Durable XRD-10 observation fence, never a consent source or authorization grant.

The deployment supplies a qualified PostgreSQL store shared by every consumer of
one tenant/legal-entity/AMH origin. Apply the reviewed companion DDL separately.
Missing schema, uncertain commit or unavailable storage refuses the protected read.
No TTL, eviction or process-local fallback may erase an observed denial. The source
still owns current consent and AMH still enforces it for each HTTP request.
"""

from __future__ import annotations

from datetime import UTC
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from maezo.gateway.audit import hash_input
from maezo.ports.consent import ConsentDecision

# One atomic upsert serializes competing observations across processes. Equal
# revision equivocation poisons that revision; only a strictly newer observation
# can supersede it. Lower revisions never mutate the stored decision.
_OBSERVE = text("""
INSERT INTO maezo_amh_consent.revision_guard AS stored
    (domain_digest, subject_purpose_digest, revision, decision_digest, conflicted)
VALUES (:domain, :subject_purpose, CAST(CAST(:revision AS TEXT) AS NUMERIC), :decision, false)
ON CONFLICT (domain_digest, subject_purpose_digest) DO UPDATE SET
    revision = GREATEST(stored.revision, EXCLUDED.revision),
    decision_digest = CASE WHEN EXCLUDED.revision > stored.revision
        THEN EXCLUDED.decision_digest ELSE stored.decision_digest END,
    conflicted = CASE
        WHEN EXCLUDED.revision > stored.revision THEN false
        WHEN EXCLUDED.revision = stored.revision THEN
            stored.conflicted OR stored.decision_digest <> EXCLUDED.decision_digest
        ELSE stored.conflicted END
RETURNING revision::text, decision_digest, conflicted
""")
_CURRENT = text("""
SELECT revision::text, decision_digest, conflicted
FROM maezo_amh_consent.revision_guard
WHERE domain_digest = :domain AND subject_purpose_digest = :subject_purpose
""")


class PostgresAmhConsentRevisionGuard:
    """A concrete mandatory store; constructor bindings are not owner qualification.

    Store placement, shared realm, source provenance and protection against row
    deletion/restore rollback require deployment qualification. Digests are still
    sensitive linkable metadata; no raw subject, purpose or decision ref is stored
    or logged here. Engine credentials and SQL bind logging remain PHI-zone inputs.
    """

    def __init__(self, engine: AsyncEngine, *, tenant: str, legal_entity_ref: str, origin: str) -> None:
        self._engine = engine
        self._binding = (tenant, legal_entity_ref, origin)
        self._domain = hash_input(list(self._binding))

    @property
    def binding(self) -> tuple[str, str, str]:
        return self._binding

    def _parameters(self, decision: ConsentDecision) -> dict[str, str]:
        if (
            not isinstance(decision, ConsentDecision)
            or type(decision.consent_revision) is not int
            or decision.consent_revision < 0
            or type(decision.granted) is not bool
            or any(
                type(value) is not str or not value
                for value in (
                    decision.portable_subject_ref,
                    decision.purpose_of_use,
                    decision.consent_decision_ref,
                )
            )
            or decision.decided_at.tzinfo is None
            or decision.decided_at.utcoffset() is None
        ):
            raise ValueError("amh_consent_observation_invalid")
        return {
            "domain": self._domain,
            "subject_purpose": hash_input([decision.portable_subject_ref, decision.purpose_of_use]),
            "revision": str(decision.consent_revision),
            "decision": hash_input(
                [
                    decision.consent_decision_ref,
                    decision.granted,
                    decision.decided_at.astimezone(UTC).isoformat(),
                ]
            ),
        }

    @staticmethod
    def _matches(row: Any, parameters: dict[str, str]) -> bool:
        return bool(
            row is not None
            and row[0] == parameters["revision"]
            and row[1] == parameters["decision"]
            and row[2] is False
        )

    async def observe(self, decision: ConsentDecision) -> bool:
        """Commit denials too, before testing grant/ref; unknown commit never permits."""
        parameters = self._parameters(decision)
        async with self._engine.begin() as connection:
            result = await connection.execute(_OBSERVE, parameters)
            allowed = self._matches(result.one(), parameters)
        # Do not return an affirmative answer until context-manager COMMIT succeeds.
        return allowed

    async def is_current(self, decision: ConsentDecision) -> bool:
        """Check observations completed during awaited audit/HTTP work; no grant cache."""
        parameters = self._parameters(decision)
        async with self._engine.connect() as connection:
            result = await connection.execute(_CURRENT, parameters)
            return self._matches(result.one_or_none(), parameters)
