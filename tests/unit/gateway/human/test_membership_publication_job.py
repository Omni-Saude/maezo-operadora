"""T1.5 unit: provenance contract with the Java provider, minimal catalog, fail-closed sources, CAS ledger.

DB I/O is isolated here (the store's get_membership is an AsyncMock); the live PostgreSQL run is
tests/integration/gateway/test_membership_publication_job_live_pg.py. Synthetic data only.
"""

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from maezo.gateway.human.membership_publication_job import (
    MembershipPublicationJob,
    PostgresMembershipHandshake,
    PublicationLedger,
    RefusingPagtoSource,
    RefusingResourceSource,
    RefusingRevocationSource,
    StaffCatalogConfig,
    StaffCatalogPublicationSource,
    load_config,
    main,
    staff_catalog_artifact,
)
from maezo.gateway.human.queue import ReadRefusalError
from maezo.gateway.human.read_profile import (
    CatalogDesignation,
    ReadCatalogArtifact,
    digest,
    parse_model,
    wire,
)
from maezo.gateway.human.read_publisher import PostgresMembershipPublicationSource, PublicationReceipt
from maezo.portal.api.postgres import PostgresIdentityStore
from maezo.portal.api.records import MembershipRecord
from maezo.portal.engine.profile import ProfileError, canonicalize

VECTOR = Path(__file__).resolve().parents[3] / "fixtures" / "portal_read" / "jcs-membership-vector.json"
NOW = datetime(2026, 9, 23, 12, tzinfo=UTC)
PUBLISHER = "portal-staff"
RECEIPT_DIGEST = "d" * 64


def _case(name: str = "staff-utc-microseconds") -> dict:
    return next(c for c in json.loads(VECTOR.read_text(encoding="utf-8"))["cases"] if c["name"] == name)


def _store(*records: MembershipRecord | None, tenant: str = "amh") -> PostgresIdentityStore:
    store = object.__new__(PostgresIdentityStore)
    store.tenant = tenant
    store.get_membership = AsyncMock(side_effect=list(records))
    return store


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


def _handshake(store, clock=None, seconds=300) -> PostgresMembershipHandshake:
    return PostgresMembershipHandshake(
        store=store,
        publisher_ref=PUBLISHER,
        source_ref_prefix="portal-identity:amh:membership:",
        observation_seconds=seconds,
        clock=clock or Clock(),
    )


def _catalog_config(**changes) -> StaffCatalogConfig:
    raw = staff_catalog_artifact(
        catalog_ref="catalog-staff",
        publisher_ref=PUBLISHER,
        deployment_receipt_ref="deployment-1",
        deployment_receipt_digest=RECEIPT_DIGEST,
    )
    value = dict(
        catalog_ref="catalog-staff",
        catalog_revision=1,
        admitted_catalog_digest=hashlib.sha256(raw).hexdigest(),
        deployment_receipt_ref="deployment-1",
        deployment_receipt_digest=RECEIPT_DIGEST,
        source_ref_prefix="portal-read-catalog:amh:",
        valid_seconds=86400,
    )
    value.update(changes)
    return StaffCatalogConfig.model_validate(value)


@pytest.mark.asyncio
async def test_membership_provenance_is_the_t17b_contract_and_matches_the_shared_vector():
    case = _case()
    record = MembershipRecord.model_validate_json(case["row_payload"])
    lease = await _handshake(_store(record)).freeze(record.issuer, record.subject)
    p = lease.provenance
    assert p.source_digest == case["projection_sha256"]
    assert p.source_revision == record.revision and p.publisher_ref == PUBLISHER
    assert p.receipt_ref == "portal-identity:amh:membership:human-1@1"
    assert p.source_ref == "portal-identity:amh:membership:human-1"
    assert p.observed_at == NOW and p.valid_until == NOW + timedelta(seconds=300)


@pytest.mark.asyncio
async def test_source_read_through_the_handshake_yields_the_frozen_projection():
    record = MembershipRecord.model_validate_json(_case()["row_payload"])
    store = _store(record, record)
    snapshot = await PostgresMembershipPublicationSource(store=store, handshake=_handshake(store)).read(
        record.issuer, record.subject
    )
    assert digest(snapshot.payload) == snapshot.source.source_digest


