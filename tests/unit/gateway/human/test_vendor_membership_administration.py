"""VW1-P0 closed vendor administrative boundary; store doubles marked UNIT.

Mirrors `tests/unit/gateway/test_provider_membership_administration.py` against the vendor
module (the provider molde itself is never edited). No live PostgreSQL, engine or IdP is
claimed here; the live-PG transport proof is CI's integration lane.
"""

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from maezo.gateway.human.vendor_membership_administration import (
    ADMIN_ACTION,
    AdministrationBinding,
    VendorAdministrationError,
    VendorAdministrationReason,
    VendorAdministrationReceipt,
    VendorChannelState,
    VendorMembershipAdministration,
    VendorMembershipCommand,
    command_bytes,
    command_digest,
)
from maezo.gateway.human.vendor_membership_administration_postgres import (
    PostgresVendorMembershipAdministration,
    VendorSourceDescriptor,
)
from maezo.gateway.pep import PEP, ActionPolicy, AutonomyMatrix, Level
from maezo.portal.api.records import MembershipRecord
from maezo.portal.contracts.models import MembershipBinding, SubjectBinding

ISSUER = "https://idp.example"
NOW = datetime.now(UTC)


def binding(**changes: Any) -> AdministrationBinding:
    return AdministrationBinding(
        **dict(
            tenant="tenant-a",
            actor_ref="human-admin",
            actor_session_ref="admin-session",
            source_authority_ref="operator-identity-source",
            policy_revision="policy-v1",
            valid_until=NOW + timedelta(hours=1),
        )
        | changes
    )


def record(**changes: Any) -> MembershipRecord:
    return MembershipRecord(
        **dict(
            tenant="tenant-a",
            issuer=ISSUER,
            subject="idp-vendor-subject",
            principal_ref="vendor-principal",
            revision=1,
            audience="vendor",
            memberships=(
                MembershipBinding(membership_ref="vendor-binding", roles=("vendor_portal",), groups=()),
            ),
            subject_bindings=(SubjectBinding(kind="vendor", resource_ref="channel-a"),),
            reviewed_until=NOW + timedelta(minutes=10),
            revoked=False,
        )
        | changes
    )


def command(**changes: Any) -> VendorMembershipCommand:
    return VendorMembershipCommand(
        **dict(
            schema_version="vendor-membership-administration.v1",
            command_id="reviewed-vendor-command",
            operation="grant",
            channel_ref="channel-a",
            expected_revision=0,
            record=record(),
        )
        | changes
    )


def channel(**changes: Any) -> VendorChannelState:
    return VendorChannelState(
        **dict(
            tenant="tenant-a",
            channel_ref="channel-a",
            revision=0,
            status="active",
            updated_at=NOW,
        )
        | changes
    )


def pep(*, tenant: str = "tenant-a", level: Level | None = Level.L1) -> PEP:
    actions = {} if level is None else {ADMIN_ACTION: ActionPolicy(ADMIN_ACTION, level, False)}
    return PEP(AutonomyMatrix(tenant, actions))


_ABSENT = object()  # sentinel: an explicitly ABSENT channel row, distinct from the default


class UnitChannels:
    def __init__(self, state: VendorChannelState | object = _ABSENT) -> None:
        self.state = None if state is None else (channel() if state is _ABSENT else state)

    async def channel(self, tenant: str, channel_ref: str) -> VendorChannelState | None:
        if self.state is None:
            return None
        return self.state  # type: ignore[return-value]


class UnitStore:
    """No source acceptance; only tests the service's exact receipt check."""

    calls = 0

    async def record(
        self, c: VendorMembershipCommand, b: AdministrationBinding
    ) -> VendorAdministrationReceipt:
        self.calls += 1
        return VendorAdministrationReceipt(
            schema_version="vendor-membership-administration-receipt.v1",
            tenant=b.tenant,
            command_id=c.command_id,
            request_digest=command_digest(c),
            principal_ref=c.record.principal_ref,
            channel_ref=c.channel_ref,
            membership_revision=c.record.revision,
            administrative_act_ref="unit-act",
            authority_receipt_ref="unit-human-authority",
            relationship_receipt_ref="unit-accreditation",
            audit_receipt_ref="unit-audit",
            committed_at=datetime.now(UTC),
            proof_state="validated",
            application_status="pending",
        )


