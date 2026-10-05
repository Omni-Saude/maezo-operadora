"""PW1-B closed contracts and enforcing administrative boundary; store doubles marked UNIT."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from maezo.gateway.human.provider_membership_administration import (
    ADMIN_ACTION,
    AdministrationBinding,
    AdministrationReason,
    ProviderAdministrationError,
    ProviderAdministrationReceipt,
    ProviderMembershipAdministration,
    ProviderMembershipCommand,
    command_digest,
)
from maezo.gateway.human.provider_membership_administration_postgres import (
    RELATIONS,
    AdministrationRelationPin,
    AdministrationSourceDescriptor,
)
from maezo.gateway.pep import PEP, ActionPolicy, AutonomyMatrix, Level
from maezo.portal.api.records import MembershipRecord
from maezo.portal.contracts.models import MembershipBinding, SubjectBinding


def binding(**changes: Any) -> AdministrationBinding:
    return AdministrationBinding(
        **dict(
            tenant="tenant-a",
            actor_ref="human-admin",
            actor_session_ref="admin-session",
            source_authority_ref="operator-identity-source",
            policy_revision="policy-v1",
            valid_until=datetime.now(UTC) + timedelta(hours=1),
        )
        | changes
    )


def command(**changes: Any) -> ProviderMembershipCommand:
    return ProviderMembershipCommand(
        **dict(
            schema_version="provider-membership-administration.v1",
            command_id="reviewed-command",
            operation="grant",
            provider_ref="provider-a",
            expected_revision=0,
            record=MembershipRecord(
                tenant="tenant-a",
                issuer="https://idp.example",
                subject="idp-subject",
                principal_ref="provider-principal",
                revision=1,
                audience="provider",
                memberships=(
                    MembershipBinding(membership_ref="provider-binding", roles=("provider_case",), groups=()),
                ),
                subject_bindings=(SubjectBinding(kind="provider", resource_ref="provider-a"),),
                reviewed_until=datetime.now(UTC) + timedelta(minutes=10),
                revoked=False,
            ),
        )
        | changes
    )


def pep(*, tenant: str = "tenant-a", level: Level | None = Level.L1) -> PEP:
    actions = {} if level is None else {ADMIN_ACTION: ActionPolicy(ADMIN_ACTION, level, False)}
    return PEP(AutonomyMatrix(tenant, actions))


class UnitStore:
    """No source acceptance; only tests the service's exact receipt check."""

    calls = 0

    async def record(
        self, c: ProviderMembershipCommand, b: AdministrationBinding
    ) -> ProviderAdministrationReceipt:
        self.calls += 1
        return ProviderAdministrationReceipt(
            schema_version="provider-membership-administration-receipt.v1",
            tenant=b.tenant,
            command_id=c.command_id,
            request_digest=command_digest(c),
            principal_ref=c.record.principal_ref,
            provider_ref=c.provider_ref,
            membership_revision=c.record.revision,
            administrative_act_ref="unit-act",
            authority_receipt_ref="unit-human-authority",
            relationship_receipt_ref="unit-relationship",
            audit_receipt_ref="unit-audit",
            committed_at=datetime.now(UTC),
            proof_state="validated",
            application_status="pending",
        )


@pytest.mark.parametrize(
    "change",
    [
        {"provider_ref": "another-provider"},
        {"expected_revision": 1},
        {"operation": "revoke"},
    ],
)
def test_shape_cannot_conflate_relation_revision_or_revocation(change: dict[str, object]) -> None:
    with pytest.raises((ProviderAdministrationError, ValidationError)):
        command(**change)


def test_staff_record_refused_and_remains_unchanged() -> None:
    original = command().record
    staff = original.model_copy(update={"audience": "staff", "subject_bindings": ()})
    with pytest.raises(ProviderAdministrationError):
        command(record=staff)
    assert original.audience == "provider"


def test_model_copy_and_output_in_input_are_not_admission() -> None:
    c = command()
    with pytest.raises(ValidationError):
        ProviderMembershipCommand.model_validate(dict(c.model_dump(mode="python"), proof_state="enabled"))
    forged = c.model_copy(update={"provider_ref": "other"})
    with pytest.raises(ProviderAdministrationError):
        command_digest(forged)


@pytest.mark.parametrize("roles", [("provider_case", "provider_case"), ()])
def test_duplicate_or_empty_roles_refused(roles: tuple[str, ...]) -> None:
    c = command()
    record = c.record.model_copy(
        update={"memberships": (MembershipBinding(membership_ref="p", roles=roles, groups=()),)}
    )
    with pytest.raises((ProviderAdministrationError, ValidationError)):
        command(record=record)


@pytest.mark.parametrize("policy", [pep(level=None), pep(tenant="tenant-b")])
async def test_unknown_policy_or_cross_tenant_never_calls_source(policy: PEP) -> None:
    source = UnitStore()
    service = ProviderMembershipAdministration(binding=binding(), pep=policy, store=source)
    with pytest.raises(ProviderAdministrationError) as e:
        await service.record(command())
    assert e.value.reason == AdministrationReason.AUTHORITY_UNPROVEN
    assert source.calls == 0