@pytest.mark.asyncio
async def test_row_changed_between_freeze_and_read_is_refused():
    record = MembershipRecord.model_validate_json(_case()["row_payload"])
    changed = record.model_copy(update={"revision": record.revision + 1})
    store = _store(record, changed)
    with pytest.raises(ReadRefusalError):
        await PostgresMembershipPublicationSource(store=store, handshake=_handshake(store)).read(
            record.issuer, record.subject
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("which", ["absent", "other-tenant"])
async def test_absent_or_foreign_row_is_refused(which):
    record = MembershipRecord.model_validate_json(_case()["row_payload"])
    store = _store(None) if which == "absent" else _store(record, tenant="outro")
    with pytest.raises(ReadRefusalError):
        await _handshake(store).freeze(record.issuer, record.subject)


@pytest.mark.asyncio
async def test_lease_dies_after_observation_window_and_after_uncertain():
    record = MembershipRecord.model_validate_json(_case()["row_payload"])
    clock = Clock()
    lease = await _handshake(_store(record), clock).freeze(record.issuer, record.subject)
    lease.live()
    clock.now = NOW + timedelta(seconds=300)
    with pytest.raises(ReadRefusalError):
        lease.live()
    clock.now = NOW
    lease.uncertain()
    with pytest.raises(ReadRefusalError):
        lease.live()


@pytest.mark.parametrize(
    "kwargs",
    [dict(observation_seconds=59), dict(observation_seconds=901), dict(source_ref_prefix="sem-separador")],
)
def test_handshake_configuration_fails_closed(kwargs):
    base = dict(store=_store(), publisher_ref=PUBLISHER, source_ref_prefix="p:", observation_seconds=300)
    base.update(kwargs)
    with pytest.raises(ReadRefusalError):
        PostgresMembershipHandshake(**base)


def test_minimal_catalog_is_a_valid_artifact_with_empty_lists():
    raw = staff_catalog_artifact(
        catalog_ref="c",
        publisher_ref=PUBLISHER,
        deployment_receipt_ref="d",
        deployment_receipt_digest=RECEIPT_DIGEST,
    )
    artifact = parse_model(ReadCatalogArtifact, json.loads(raw))
    assert artifact.entries == () and artifact.policies == () and artifact.forms == ()
    assert canonicalize(wire(artifact)) == raw


@pytest.mark.asyncio
async def test_catalog_designation_carries_the_admitted_digest_and_bytes():
    config = _catalog_config()
    source = StaffCatalogPublicationSource(config=config, publisher_ref=PUBLISHER, clock=Clock())
    snapshot = await source.read("catalog-staff")
    payload = parse_model(CatalogDesignation, wire(snapshot.payload))
    raw = base64.b64decode(payload.catalog_artifact_base64)
    assert hashlib.sha256(raw).hexdigest() == payload.catalog_digest == config.admitted_catalog_digest
    assert payload.valid_until == snapshot.source.valid_until == NOW + timedelta(days=1)
    assert snapshot.source.publisher_ref == PUBLISHER == json.loads(raw)["publisher_ref"]
    assert snapshot.source.source_digest == config.admitted_catalog_digest


def test_catalog_with_a_digest_not_admitted_never_offers_itself():
    with pytest.raises(ReadRefusalError):
        StaffCatalogPublicationSource(
            config=_catalog_config(admitted_catalog_digest="0" * 64), publisher_ref=PUBLISHER
        )
    # same config, other publisher -> other bytes -> other digest
    with pytest.raises(ReadRefusalError):
        StaffCatalogPublicationSource(config=_catalog_config(), publisher_ref="outro-publisher")


@pytest.mark.asyncio
async def test_catalog_refuses_another_ref():
    source = StaffCatalogPublicationSource(config=_catalog_config(), publisher_ref=PUBLISHER)
    with pytest.raises(ReadRefusalError):
        await source.read("outro-catalogo")


@pytest.mark.asyncio
async def test_onda8_sources_always_refuse():
    for call in (
        RefusingResourceSource().read("t"),
        RefusingPagtoSource().read("e", 1, "0" * 64),
        RefusingRevocationSource().read_catalog("c"),
        RefusingRevocationSource().read_key("0" * 64),
    ):
        with pytest.raises(ReadRefusalError):
            await call


class FakePublisher:
    """Stands in for the engine's CAS: receipt revision = expected + 1, conflict otherwise."""

    def __init__(self) -> None:
        self.revision = 0
        self.calls: list[tuple[str, str]] = []
        self.principals: list[dict] = []

    def _receipt(self, kind: str, expected: int) -> PublicationReceipt:
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
                "record_digest": "b" * 64,
            }
        )

    async def publish_catalog(self, catalog_ref, *, expected_revision):
        self.calls.append(("catalog-designate", catalog_ref))
        return self._receipt("catalog-designate", expected_revision)

    async def publish_membership(self, issuer, subject, *, expected_revision):
        self.calls.append(("membership", subject))
        return self._receipt("membership", expected_revision)

    async def publish_principal(self, raw):
        command = json.loads(raw)
        if int(command["expected_revision"]) != self.revision:
            raise ReadRefusalError("read_dependency_unavailable")
        self.revision += 1
        self.calls.append(("principal", command["subject"]))
        self.principals.append(command)
        return {
            "schema": "human-authority-receipt.v1",
            "tenant": command["tenant"],
            "revision": str(self.revision),
            "digest": hashlib.sha256(raw).hexdigest(),
        }