def service(
    *, channels: Any | None = None, store: Any | None = None, **binding_changes: Any
) -> VendorMembershipAdministration:
    return VendorMembershipAdministration(
        binding=binding(**binding_changes),
        pep=pep(),
        channels=UnitChannels() if channels is None else channels,
        store=UnitStore() if store is None else store,
    )


@pytest.mark.parametrize(
    "change",
    [
        {"channel_ref": "another-channel"},
        {"expected_revision": 1},
        {"operation": "revoke"},
    ],
)
def test_shape_cannot_conflate_relation_revision_or_revocation(change: dict[str, object]) -> None:
    with pytest.raises((VendorAdministrationError, ValidationError)):
        command(**change)


def test_staff_and_provider_records_are_outside_the_vendor_command() -> None:
    stray = record().model_copy(
        update={
            "audience": "provider",
            "subject_bindings": (SubjectBinding(kind="provider", resource_ref="x"),),
        }
    )
    with pytest.raises(VendorAdministrationError):
        command(record=stray)
    assert record().audience == "vendor"
    # A heterogeneous binding cannot even exist on a vendor record: the records validator refuses
    # it at construction, and the command codec keeps refusing the smuggled copy.
    heterogeneous = record().model_copy(
        update={"subject_bindings": (SubjectBinding(kind="provider", resource_ref="channel-a"),)}
    )
    with pytest.raises((VendorAdministrationError, ValidationError)):
        command(record=heterogeneous)


@pytest.mark.parametrize("roles", [("vendor_portal", "vendor_portal"), ()])
def test_duplicate_or_empty_roles_refused(roles: tuple[str, ...]) -> None:
    c = command()
    with pytest.raises((VendorAdministrationError, ValidationError)):
        command(
            record=c.record.model_copy(
                update={"memberships": (MembershipBinding(membership_ref="v", roles=roles, groups=()),)}
            )
        )


async def test_cas_grant_against_the_channel_store_is_a_validated_act_only() -> None:
    store = UnitStore()
    receipt = await service(store=store).record(command())
    assert store.calls == 1
    assert receipt.proof_state == "validated"
    assert receipt.application_status == "pending"
    assert "enabled" not in receipt.model_dump_json()
    assert "applied" not in receipt.model_dump_json()


async def test_store_revision_is_the_cas_anchor_and_drift_is_contract_mismatch() -> None:
    store = UnitStore()
    channels = UnitChannels(channel(revision=3))
    with pytest.raises(VendorAdministrationError) as e:
        await service(channels=channels, store=store).record(command(expected_revision=0))
    assert e.value.reason == VendorAdministrationReason.CONTRACT_MISMATCH
    assert store.calls == 0


async def test_revoked_channel_accreditation_refuses_the_act() -> None:
    store = UnitStore()
    channels = UnitChannels(channel(status="revoked"))
    with pytest.raises(VendorAdministrationError) as e:
        await service(channels=channels, store=store).record(command())
    assert e.value.reason == VendorAdministrationReason.CONTRACT_MISMATCH
    assert store.calls == 0


async def test_absent_or_empty_store_is_source_unavailable_never_zero() -> None:
    store = UnitStore()
    # No channel source wired at all, and a source that does not know the channel: both are
    # UNKNOWN — a typed refusal, never an empty set to act over.
    without_source = VendorMembershipAdministration(binding=binding(), pep=pep(), channels=None, store=store)
    with pytest.raises(VendorAdministrationError) as e:
        await without_source.record(command())
    assert e.value.reason == VendorAdministrationReason.SOURCE_UNAVAILABLE
    assert str(e.value) == "SOURCE_UNAVAILABLE"
    with pytest.raises(VendorAdministrationError) as e:
        await service(channels=UnitChannels(state=None), store=store).record(command())
    assert e.value.reason == VendorAdministrationReason.SOURCE_UNAVAILABLE
    assert store.calls == 0


