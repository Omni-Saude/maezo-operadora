"""Focal revision-fence controls; PostgreSQL-marked controls require a real DB."""

import asyncio
import os
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from maezo.gateway import amh_consent_revision as revision
from maezo.ports.consent import ConsentDecision


class RevisionDouble(revision.PostgresAmhConsentRevisionGuard):
    """Executor-only test collaborator; never evidence of PostgreSQL behavior."""

    def __init__(self, *, tenant="amh", legal_entity_ref="legal-entity1", origin="https://amh.example"):
        super().__init__(None, tenant=tenant, legal_entity_ref=legal_entity_ref, origin=origin)
        self.observations = {}
        self.failure = None
        self.pending = {}

    async def begin_observation(self, subject, purpose):
        key = self._key(subject, purpose)["subject_purpose"]
        if key in self.pending:
            return None
        intent = revision.ConsentObservationIntent(self._domain, key, uuid4().hex)
        self.pending[key] = intent
        return intent

    async def observe(self, decision, *, intent=None):
        # Optional auto-admission exists ONLY in this offline double for legacy
        # controls injecting independent observations; production requires intent.
        if intent is None:
            intent = await self.begin_observation(decision.portable_subject_ref, decision.purpose_of_use)
        if intent is None:
            return False
        if self.failure:
            raise self.failure
        p = self._parameters(decision)
        key = p["subject_purpose"]
        if self.pending.get(key) != intent:
            return False
        old = self.observations.get(key)
        if old is None or decision.consent_revision > old[0]:
            self.observations[key] = (decision.consent_revision, p["decision"], False)
        elif decision.consent_revision == old[0] and p["decision"] != old[1]:
            self.observations[key] = (old[0], old[1], True)
        del self.pending[key]
        return await self.is_current(decision)

    async def is_current(self, decision):
        if self.failure:
            raise self.failure
        p = self._parameters(decision)
        return p["subject_purpose"] not in self.pending and self.observations.get(p["subject_purpose"]) == (
            decision.consent_revision,
            p["decision"],
            False,
        )


def decision(revision_number=9, **changes):
    return replace(
        ConsentDecision(
            portable_subject_ref="opaque-subject",
            purpose_of_use="opaque-purpose",
            consent_decision_ref="opaque-decision",
            consent_revision=revision_number,
            granted=False,
            decided_at=datetime(2026, 9, 10, tzinfo=UTC),
        ),
        **changes,
    )


@pytest.mark.asyncio
async def test_real_adapter_waits_for_commit_and_refuses_unknown_ack():
    d = decision()
    guard = revision.PostgresAmhConsentRevisionGuard(
        None, tenant="amh", legal_entity_ref="legal-entity1", origin="https://amh.example"
    )
    p = guard._parameters(d)
    events = []

    class Connection:
        async def execute(self, statement, parameters):
            assert parameters == {**p, "token": "a" * 32}
            assert all("opaque-" not in value for value in parameters.values())
            if str(statement) == str(revision._LOCK_PENDING):
                events.append("lock")
                return SimpleNamespace(one_or_none=lambda: ("a" * 32,))
            if str(statement) == str(revision._COMPLETE):
                events.append("complete")
                return SimpleNamespace(one_or_none=lambda: ("a" * 32,))
            assert str(statement) == str(revision._OBSERVE)
            events.append("execute")
            return SimpleNamespace(one=lambda: ("9", p["decision"], False))

    @asynccontextmanager
    async def begin():
        yield Connection()
        events.append("commit_ack_lost")
        raise OSError("synthetic unknown commit")

    guard._engine = SimpleNamespace(begin=begin)
    with pytest.raises(OSError):
        await guard.observe(
            d, intent=revision.ConsentObservationIntent(guard._domain, p["subject_purpose"], "a" * 32)
        )
    assert events == ["lock", "execute", "complete", "commit_ack_lost"]