class RowStore:
    def __init__(self, *records: MembershipRecord) -> None:
        self.rows = {(r.issuer, r.subject): r for r in records}
        self.store = object.__new__(PostgresIdentityStore)
        self.store.tenant = "amh"
        self.store.get_membership = AsyncMock(side_effect=lambda i, s: self.rows.get((i, s)))


def _job(rows: RowStore, publisher: FakePublisher, ledger_path: Path) -> MembershipPublicationJob:
    job = MembershipPublicationJob(
        publisher=publisher,
        ledger=PublicationLedger(ledger_path, "amh"),
        catalog=StaffCatalogPublicationSource(config=_catalog_config(), publisher_ref=PUBLISHER),
        handshake=_handshake(rows.store, clock=lambda: datetime.now(UTC)),
        store=rows.store,
        engine=None,  # type: ignore[arg-type]
        workload_ref=PUBLISHER,
    )
    job._principals = AsyncMock(side_effect=lambda: sorted(rows.rows))  # type: ignore[method-assign]
    return job


def _record(name: str, **changes) -> MembershipRecord:
    base = MembershipRecord.model_validate_json(_case()["row_payload"])
    return base.model_copy(
        update=dict(subject=f"subject-{name}", principal_ref=f"principal-{name}", **changes)
    )


@pytest.mark.asyncio
async def test_second_run_publishes_nothing_and_a_new_revision_publishes_only_that(tmp_path):
    rows, publisher, path = RowStore(_record("a"), _record("b")), FakePublisher(), tmp_path / "ledger.json"
    first = await _job(rows, publisher, path).run()
    assert (first.catalog_published, first.memberships_published, first.authority_revision) == (True, 2, 5)
    assert first.principals_published == 2
    second = await _job(rows, publisher, path).run()  # new process, same durable ledger
    assert (second.catalog_published, second.memberships_published, second.memberships_unchanged) == (
        False,
        0,
        2,
    )
    assert second.principals_published == 0
    assert len(publisher.calls) == 5
    rows.rows[(_record("a").issuer, "subject-a")] = _record("a", revision=2, revoked=True)
    third = await _job(rows, publisher, path).run()
    assert (third.memberships_published, third.memberships_unchanged, third.authority_revision) == (1, 1, 7)
    assert publisher.calls[-2:] == [("principal", "subject-a"), ("membership", "subject-a")]
    assert publisher.principals[-1]["active"] is False
    fourth = await _job(rows, publisher, path).run()  # the deactivation is not re-sent
    assert (fourth.principals_published, fourth.authority_revision) == (0, 7)


@pytest.mark.asyncio
async def test_non_staff_rows_are_not_published(tmp_path):
    rows, publisher = RowStore(_record("p", audience="provider")), FakePublisher()
    result = await _job(rows, publisher, tmp_path / "l.json").run()
    assert result.memberships_published == 0 and publisher.calls == [("catalog-designate", "catalog-staff")]