async def test_revoke_is_a_new_payload_with_revoked_true_never_a_mutation() -> None:
    grant = command()
    grant_record = grant.record
    revoke = command(
        operation="revoke",
        expected_revision=1,
        command_id="revoke-vendor-command",
        record=grant_record.model_copy(update={"revision": 2, "revoked": True}),
    )
    # Revoke-before-change: the granted record keeps its exact identity, untouched.
    assert grant_record.revoked is False and grant_record.revision == 1
    store = UnitStore()
    receipt = await service(channels=UnitChannels(channel(revision=1)), store=store).record(revoke)
    assert receipt.membership_revision == 2
    payload = json.loads(command_bytes(revoke))
    assert payload["record"]["revoked"] is True
    assert payload["operation"] == "revoke"
    assert payload["record"]["revision"] == 2


async def test_revoke_command_rejects_an_unrevoked_record() -> None:
    with pytest.raises(VendorAdministrationError):
        command(operation="revoke", record=record(revoked=False, revision=1))


@pytest.mark.parametrize("policy", [pep(level=None), pep(tenant="tenant-b")])
async def test_unknown_policy_or_cross_tenant_never_calls_source(policy: PEP) -> None:
    store = UnitStore()
    svc = VendorMembershipAdministration(binding=binding(), pep=policy, channels=UnitChannels(), store=store)
    with pytest.raises(VendorAdministrationError) as e:
        await svc.record(command())
    assert e.value.reason == VendorAdministrationReason.AUTHORITY_UNPROVEN
    assert store.calls == 0


async def test_expired_binding_fails_closed() -> None:
    store = UnitStore()
    svc = service(store=store, valid_until=datetime.now(UTC) - timedelta(seconds=1))
    with pytest.raises(VendorAdministrationError):
        await svc.record(command())
    assert store.calls == 0


async def test_missing_source_refuses_before_any_store_call() -> None:
    store = UnitStore()
    svc = VendorMembershipAdministration(binding=binding(), pep=pep(), store=store)
    with pytest.raises(VendorAdministrationError) as e:
        await svc.record(command())
    assert e.value.reason == VendorAdministrationReason.SOURCE_UNAVAILABLE
    assert store.calls == 0


async def test_dishonest_receipt_refused() -> None:
    class WrongStore(UnitStore):
        async def record(
            self, c: VendorMembershipCommand, b: AdministrationBinding
        ) -> VendorAdministrationReceipt:
            return (await super().record(c, b)).model_copy(update={"channel_ref": "other"})

    with pytest.raises(VendorAdministrationError) as e:
        await service(store=WrongStore()).record(command())
    assert e.value.reason == VendorAdministrationReason.CONTRACT_MISMATCH


async def test_vendor_exception_and_cause_are_sanitized() -> None:
    class BrokenStore(UnitStore):
        async def record(
            self, c: VendorMembershipCommand, b: AdministrationBinding
        ) -> VendorAdministrationReceipt:
            raise RuntimeError("vendor-secret")

    with pytest.raises(VendorAdministrationError) as e:
        await service(store=BrokenStore()).record(command())
    assert str(e.value) == "SOURCE_UNAVAILABLE"
    assert e.value.__cause__ is None and e.value.__suppress_context__


# --- PG adapter (posture + generic transport; no live PostgreSQL) --------------------------


def descriptor(**changes: Any) -> VendorSourceDescriptor:
    return VendorSourceDescriptor(
        **dict(
            schema_version="vendor-membership-administration-source.v1",
            tenant="tenant-a",
            database_name="db",
            database_oid=1,
            writer_role="vendor_writer",
            source_authority_ref="operator-identity-source",
            accreditation_receipt_ref="unit-accreditation",
            valid_until=NOW + timedelta(hours=1),
        )
        | changes
    )


def _engine(**kwargs: Any):
    from sqlalchemy.ext.asyncio import create_async_engine

    return create_async_engine("postgresql+asyncpg://test-only:test-only@127.0.0.1:1/test_only", **kwargs)


@pytest.mark.parametrize("echo,hide_parameters", [(True, True), (False, False), (True, False)])
def test_source_rejects_parameter_logging_engine_before_any_connection(
    echo: bool, hide_parameters: bool
) -> None:
    with pytest.raises(VendorAdministrationError) as failure:
        PostgresVendorMembershipAdministration(
            _engine(echo=echo, hide_parameters=hide_parameters), descriptor()
        )
    assert failure.value.reason == VendorAdministrationReason.SOURCE_UNAVAILABLE


