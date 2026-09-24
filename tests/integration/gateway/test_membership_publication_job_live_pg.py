"""T1.5 contra PostgreSQL REAL: handshake sobre `portal_memberships` (migracao 0012) e o job idempotente.

* Linhas reais inseridas na tabela criada pelas migracoes REAIS, lidas pelo `PostgresIdentityStore`
  real (SQL de `postgres.py:142`), sem mock de I/O.
* O publicador e um CAS em memoria (revisao = esperada + 1). A ida ao engine pela rota mTLS e o
  checkpoint C1 (harness Java, layout D-C2), fora deste teste.
Dados sinteticos.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg  # type: ignore[import-untyped]
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from tests.integration.conftest import _apply_migrations, _pg_reachable

from maezo.gateway.audit_postgres import normalize_dsn
from maezo.gateway.human.membership_publication_job import (
    MembershipPublicationJob,
    PostgresMembershipHandshake,
    PublicationLedger,
    StaffCatalogConfig,
    StaffCatalogPublicationSource,
    staff_catalog_artifact,
)
from maezo.gateway.human.queue import ReadRefusalError
from maezo.gateway.human.read_profile import digest
from maezo.gateway.human.read_publisher import PostgresMembershipPublicationSource, PublicationReceipt
from maezo.portal.api.postgres import PostgresIdentityStore
from maezo.portal.api.records import MembershipRecord
from maezo.portal.contracts.models import MembershipBinding

pytestmark = pytest.mark.integration

ISSUER = "https://issuer.example/maezo"
PUBLISHER = "portal-staff"


def _pg_dsn() -> str:
    explicit = os.environ.get("MAEZO_TEST_DATABASE_URL")
    if explicit:
        return explicit
    return f"postgresql://maezo:maezo@localhost:{os.environ.get('MAEZO_PG_HOST_PORT', '5433')}/maezo"


@pytest.fixture
def pg_schema() -> Iterator[tuple[str, str]]:
    dsn = _pg_dsn()
    if not _pg_reachable(dsn):
        pytest.skip(f"COULD NOT VERIFY: Postgres inalcancavel em {dsn!r} (MAEZO_TEST_DATABASE_URL)")
    schema = "t15_" + uuid.uuid4().hex[:12]

    async def _run(sql: str) -> None:
        connection = await asyncpg.connect(normalize_dsn(dsn))
        try:
            await connection.execute(sql)
        finally:
            await connection.close()

    asyncio.run(_run(f'CREATE SCHEMA "{schema}"'))
    try:
        _apply_migrations(dsn, schema)
        yield dsn, schema
    finally:
        asyncio.run(_run(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


@pytest.fixture
async def pg(pg_schema: tuple[str, str]) -> AsyncIterator[tuple[AsyncEngine, str]]:
    dsn, schema = pg_schema
    url = dsn if "+asyncpg" in dsn else dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    try:
        yield engine, schema
    finally:
        await engine.dispose()


def record(name: str, *, tenant: str = "amh", **changes: object) -> MembershipRecord:
    value: dict[str, object] = dict(
        tenant=tenant,
        issuer=ISSUER,
        subject=f"subject-{name}",
        principal_ref=f"principal-{name}",
        revision=1,
        audience="staff",
        memberships=(
            MembershipBinding(membership_ref=f"m-{name}", roles=("staff",), groups=("atendimento-humano",)),
        ),
        subject_bindings=(),
        reviewed_until=datetime.now(UTC) + timedelta(days=1),
        revoked=False,
    )
    value.update(changes)
    return MembershipRecord(**value)  # type: ignore[arg-type]


async def upsert(engine: AsyncEngine, r: MembershipRecord) -> None:
    async with engine.begin() as c:
        await c.execute(
            text("DELETE FROM portal_memberships WHERE tenant=:t AND issuer=:i AND subject=:s"),
            dict(t=r.tenant, i=r.issuer, s=r.subject),
        )
        await c.execute(
            text(
                "INSERT INTO portal_memberships (tenant, issuer, subject, principal_ref, payload) "
                "VALUES (:tenant, :issuer, :subject, :principal_ref, :payload)"
            ),
            dict(
                tenant=r.tenant,
                issuer=r.issuer,
                subject=r.subject,
                principal_ref=r.principal_ref,
                payload=r.model_dump_json(),
            ),
        )


class CasPublisher:
    """Engine CAS stand-in that drives the REAL source (and its handshake) for memberships."""

    def __init__(
        self, membership: PostgresMembershipPublicationSource, catalog: StaffCatalogPublicationSource
    ):
        self.membership, self.catalog, self.revision = membership, catalog, 0
        self.published: list[tuple[str, str]] = []

    def _receipt(self, kind: str, expected: int, payload_digest: str) -> PublicationReceipt:
        if expected != self.revision:
            raise ReadRefusalError("read_dependency_unavailable")
        self.revision += 1
        return PublicationReceipt.model_validate(
            {
                "schema": "portal-read-publication-receipt.v1",
                "publication_id": f"publication-{self.revision}",
                "request_digest": "a" * 64,
                "authority_revision": self.revision,
                "kind": kind,
                "record_digest": payload_digest,
            }
        )

    async def publish_catalog(self, catalog_ref: str, *, expected_revision: int) -> PublicationReceipt:
        snapshot = await self.catalog.read(catalog_ref)
        self.published.append(("catalog-designate", catalog_ref))
        return self._receipt("catalog-designate", expected_revision, digest(snapshot.payload))

    async def publish_membership(
        self, issuer: str, subject: str, *, expected_revision: int
    ) -> PublicationReceipt:
        snapshot = await self.membership.read(issuer, subject)  # freeze + live re-read + byte compare
        snapshot.lease.live()
        assert snapshot.source.source_digest == digest(snapshot.payload)
        self.published.append(("membership", subject))
        return self._receipt("membership", expected_revision, digest(snapshot.payload))


def compose(engine: AsyncEngine, ledger: Path, tenant: str = "amh"):
    store = PostgresIdentityStore(tenant, engine)
    handshake = PostgresMembershipHandshake(
        store=store,
        publisher_ref=PUBLISHER,
        source_ref_prefix=f"portal-identity:{tenant}:membership:",
        observation_seconds=300,
    )
    raw = staff_catalog_artifact(
        catalog_ref="catalog-staff",
        publisher_ref=PUBLISHER,
        deployment_receipt_ref="deployment-1",
        deployment_receipt_digest="d" * 64,
    )
    import hashlib

    catalog = StaffCatalogPublicationSource(
        config=StaffCatalogConfig.model_validate(
            dict(
                catalog_ref="catalog-staff",
                catalog_revision=1,
                admitted_catalog_digest=hashlib.sha256(raw).hexdigest(),
                deployment_receipt_ref="deployment-1",
                deployment_receipt_digest="d" * 64,
                source_ref_prefix=f"portal-read-catalog:{tenant}:",
                valid_seconds=86400,
            )
        ),
        publisher_ref=PUBLISHER,
    )
    source = PostgresMembershipPublicationSource(store=store, handshake=handshake)
    return store, handshake, catalog, source


async def test_live_row_freezes_with_the_contract_provenance(
    pg: tuple[AsyncEngine, str], tmp_path: Path
) -> None:
    engine, _ = pg
    r = record("a", revision=4)
    await upsert(engine, r)
    _, handshake, _, source = compose(engine, tmp_path / "l.json")
    lease = await handshake.freeze(ISSUER, "subject-a")
    assert lease.provenance.receipt_ref == "portal-identity:amh:membership:principal-a@4"
    assert lease.provenance.source_revision == 4
    snapshot = await source.read(ISSUER, "subject-a")
    assert snapshot.source.source_digest == digest(snapshot.payload)


async def test_job_publishes_catalog_once_and_each_revision_once(
    pg: tuple[AsyncEngine, str], tmp_path: Path
) -> None:
    engine, _ = pg
    for r in (record("a"), record("b"), record("x", tenant="outro")):
        await upsert(engine, r)
    ledger_path = tmp_path / "ledger.json"
    store, handshake, catalog, source = compose(engine, ledger_path)
    publisher = CasPublisher(source, catalog)

    def job() -> MembershipPublicationJob:
        return MembershipPublicationJob(
            publisher=publisher,
            ledger=PublicationLedger(ledger_path, "amh"),
            catalog=catalog,
            handshake=handshake,
            store=store,
            engine=engine,
        )

    first = await job().run()
    assert (first.catalog_published, first.memberships_published, first.authority_revision) == (True, 2, 3)
    assert ("membership", "subject-x") not in publisher.published  # other tenant never read
    second = await job().run()
    assert (second.catalog_published, second.memberships_published, second.memberships_unchanged) == (
        False,
        0,
        2,
    )
    assert publisher.revision == 3  # CAS untouched on the second run
    await upsert(engine, record("b", revision=2, revoked=True))
    third = await job().run()
    assert (third.memberships_published, third.authority_revision) == (1, 4)
    assert publisher.published[-1] == ("membership", "subject-b")


async def test_row_changed_after_freeze_is_refused_live(pg: tuple[AsyncEngine, str], tmp_path: Path) -> None:
    engine, _ = pg
    await upsert(engine, record("a"))
    store, handshake, _, _ = compose(engine, tmp_path / "l.json")

    class ChangingHandshake(PostgresMembershipHandshake):
        async def freeze(self, issuer: str, subject: str):  # type: ignore[override]
            lease = await handshake.freeze(issuer, subject)
            await upsert(engine, record("a", revision=2))  # committed change between freeze and read
            return lease

    changing = object.__new__(ChangingHandshake)
    with pytest.raises(ReadRefusalError):
        await PostgresMembershipPublicationSource(store=store, handshake=changing).read(ISSUER, "subject-a")


async def test_absent_row_is_refused_live(pg: tuple[AsyncEngine, str], tmp_path: Path) -> None:
    engine, _ = pg
    _, handshake, _, _ = compose(engine, tmp_path / "l.json")
    with pytest.raises(ReadRefusalError):
        await handshake.freeze(ISSUER, "subject-ninguem")