@pytest.mark.asyncio
async def test_stale_ledger_revision_fails_closed_until_rebased(tmp_path):
    rows, publisher, path = RowStore(_record("a")), FakePublisher(), tmp_path / "l.json"
    publisher.revision = 7  # another publisher advanced the tenant's authority counter
    with pytest.raises(ReadRefusalError):
        await _job(rows, publisher, path).run()
    PublicationLedger(path, "amh").rebase(7)
    assert (await _job(rows, publisher, path).run()).authority_revision == 10


def test_ledger_of_another_tenant_is_refused(tmp_path):
    path = tmp_path / "l.json"
    PublicationLedger(path, "amh").rebase(1)
    with pytest.raises(ReadRefusalError):
        PublicationLedger(path, "outro")


def test_ledger_refuses_a_receipt_that_skips_a_revision(tmp_path):
    ledger = PublicationLedger(tmp_path / "l.json", "amh")
    publisher = FakePublisher()
    publisher.revision = 5
    receipt = publisher._receipt("membership", 5)
    source = _catalog_source_provenance()
    with pytest.raises(ReadRefusalError):
        ledger.record("membership", source, receipt)


def _catalog_source_provenance():
    import asyncio

    return asyncio.run(
        StaffCatalogPublicationSource(config=_catalog_config(), publisher_ref=PUBLISHER).read("catalog-staff")
    ).source


def test_catalog_digest_command_prints_what_the_admission_must_carry(capsys):
    assert (
        main(
            [
                "catalog-digest",
                "--catalog-ref",
                "catalog-staff",
                "--publisher-ref",
                PUBLISHER,
                "--deployment-receipt-ref",
                "deployment-1",
                "--deployment-receipt-digest",
                RECEIPT_DIGEST,
            ]
        )
        == 0
    )
    assert capsys.readouterr().out.strip() == _catalog_config().admitted_catalog_digest


def test_publish_without_material_fails_closed_without_echoing(tmp_path, capsys):
    config = tmp_path / "c.json"
    config.write_text("{}", encoding="utf-8")
    assert main(["publish", "--config", str(config)]) == 2
    err = capsys.readouterr().err
    assert err.startswith("publication refused:") and "{}" not in err


# --- T1.5: principal -> /v1/authority -------------------------------------------------------


@pytest.mark.asyncio
async def test_principal_is_the_authority_command_and_precedes_its_membership(tmp_path):
    rows, publisher = RowStore(_record("a")), FakePublisher()
    await _job(rows, publisher, tmp_path / "l.json").run()
    assert publisher.calls == [
        ("catalog-designate", "catalog-staff"),
        ("principal", "subject-a"),
        ("membership", "subject-a"),
    ]
    command = publisher.principals[0]
    record = _record("a")
    assert set(command) == {
        "schema",
        "tenant",
        "workload_ref",
        "operation",
        "expected_revision",
        "principal_ref",
        "issuer",
        "subject",
        "active",
        "valid_until",
        "groups",
    }
    assert (command["schema"], command["operation"], command["expected_revision"]) == (
        "human-authority.v1",
        "principal",
        "1",
    )
    assert (command["tenant"], command["workload_ref"], command["principal_ref"]) == (
        "amh",
        PUBLISHER,
        "principal-a",
    )
    assert command["active"] is True
    assert command["groups"] == sorted({g for b in record.memberships for g in b.groups})
    assert command["valid_until"] == str(int(record.reviewed_until.timestamp()))


@pytest.mark.asyncio
async def test_inactive_principal_never_published_is_not_created(tmp_path):
    rows, publisher = RowStore(_record("a", revoked=True)), FakePublisher()
    result = await _job(rows, publisher, tmp_path / "l.json").run()
    assert result.principals_published == 0
    assert ("principal", "subject-a") not in publisher.calls


