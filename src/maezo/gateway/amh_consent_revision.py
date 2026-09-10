"""Durable XRD-10 observation fence, never a consent source or authorization grant.

The deployment supplies a qualified PostgreSQL store shared by every consumer of
one tenant/legal-entity/AMH origin. Apply the reviewed companion DDL separately.
Missing schema, uncertain commit or unavailable storage refuses the protected read.
No TTL, eviction or process-local fallback may erase an observed denial. The source
still owns current consent and AMH still enforces it for each HTTP request.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from typing import Any
from uuid import uuid4

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
_BEGIN = text("""
INSERT INTO maezo_amh_consent.pending_observation
    (domain_digest, subject_purpose_digest, observation_token)
VALUES (:domain, :subject_purpose, :token)
ON CONFLICT (domain_digest, subject_purpose_digest) DO NOTHING
RETURNING observation_token
""")
_LOCK_PENDING = text("""
SELECT observation_token FROM maezo_amh_consent.pending_observation
WHERE domain_digest = :domain AND subject_purpose_digest = :subject_purpose
FOR UPDATE
""")
_COMPLETE = text("""
DELETE FROM maezo_amh_consent.pending_observation
WHERE domain_digest = :domain AND subject_purpose_digest = :subject_purpose
    AND observation_token = :token
RETURNING observation_token
""")
_PENDING = text("""
SELECT p.observation_token, r.revision::text, r.decision_digest, r.conflicted
FROM maezo_amh_consent.pending_observation p
LEFT JOIN maezo_amh_consent.revision_guard r
    USING (domain_digest, subject_purpose_digest)
WHERE p.domain_digest = :domain AND p.subject_purpose_digest = :subject_purpose
""")
_CURRENT = text("""
SELECT revision::text, decision_digest, conflicted
FROM maezo_amh_consent.revision_guard
WHERE domain_digest = :domain AND subject_purpose_digest = :subject_purpose
    AND NOT EXISTS (SELECT 1 FROM maezo_amh_consent.pending_observation p
        WHERE p.domain_digest = :domain AND p.subject_purpose_digest = :subject_purpose)
""")


@dataclass(frozen=True, slots=True)
class ConsentObservationIntent:
    """Internal opaque ownership, never consent authority or a browser token."""

    domain_digest: str
    subject_purpose_digest: str
    observation_token: str


@dataclass(frozen=True, slots=True)
class PendingConsentReconciliation:
    """Read-only recovery evidence; the possibly observed value is NOT known here.

    An active request and an abandoned intent are deliberately indistinguishable.
    A retained exact decision may retry with this intent; after losing that value,
    qualified source reconciliation is mandatory. Neither age nor a lower fresh
    reply qualifies deletion. No automated source reconciliation is implemented.
    """

    intent: ConsentObservationIntent
    persisted_revision: int | None
    persisted_decision_digest: str | None
    conflicted: bool | None


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

    def _key(self, subject: str, purpose: str) -> dict[str, str]:
        if any(type(value) is not str or not value for value in (subject, purpose)):
            raise ValueError("amh_consent_observation_invalid")
        return {"domain": self._domain, "subject_purpose": hash_input([subject, purpose])}

    async def begin_observation(self, subject: str, purpose: str) -> ConsentObservationIntent | None:
        """Commit intent BEFORE source IO; conflicts/unknown commits never observe.

        There is no timeout expiry or replacement. A committed abandoned intent
        survives reconstruction and blocks only this authority/subject/purpose.
        """
        parameters = self._key(subject, purpose)
        parameters["token"] = uuid4().hex
        async with self._engine.begin() as connection:
            result = await connection.execute(_BEGIN, parameters)
            row = result.one_or_none()
        if row is None:
            return None
        if row[0] != parameters["token"]:
            raise ValueError("amh_consent_observation_unresolved")
        return ConsentObservationIntent(self._domain, parameters["subject_purpose"], row[0])

    async def observe(self, decision: ConsentDecision, *, intent: ConsentObservationIntent) -> bool:
        """Atomically retain floor and complete owned intent, including denials.

        On unknown commit, the floor has committed OR the intent still fences
        readers. Retry is safe only with the retained exact observation and same
        intent. Missing/replaced intents are refused, never implicitly recreated.
        """
        parameters = self._parameters(decision)
        if (
            not isinstance(intent, ConsentObservationIntent)
            or intent.domain_digest != self._domain
            or intent.subject_purpose_digest != parameters["subject_purpose"]
        ):
            raise ValueError("amh_consent_observation_invalid")
        parameters["token"] = intent.observation_token
        async with self._engine.begin() as connection:
            owned = (await connection.execute(_LOCK_PENDING, parameters)).one_or_none()
            if owned is None or owned[0] != intent.observation_token:
                return False
            result = await connection.execute(_OBSERVE, parameters)
            allowed = self._matches(result.one(), parameters)
            completed = (await connection.execute(_COMPLETE, parameters)).one_or_none()
            if completed is None or completed[0] != intent.observation_token:
                raise ValueError("amh_consent_observation_unresolved")
        return allowed

    async def pending_status(self, subject: str, purpose: str) -> PendingConsentReconciliation | None:
        """Concrete read-only reconciliation evidence; no grant or unlock effect."""
        parameters = self._key(subject, purpose)
        async with self._engine.connect() as connection:
            row = (await connection.execute(_PENDING, parameters)).one_or_none()
        if row is None:
            return None
        return PendingConsentReconciliation(
            ConsentObservationIntent(self._domain, parameters["subject_purpose"], row[0]),
            int(row[1]) if row[1] is not None else None,
            row[2],
            row[3],
        )

    async def is_current(self, decision: ConsentDecision) -> bool:
        """Linearize after HTTP cleanup; unresolved observations also refuse."""
        parameters = self._parameters(decision)
        async with self._engine.connect() as connection:
            result = await connection.execute(_CURRENT, parameters)
            row = result.one_or_none()
        return self._matches(row, parameters)