async def test_expired_binding_and_missing_source_fail_closed() -> None:
    source = UnitStore()
    service = ProviderMembershipAdministration(
        binding=binding(valid_until=datetime.now(UTC) - timedelta(seconds=1)),
        pep=pep(),
        store=source,
    )
    with pytest.raises(ProviderAdministrationError):
        await service.record(command())
    assert source.calls == 0
    with pytest.raises(ProviderAdministrationError) as e:
        await ProviderMembershipAdministration(binding=binding(), pep=pep()).record(command())
    assert e.value.reason == AdministrationReason.SOURCE_UNAVAILABLE


async def test_human_policy_receipt_is_validated_act_only() -> None:
    receipt = await ProviderMembershipAdministration(binding=binding(), pep=pep(), store=UnitStore()).record(
        command()
    )
    assert receipt.proof_state == "validated"
    assert receipt.application_status == "pending"
    assert "enabled" not in receipt.model_dump_json()


async def test_dishonest_receipt_refused() -> None:
    class WrongStore(UnitStore):
        async def record(
            self, c: ProviderMembershipCommand, b: AdministrationBinding
        ) -> ProviderAdministrationReceipt:
            return (await super().record(c, b)).model_copy(update={"provider_ref": "other"})

    with pytest.raises(ProviderAdministrationError) as e:
        await ProviderMembershipAdministration(binding=binding(), pep=pep(), store=WrongStore()).record(
            command()
        )
    assert e.value.reason == AdministrationReason.CONTRACT_MISMATCH


async def test_provider_exception_and_cause_are_sanitized() -> None:
    class BrokenStore(UnitStore):
        async def record(
            self, c: ProviderMembershipCommand, b: AdministrationBinding
        ) -> ProviderAdministrationReceipt:
            raise RuntimeError("patient-secret")

    with pytest.raises(ProviderAdministrationError) as e:
        await ProviderMembershipAdministration(binding=binding(), pep=pep(), store=BrokenStore()).record(
            command()
        )
    assert str(e.value) == "SOURCE_UNAVAILABLE"
    assert e.value.__cause__ is None and e.value.__suppress_context__


def descriptor(**changes: Any) -> AdministrationSourceDescriptor:
    return AdministrationSourceDescriptor(
        **dict(
            schema_version="provider-membership-administration-source.v1",
            tenant="tenant-a",
            database_name="db",
            database_oid=1,
            schema_name="provider_admin_source",
            schema_oid=2,
            owner_role="admin_owner",
            writer_role="admin_writer",
            authority_publisher_role="authority_writer",
            source_authority_ref="operator-identity-source",
            installation_receipt_ref="unit-installation",
            valid_until=datetime.now(UTC) + timedelta(hours=1),
            relations=tuple(
                AdministrationRelationPin(name=n, oid=i + 10, owner="admin_owner")
                for i, n in enumerate(sorted(RELATIONS))
            ),
            function_oid=100,
            function_definition_digest="a" * 64,
        )
        | changes
    )


@pytest.mark.parametrize(
    "change",
    [
        {"schema_name": "maezo_native"},
        {"writer_role": "admin_owner"},
        {"authority_publisher_role": "admin_writer"},
        {"relations": ()},
    ],
)
def test_descriptor_refuses_native_namespace_shared_authority_and_missing_pins(
    change: dict[str, object],
) -> None:
    with pytest.raises(ProviderAdministrationError):
        descriptor(**change)


@pytest.mark.parametrize("echo,hide_parameters", [(True, True), (False, False), (True, False)])
def test_source_rejects_parameter_logging_engine_before_any_connection(echo, hide_parameters):
    from sqlalchemy.ext.asyncio import create_async_engine

    from maezo.gateway.human.provider_membership_administration_postgres import (
        PostgresProviderMembershipAdministration,
    )

    engine = create_async_engine(
        "postgresql+asyncpg://test-only:test-only@127.0.0.1:1/test_only",
        echo=echo,
        hide_parameters=hide_parameters,
    )
    with pytest.raises(ProviderAdministrationError) as failure:
        PostgresProviderMembershipAdministration(engine, descriptor())
    assert failure.value.reason == AdministrationReason.SOURCE_UNAVAILABLE


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["echo", "hide_parameters"])
async def test_logging_configuration_drift_denied_before_record_io(change):
    from sqlalchemy.ext.asyncio import create_async_engine

    from maezo.gateway.human.provider_membership_administration_postgres import (
        PostgresProviderMembershipAdministration,
    )

    engine = create_async_engine(
        "postgresql+asyncpg://test-only:test-only@127.0.0.1:1/test_only", echo=False, hide_parameters=True
    )
    store = PostgresProviderMembershipAdministration(engine, descriptor())
    if change == "echo":
        engine.echo = True
    else:
        engine.sync_engine.hide_parameters = False
    with pytest.raises(ProviderAdministrationError) as failure:
        await store.record(command(), binding())
    assert failure.value.reason == AdministrationReason.SOURCE_UNAVAILABLE