@pytest.mark.parametrize(
    "tamper",
    [
        lambda r: dict(r, digest="0" * 64),
        lambda r: dict(r, tenant="outro"),
        lambda r: dict(r, revision=str(int(r["revision"]) + 1)),
        lambda r: dict(r, schema="human-authority-receipt.v0"),
        lambda r: {**r, "extra": "x"},
    ],
)
@pytest.mark.asyncio
async def test_authority_receipt_that_does_not_match_fails_closed_and_ledger_keeps_nothing(tmp_path, tamper):
    rows, publisher, path = RowStore(_record("a")), FakePublisher(), tmp_path / "l.json"
    honest = publisher.publish_principal

    async def lying(raw):
        return tamper(await honest(raw))

    publisher.publish_principal = lying  # type: ignore[method-assign]
    with pytest.raises(ReadRefusalError):
        await _job(rows, publisher, path).run()
    assert PublicationLedger(path, "amh").entry("principal", "principal-a") is None
    assert ("membership", "subject-a") not in publisher.calls


@pytest.mark.asyncio
async def test_authority_transport_error_is_a_read_refusal(tmp_path):
    rows, publisher = RowStore(_record("a")), FakePublisher()

    async def broken(raw):
        raise RuntimeError("REVISION_CONFLICT")

    publisher.publish_principal = broken  # type: ignore[method-assign]
    with pytest.raises(ReadRefusalError):
        await _job(rows, publisher, tmp_path / "l.json").run()


def test_job_refuses_without_workload(tmp_path):
    rows = RowStore(_record("a"))
    with pytest.raises(ReadRefusalError):
        MembershipPublicationJob(
            publisher=FakePublisher(),
            ledger=PublicationLedger(tmp_path / "l.json", "amh"),
            catalog=StaffCatalogPublicationSource(config=_catalog_config(), publisher_ref=PUBLISHER),
            handshake=_handshake(rows.store),
            store=rows.store,
            engine=None,  # type: ignore[arg-type]
            workload_ref="",
        )


def test_load_config_accepts_a_complete_file_with_authority(tmp_path: Path) -> None:
    """F3 (C1): `authority` was a forward reference the profile decoder never resolved."""
    raw = {
        "schema": "portal-membership-publication-job.v1",
        "tenant": "amh",
        "identity_dsn_file": "/run/dsn",
        "ledger_file": "/run/ledger.json",
        "membership_source_ref_prefix": "portal-membership:",
        "observation_seconds": "600",
        "catalog": {
            "catalog_ref": "catalog-staff",
            "catalog_revision": "1",
            "admitted_catalog_digest": "a" * 64,
            "deployment_receipt_ref": "receipt-1",
            "deployment_receipt_digest": RECEIPT_DIGEST,
            "source_ref_prefix": "portal-catalog:",
            "valid_seconds": "86400",
        },
        "client_certificate_file": "/run/job-client.pem",
        "client_key_file": "/run/job-client-key.pem",
        "publication": {
            "key_file": "/run/publication.pem",
            "key_id": "portal-read-publication-1",
            "fingerprint": "c" * 64,
            "not_after": "2026-09-24T12:00:00.000000Z",
        },
        "authority": {
            "key_file": "/run/authority.pem",
            "key_id": "human-authority-1",
            "audience": "maezo-human",
            "fingerprint": "b" * 64,
            "not_after": "2026-09-24T12:00:00.000000Z",
            "max_envelope_seconds": "30",
        },
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    config = load_config(str(path))
    assert config.authority.key_id == "human-authority-1"
    assert config.authority.max_envelope_seconds == 30
    assert config.publication.key_id == "portal-read-publication-1"
    raw["authority"]["unknown"] = "x"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ProfileError):
        load_config(str(path))


# --- renewal before the source expires (the 503 after 10 min) -----------------------------


def _clocked_job(
    rows: RowStore, publisher: FakePublisher, path: Path, clock, *, seconds=600, catalog_seconds=3600
):
    job = MembershipPublicationJob(
        publisher=publisher,
        ledger=PublicationLedger(path, "amh"),
        catalog=StaffCatalogPublicationSource(
            config=_catalog_config(valid_seconds=catalog_seconds), publisher_ref=PUBLISHER, clock=clock
        ),
        handshake=_handshake(rows.store, clock=clock, seconds=seconds),
        store=rows.store,
        engine=None,  # type: ignore[arg-type]
        workload_ref=PUBLISHER,
        clock=clock,
    )
    job._principals = AsyncMock(side_effect=lambda: sorted(rows.rows))  # type: ignore[method-assign]
    return job


