"""Real cryptography, explicitly synthetic key/admission providers, no source authority."""

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from tests.unit.portal.test_read_engine_profile import fixture

from maezo.gateway.human.models import Scope
from maezo.gateway.human.queue import QueueBinding, ReadRefusalError
from maezo.gateway.human.queue_cursor import AeadQueueCursorCustody
from maezo.gateway.human.read_credentials import (
    CursorKeyLease,
    CursorKeySet,
    QueueCursorKeyProvider,
    ReadAdmission,
    ReadAdmissionLease,
    ReadCredentialPartition,
    ReadCredentialProvider,
    ReadDeploymentAdmission,
    ReadSigningLease,
)
from maezo.gateway.human.read_profile import Requester, parse_model
from maezo.portal.contracts.models import HumanPrincipal


class Clock:
    def __init__(self):
        self.now = datetime(2026, 9, 9, 21, tzinfo=UTC)

    def __call__(self):
        return self.now


class SyntheticProviders(ReadCredentialProvider, ReadDeploymentAdmission, QueueCursorKeyProvider):
    def __init__(self, clock):
        self.clock = clock
        self.calls = 0
        self.revoked = False
        self.scope = Scope(tenant="test-tenant", environment="test", workload_ref="read-gateway")
        self.before = clock() - timedelta(seconds=1)
        self.until = clock() + timedelta(minutes=2)
        self.key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
        self.public = hashlib.sha256(
            self.key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        ).hexdigest()
        self.cursor = CursorKeyLease(
            self.scope,
            "cursor",
            bytes(range(16)),
            self.before,
            self.until,
            1,
            bytes(range(32)),
            self.live,
        )

    def live(self):
        if self.revoked:
            raise ReadRefusalError("read_dependency_unavailable")

    async def acquire(self, scope, *args):
        self.calls += 1
        self.live()
        if len(args) == 1:
            return CursorKeySet(self.cursor, (self.cursor,), self.live)
        return ReadSigningLease(
            scope,
            args[0],
            Requester(
                issuer=scope.workload_ref,
                key_id=args[1],
                public_key_sha256=self.public,
                peer_spki_sha256="c" * 64,
            ),
            "read",
            self.before,
            self.until,
            60,
            1,
            self.key,
            self.live,
        )

    async def current(self, scope, engine):
        self.calls += 1
        self.live()
        return ReadAdmissionLease(
            ReadAdmission(
                scope=scope,
                engine_name=engine,
                database_incarnation="incarnation-1",
                read_deployment_ref="read-release-1",
                read_deployment_digest="a" * 64,
                runtime_admission_generation=8,
                capability_digest="d" * 64,
                observed_at=self.before,
                valid_until=self.until,
                provider_ref="admission",
                provider_revision=1,
            ),
            self.live,
        )


def setup():
    clock = Clock()
    providers = SyntheticProviders(clock)
    partition = ReadCredentialPartition(
        scope=providers.scope,
        engine_name="engine-1",
        key_id="read-key-1",
        credentials=providers,
        admission=providers,
        clock=clock,
    )
    custody = AeadQueueCursorCustody(partition=partition, provider=providers, key_id="cursor")
    p = parse_model(HumanPrincipal, fixture()["principal"])
    binding = QueueBinding(
        scope=providers.scope,
        principal=p,
        queue="team",
        limit=1,
        catalog_revision=1,
        catalog_ref="catalog",
        publisher_ref="publisher",
        catalog_digest="e" * 64,
    )
    return clock, providers, partition, custody, binding


@pytest.mark.asyncio
async def test_finalize_reseals_and_future_resolve_enforces_shortest_ceiling():
    clock, providers, partition, custody, binding = setup()
    await custody.prepare()
    long = custody.provisional(
        binding=binding, after_task_id="task-1", valid_until=clock() + timedelta(seconds=30)
    )
    calls = providers.calls
    short = clock() + timedelta(seconds=2)
    final = custody.finalize(long.cursor, binding=binding, after_task_id="task-1", valid_until=short)
    assert providers.calls == calls and final.cursor != long.cursor and final.valid_until == short
    assert (await custody.resolve(final.cursor, binding=binding)).valid_until == short
    clock.now = short
    with pytest.raises(ReadRefusalError, match="refresh_required"):
        await custody.resolve(final.cursor, binding=binding)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["limit", "session", "catalog", "queue", "tamper", "padding", "expired", "revoked"]
)
async def test_real_aead_negative_boundaries(change):
    clock, p, partition, c, b = setup()
    await c.prepare()
    g = c.provisional(binding=b, after_task_id="task-1", valid_until=clock() + timedelta(seconds=20))
    token = g.cursor
    if change == "limit":
        b = b.model_copy(update={"limit": 2})
    if change == "session":
        b = b.model_copy(update={"principal": b.principal.model_copy(update={"session_ref": "other"})})
    if change == "catalog":
        b = b.model_copy(update={"catalog_digest": "f" * 64})
    if change == "queue":
        b = b.model_copy(update={"queue": "mine"})
    if change == "tamper":
        token = token[:45] + ("A" if token[45] != "A" else "B") + token[46:]
    if change == "padding":
        token += "="
    if change == "expired":
        clock.now = g.valid_until
    if change == "revoked":
        p.revoked = True
    with pytest.raises(ReadRefusalError):
        await c.resolve(token, binding=b)


@pytest.mark.asyncio
async def test_no_extension_and_clock_regression():
    clock, p, partition, c, b = setup()
    await c.prepare()
    g = c.provisional(binding=b, after_task_id="task-1", valid_until=clock() + timedelta(seconds=2))
    with pytest.raises(ReadRefusalError):
        c.finalize(
            g.cursor, binding=b, after_task_id="task-1", valid_until=g.valid_until + timedelta(microseconds=1)
        )
    clock.now -= timedelta(microseconds=1)
    with pytest.raises(ReadRefusalError):
        await c.resolve(g.cursor, binding=b)


@pytest.mark.parametrize("size", [0, 16, 24, 31, 33])
def test_cursor_lease_never_accepts_non_256_bit_material(size):
    clock, providers, _, _, _ = setup()
    with pytest.raises(ReadRefusalError):
        CursorKeyLease(
            providers.scope,
            "cursor",
            bytes(16),
            providers.before,
            providers.until,
            1,
            bytes(size),
            providers.live,
        )