@pytest.mark.parametrize(
    "changes",
    [
        {"consent_revision": True},
        {"consent_revision": -1},
        {"granted": 1},
        {"decided_at": datetime(2026, 9, 10)},
        {"portable_subject_ref": ""},
    ],
)
def test_invalid_observations_cannot_change_state(changes):
    guard = revision.PostgresAmhConsentRevisionGuard(
        None, tenant="amh", legal_entity_ref="legal-entity1", origin="https://amh.example"
    )
    with pytest.raises(ValueError):
        guard._parameters(decision(**changes))


@pytest.fixture
async def postgres_guard(monkeypatch):
    """Only an owned unique schema; production SQL changes only its namespace."""
    url = os.environ.get("MAEZO_TEST_DATABASE_URL")
    if not url:
        pytest.skip("COULD NOT VERIFY: MAEZO_TEST_DATABASE_URL required for actual PostgreSQL fence")
    url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(url, poolclass=NullPool, echo=False, hide_parameters=True)
    schema = "test_amh_revision_" + uuid4().hex
    created = False
    try:
        # Connection errors are not converted to skipped/passing assertions.
        async with asyncio.timeout(5):
            async with engine.begin() as conn:
                await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
                created = True
                ddl = Path(revision.__file__).with_suffix(".sql").read_text()
                ddl = "\n".join(line for line in ddl.splitlines() if not line.lstrip().startswith("--"))
                for command in ddl.replace("maezo_amh_consent", schema).split(";"):
                    if command.strip():
                        await conn.execute(text(command))
        for name in ("_OBSERVE", "_CURRENT", "_BEGIN", "_LOCK_PENDING", "_COMPLETE", "_PENDING"):
            monkeypatch.setattr(
                revision, name, text(str(getattr(revision, name)).replace("maezo_amh_consent", schema))
            )
        yield engine
    finally:
        if created:
            async with engine.begin() as conn:
                await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await engine.dispose()


def guard(engine, tenant="amh"):
    return revision.PostgresAmhConsentRevisionGuard(
        engine, tenant=tenant, legal_entity_ref="legal-entity1", origin="https://amh.example"
    )


async def observe(guard_instance, value):
    intent = await guard_instance.begin_observation(value.portable_subject_ref, value.purpose_of_use)
    if intent is None:
        return False
    return await guard_instance.observe(value, intent=intent)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_postgres_restart_domains_and_concurrent_lower_revisions(postgres_guard):
    engine = postgres_guard
    denied = decision(2**60 + 3)
    assert await observe(guard(engine), denied)
    # New object/pool connections retain the committed floor; never a memory cache.
    assert not await observe(guard(engine), decision(2**60 + 2, granted=True))
    assert await observe(guard(engine, tenant="other"), decision(8, granted=True))
    assert await observe(guard(engine), decision(8, granted=True, portable_subject_ref="another"))
    assert await observe(guard(engine), decision(8, granted=True, purpose_of_use="another"))
    await asyncio.gather(
        *(
            observe(guard(engine), decision(n, granted=True, portable_subject_ref="concurrent"))
            for n in (10, 12, 11, 8)
        )
    )
    # Unique intents may refuse concurrent readers before observing their source.
    # Explicit final independent observation establishes the asserted revision.
    assert await observe(guard(engine), decision(12, granted=True, portable_subject_ref="concurrent"))
    assert await guard(engine).is_current(decision(12, granted=True, portable_subject_ref="concurrent"))
    assert not await observe(guard(engine), decision(11, granted=True, portable_subject_ref="concurrent"))


@pytest.mark.integration
@pytest.mark.asyncio
async def test_postgres_equal_revision_equivocation_stays_refused(postgres_guard):
    g = guard(postgres_guard)
    assert await observe(g, decision(9, granted=True))
    assert not await observe(g, decision(9, granted=False))
    assert not await observe(guard(postgres_guard), decision(9, granted=True))
    assert await observe(guard(postgres_guard), decision(10, granted=True))