def _live_record(name: str) -> MembershipRecord:
    return _record(name, reviewed_until=NOW + timedelta(days=30), revoked=False)


@pytest.mark.asyncio
async def test_unchanged_membership_is_renewed_once_half_its_validity_is_gone(tmp_path):
    clock, rows, publisher, path = Clock(), RowStore(_live_record("a")), FakePublisher(), tmp_path / "l.json"
    first = await _clocked_job(rows, publisher, path, clock).run()
    assert first.memberships_published == 1
    clock.now = NOW + timedelta(seconds=299)  # first half: idempotent by content
    assert (await _clocked_job(rows, publisher, path, clock).run()).memberships_unchanged == 1
    clock.now = NOW + timedelta(seconds=300)  # half gone: renew the SAME revision/digest
    renewed = await _clocked_job(rows, publisher, path, clock).run()
    assert (renewed.memberships_published, renewed.principals_published) == (1, 0)
    assert publisher.calls[-1] == ("membership", "subject-a")
    entry = PublicationLedger(path, "amh").entry("membership", "portal-identity:amh:membership:principal-a")
    assert entry is not None and entry.valid_until == NOW + timedelta(seconds=900)
    clock.now = NOW + timedelta(seconds=599)  # the renewed window is fresh again
    assert (await _clocked_job(rows, publisher, path, clock).run()).memberships_published == 0


@pytest.mark.asyncio
async def test_a_five_minute_cadence_never_lets_the_source_expire(tmp_path):
    clock, rows, publisher, path = Clock(), RowStore(_live_record("a")), FakePublisher(), tmp_path / "l.json"
    ledger_ref = "portal-identity:amh:membership:principal-a"
    for minute in range(0, 61, 5):
        clock.now = NOW + timedelta(minutes=minute, seconds=7)  # jitter of the scheduler
        await _clocked_job(rows, publisher, path, clock).run()
        entry = PublicationLedger(path, "amh").entry("membership", ledger_ref)
        # before the NEXT run the committed validity still covers it
        assert entry is not None and entry.valid_until > clock.now + timedelta(minutes=5)


@pytest.mark.asyncio
async def test_catalog_is_redesignated_before_its_validity_ends(tmp_path):
    clock, rows, publisher, path = Clock(), RowStore(), FakePublisher(), tmp_path / "l.json"
    assert (await _clocked_job(rows, publisher, path, clock).run()).catalog_published is True
    clock.now = NOW + timedelta(seconds=1799)
    assert (await _clocked_job(rows, publisher, path, clock).run()).catalog_published is False
    clock.now = NOW + timedelta(seconds=1800)
    assert (await _clocked_job(rows, publisher, path, clock).run()).catalog_published is True
    assert publisher.calls == [("catalog-designate", "catalog-staff")] * 2
    entry = PublicationLedger(path, "amh").entry("catalog-designate", "portal-read-catalog:amh:catalog-staff")
    assert entry is not None and entry.source_revision == 2  # same bytes, next revision
    clock.now = NOW + timedelta(seconds=1900)  # the renewed revision is probed, not the config one
    assert (await _clocked_job(rows, publisher, path, clock).run()).catalog_published is False


@pytest.mark.asyncio
async def test_ledger_without_validity_renews_and_a_failed_renewal_fails_closed(tmp_path):
    clock, rows, publisher, path = Clock(), RowStore(_live_record("a")), FakePublisher(), tmp_path / "l.json"
    await _clocked_job(rows, publisher, path, clock).run()
    legacy = json.loads(path.read_bytes())
    for entry in legacy["entries"]:
        entry.pop("valid_until", None)  # a ledger written before renewal existed
    path.write_text(json.dumps(legacy), encoding="utf-8")
    renewed = await _clocked_job(rows, publisher, path, clock).run()
    assert (renewed.catalog_published, renewed.memberships_published) == (True, 1)
    clock.now = NOW + timedelta(seconds=400)
    publisher.revision += 1  # engine moved: the renewal CAS must refuse, not skip silently
    with pytest.raises(ReadRefusalError):
        await _clocked_job(rows, publisher, path, clock).run()