class UnitMetadataResult:
    def __init__(self, values: Any) -> None:
        self.values = values

    def mappings(self) -> "UnitMetadataResult":
        return self

    def one(self) -> Any:
        return self.values

    def one_or_none(self) -> Any:
        return self.values


class UnitConnection:
    """SQL metadata DTOs plus a statement census; never a PostgreSQL qualification."""

    def __init__(self, d: VendorSourceDescriptor, **drift: Any) -> None:
        self.d = d
        self.drift = drift
        self.statements: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> UnitMetadataResult:
        sql, params = str(statement), params or {}
        self.statements.append((sql, params))
        if "current_database() AS db" in sql:
            row = dict(
                db=self.drift.get("db", self.d.database_name),
                db_oid=self.d.database_oid,
                login=self.drift.get("login", self.d.writer_role),
                actor=self.d.writer_role,
                now=datetime.now(UTC),
                temp_oid=0,
                tls=True,
            )
        elif "FROM pg_roles r WHERE" in sql:
            flags = {
                k: False
                for k in (
                    "rolsuper",
                    "rolcreaterole",
                    "rolcreatedb",
                    "rolbypassrls",
                    "rolreplication",
                    "membership",
                    "temp",
                )
            }
            flags["rolcanlogin"] = self.drift.get("writer_nologin", True) is True
            row = flags
        elif "portal_auth.apply_change" in sql:
            row = None
        elif "clock_timestamp() AS now" in sql:
            row = dict(now=datetime.now(UTC))
        else:
            raise AssertionError(f"Unexpected statement; no permissive fallback: {sql}")
        return UnitMetadataResult(row)


async def _transport(db: UnitConnection, monkeypatch: Any) -> VendorAdministrationReceipt:
    import maezo.gateway.human.vendor_membership_administration_postgres as vendor_pg

    engine = _engine(echo=False, hide_parameters=True)

    @asynccontextmanager
    async def fake_transaction(engine: Any, seconds: float):
        assert 0 < seconds <= 10
        yield db

    monkeypatch.setattr(vendor_pg, "transaction", fake_transaction)
    store = PostgresVendorMembershipAdministration(engine, descriptor())
    return await store.record(command(), binding())


@pytest.mark.asyncio
async def test_transport_completes_the_generic_apply_change_path_and_never_writes(monkeypatch: Any) -> None:
    db = UnitConnection(descriptor())
    receipt = await _transport(db, monkeypatch)
    assert receipt.application_status == "pending"
    assert receipt.proof_state == "validated"
    applied = [(sql, params) for sql, params in db.statements if "portal_auth.apply_change" in sql]
    assert len(applied) == 1
    assert applied[0][1] == {"tenant": "tenant-a", "id": "reviewed-vendor-command"}
    assert any("FROM pg_roles r WHERE" in sql for sql, _ in db.statements)
    # The adapter is transport-only: revocation rides inside the frozen payload, so every
    # statement issued from here is a read or the one generic SELECT call (never DELETE).
    assert all(sql.lstrip().upper().startswith("SELECT") for sql, _ in db.statements)


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["login", "writer_nologin"])
async def test_qualification_refuses_credential_drift_before_the_generic_call(
    drift: str, monkeypatch: Any
) -> None:
    if drift == "writer_nologin":
        db = UnitConnection(descriptor(), writer_nologin=False)
    else:
        db = UnitConnection(descriptor(), login="someone_else")
    with pytest.raises(VendorAdministrationError) as failure:
        await _transport(db, monkeypatch)
    assert failure.value.reason == VendorAdministrationReason.SOURCE_UNAVAILABLE
    assert not any("portal_auth.apply_change" in sql for sql, _ in db.statements)


@pytest.mark.asyncio
async def test_tenant_mismatch_is_contract_mismatch_before_io(monkeypatch: Any) -> None:
    engine = _engine(echo=False, hide_parameters=True)
    store = PostgresVendorMembershipAdministration(engine, descriptor())
    with pytest.raises(VendorAdministrationError) as failure:
        await store.record(command(record=record(tenant="tenant-b")), binding())
    assert failure.value.reason == VendorAdministrationReason.CONTRACT_MISMATCH


def test_descriptor_requires_its_closed_shape() -> None:
    with pytest.raises(ValidationError):
        descriptor(writer_role="Vendor_Writer")
    with pytest.raises(ValidationError):
        descriptor(database_oid=0)
