"""WP-J1-00 — the production composition root binds every human port for real.

The property under test is not "an app starts"; it is *which provider sits behind
each port*. Each test names the port, the concrete class bound to it, and the
refusal that happens when something that is not a real provider is offered instead.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from maezo.gateway.human import production as production_module
from maezo.gateway.human.assignment_composition import AssignmentRuntime
from maezo.gateway.human.assignment_receipt import NativeAssignmentReceiptAuthority
from maezo.gateway.human.assignment_transport import AssignmentPrivateTransport, NativeAssignmentClient
from maezo.gateway.human.engine_evidence import EngineEvidenceReferenceSource
from maezo.gateway.human.engine_reads import (
    EngineCatalogExpectationSource,
    EngineHumanTaskQuery,
    EngineHumanTaskTransport,
    EngineReadBundle,
    EngineReadComposition,
    EngineTaskAuthority,
    EngineTaskDisclosure,
)
from maezo.gateway.human.errors import GatewayRefusalError
from maezo.gateway.human.gateway import create_production_gateway
from maezo.gateway.human.outbox import PostgresHumanAdmission, PostgresHumanOutbox
from maezo.gateway.human.production import compose_human_plane
from maezo.gateway.human.production_materials import HumanMaterialError
from maezo.gateway.human.queue_cursor import AeadQueueCursorCustody
from maezo.gateway.human.read_credentials import ReadCredentialPartition
from maezo.gateway.human.read_materials import (
    MaterialCursorKeys,
    MaterialLifetime,
    MaterialReadAdmission,
    MaterialReadCredentials,
)
from maezo.gateway.human.read_transport import PortalReadClient
from maezo.gateway.human.relay import HumanCommandRelay
from maezo.gateway.human.transport import MTLSHumanEngineTransport, PartitionedEd25519Signer

from tests.unit.gateway.human.materials_builder import build_materials

OUTBOX_DSN = "postgresql://human_outbox:pw@db.invalid:5432/maezo"


def _resolver(tenant: str):  # type: ignore[no-untyped-def]
    """The session resolver is the app's, not a port: only its tenant is read here."""
    from types import SimpleNamespace

    return SimpleNamespace(settings=SimpleNamespace(tenant=tenant))


def _plane(tmp_path, lifetime: MaterialLifetime | None = None):
    """Compose the real production plane over unopened (never connected) resources.

    `asyncpg.create_pool` without `await` and `create_async_engine` are both lazy,
    so this is the production wiring with production providers — only the network
    and the database are absent.
    """
    materials = build_materials(tmp_path / "current")
    pool = asyncpg.create_pool(dsn=OUTBOX_DSN, min_size=1, max_size=2)
    engine = create_async_engine(materials.source_url)
    runtime = compose_human_plane(
        materials,
        pool=pool,
        source_engine=engine,
        lifetime=lifetime or MaterialLifetime(),
    )
    return materials, runtime


async def test_production_app_fills_every_human_slot_and_leaves_documents_none(tmp_path):
    """T00-02 — read, command, receipt and admission ports all bound concretely."""
    materials, runtime = _plane(tmp_path)

    assert isinstance(runtime.read, EngineReadComposition)
    assert isinstance(runtime.assignment, AssignmentRuntime)
    assert runtime.scope == materials.manifest.scope

    # --- read plane: a fresh, fully bound bundle per request ---------------------
    bundle = runtime.read._new_bundle()
    assert isinstance(bundle, EngineReadBundle)
    assert isinstance(bundle.client, PortalReadClient)
    assert isinstance(bundle.cursor, AeadQueueCursorCustody)
    assert bundle.anchor.catalog_ref == materials.manifest.catalog_ref
    assert bundle.anchor.publisher_ref == materials.manifest.publisher_ref
    # Each request gets its own partition and its own client; nothing is shared but
    # the application-owned connection pool.
    other = runtime.read._new_bundle()
    assert other.client is not bundle.client
    assert other.client.partition is not bundle.client.partition
    assert bundle.client._transport is runtime.read._transport_pool
    assert not bundle.client._owns_transport

    partition = bundle.client.partition
    assert isinstance(partition, ReadCredentialPartition)
    assert isinstance(partition._credentials, MaterialReadCredentials)
    assert isinstance(partition._admission, MaterialReadAdmission)
    assert isinstance(bundle.cursor._provider, MaterialCursorKeys)

    # --- command plane -----------------------------------------------------------
    assert isinstance(runtime.assignment._client, NativeAssignmentClient)
    assert isinstance(runtime.assignment._outbox, PostgresHumanOutbox)
    assert isinstance(runtime.assignment._receipt_authority, NativeAssignmentReceiptAuthority)
    assert isinstance(runtime.assignment._relay, HumanCommandRelay)
    assert isinstance(runtime.assignment._client._transport, AssignmentPrivateTransport)
    relay_transport = runtime.assignment._relay._transport
    assert isinstance(relay_transport, MTLSHumanEngineTransport)
    assert isinstance(relay_transport._signer, PartitionedEd25519Signer)

    # --- admission plane: engine-backed evidence, never a constant ---------------
    admission = runtime.read._command_admission
    assert isinstance(admission, PostgresHumanAdmission)
    assert isinstance(admission._evidence, EngineEvidenceReferenceSource)
    assert admission._evidence._client is runtime.assignment._client

    # --- the command credential is installed, not merely referenced --------------
    credentials = runtime.read._command_credentials
    assert credentials.for_workload(runtime.scope).purpose == "human-command"


async def test_read_composition_builds_the_full_bound_port_set(tmp_path):
    """Every `BoundHumanPorts` member and every queue port is an engine adapter."""

    _, runtime = _plane(tmp_path)
    gateway = runtime.read.build(_resolver(runtime.scope.tenant))  # type: ignore[arg-type]

    assert isinstance(gateway._ports.task, EngineHumanTaskTransport)
    assert isinstance(gateway._ports.authority, EngineTaskAuthority)
    assert isinstance(gateway._ports.admission, PostgresHumanAdmission)
    assert isinstance(gateway._query, EngineHumanTaskQuery)
    assert isinstance(gateway._catalog_source, EngineCatalogExpectationSource)
    assert isinstance(gateway._disclosure_source, EngineTaskDisclosure)
    assert isinstance(gateway._cursor_custody, AeadQueueCursorCustody)


async def test_synthetic_or_in_memory_provider_is_refused(tmp_path):
    """T00-03 — there is no seam through which a fake provider can be installed."""
    parameters = inspect.signature(compose_human_plane).parameters
    # Materials + deployment resources only: no provider parameter exists at all.
    assert set(parameters) == {"material", "pool", "source_engine", "lifetime"}

    _, runtime = _plane(tmp_path)
    bundle = runtime.read._new_bundle()
    bound = [
        bundle.client.partition._credentials,
        bundle.client.partition._admission,
        bundle.cursor._provider,
        runtime.assignment._client,
        runtime.assignment._outbox,
        runtime.assignment._receipt_authority,
        runtime.read._command_admission,
        runtime.read._command_admission._evidence,
        runtime.assignment._relay._transport,
    ]
    for provider in bound:
        module = type(provider).__module__
        assert module.startswith("maezo.gateway.human."), module
        assert "test" not in module
        assert "inference" not in module

    # The module itself reaches for nothing synthetic.
    source = inspect.getsource(production_module)
    for forbidden in ("tests.", "Refusing", "Noop", "NoOp", "InMemory", "Fake", "Synthetic"):
        assert forbidden not in source, forbidden

    # And material is the only way in: no bundle, no plane.
    with pytest.raises(HumanMaterialError):
        production_module.load_human_materials("/does/not/exist")


def test_production_factory_still_refuses_and_is_untouched():
    """T00-01 — the unconditional refusal survives; this WP did not weaken it."""
    from maezo.gateway.human.models import Scope

    scope = Scope(tenant="tenant_j1", environment="producao", workload_ref="portal-human")
    with pytest.raises(GatewayRefusalError) as raised:
        create_production_gateway(resolver=_resolver(scope.tenant), scope=scope)  # type: ignore[arg-type]
    assert str(raised.value) == "production_capabilities_unavailable"


async def test_receipt_authority_must_be_native(tmp_path):
    """T00-05 — a substitute receipt authority cannot reach the runtime."""
    materials, runtime = _plane(tmp_path)

    class Substitute:
        scope = materials.manifest.scope

        async def current_authority(self, *args: object, **kwargs: object) -> object:
            raise AssertionError("must never be called")

    with pytest.raises(GatewayRefusalError) as raised:
        AssignmentRuntime(
            scope=runtime.scope,
            client=runtime.assignment._client,
            outbox=runtime.assignment._outbox,
            transport=runtime.assignment._relay._transport,
            credentials=runtime.read._command_credentials,
            receipt_authority=Substitute(),  # type: ignore[arg-type]
            relay_settings=runtime.assignment._relay._settings,
        )
    assert str(raised.value) == "production_capabilities_unavailable"


async def test_lifecycle_start_before_build_and_close_drains_relay(tmp_path):
    """T00-06 — `build` before `start` refuses; `close` awaits the in-flight relay."""
    _, runtime = _plane(tmp_path)

    # No relay running yet: the runtime refuses to hand out a gateway.
    with pytest.raises(GatewayRefusalError) as raised:
        runtime.assignment.build(_resolver(runtime.scope.tenant))  # type: ignore[arg-type]
    assert str(raised.value) == "production_capabilities_unavailable"

    # The composition root starts the relay before it yields; prove the ordering is
    # the one `create_app` and `_human_slots` rely on.
    source = inspect.getsource(production_module.human_runtime)
    assert source.index("assignment.start()") < source.index("yield runtime")
    assert "push_async_callback(runtime.assignment.close)" in source

    # `close` awaits the runner rather than cancelling it.
    closing = inspect.getsource(type(runtime.assignment).close)
    assert "await self._runner" in closing

    await runtime.assignment.close()
    with pytest.raises(GatewayRefusalError):
        await runtime.assignment.start()


async def test_evidence_source_is_engine_backed(tmp_path, monkeypatch):
    """T00-08 — the evidence reference comes from an engine read, never a constant."""
    from maezo.gateway.human.projection import EvidenceReference, ProjectionError

    _, runtime = _plane(tmp_path)
    source = runtime.read._command_admission._evidence
    assert isinstance(source, EngineEvidenceReferenceSource)

    assignment = _legacy_assignment(runtime.scope)
    engine_reference = EvidenceReference(
        tenant=runtime.scope.tenant,
        task_id=assignment.command.task_id,
        evidence_ref="evidence-1",
        revision=assignment.snapshot.evidence_revision,
        digest=assignment.snapshot.evidence_digest,
        valid_until=datetime.now(UTC) + timedelta(minutes=5),
    )
    calls: list[str] = []

    async def engine_says(principal, task_id, operation, *args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(operation)
        return (None, engine_reference, None, None, None)

    monkeypatch.setattr(runtime.assignment._client, "_query", engine_says)
    assert await source.current_reference(assignment) == engine_reference
    assert calls == ["context"]  # one engine read per admission, no cache

    # A reference that disagrees with the engine's current revision is refused.
    stale = engine_reference.model_copy(update={"revision": engine_reference.revision + 1})

    async def engine_disagrees(principal, task_id, operation, *args, **kwargs):  # type: ignore[no-untyped-def]
        return (None, stale, None, None, None)

    monkeypatch.setattr(runtime.assignment._client, "_query", engine_disagrees)
    with pytest.raises(ProjectionError):
        await source.current_reference(assignment)

    # An unreachable engine is a refusal, never a default reference.
    async def engine_down(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        raise RuntimeError("engine unreachable")

    monkeypatch.setattr(runtime.assignment._client, "_query", engine_down)
    with pytest.raises(ProjectionError):
        await source.current_reference(assignment)


def _legacy_assignment(scope):  # type: ignore[no-untyped-def]
    """A minimal, valid `AuthorizedAssignment` for the legacy admission port."""
    from maezo.gateway.human.models import AssignmentCommand, AuthorizedAssignment
    from maezo.portal.contracts.models import HumanPrincipal, MembershipBinding, TaskSnapshot

    moment = datetime.now(UTC)
    snapshot = TaskSnapshot(
        schema_version=1,
        snapshot_at=moment,
        task_id="task-1",
        process_definition_key="SP-OP-AUTH-001",
        process_definition_version=1,
        process_definition_id="SP-OP-AUTH-001:1:abc",
        process_definition_digest="1" * 64,
        task_definition_key="UT_AnaliseMedicoAuditor",
        form_key="auth_decisao",
        form_version=1,
        form_digest="2" * 64,
        form_source_status="BPMN_FORMDATA",
        task_revision=4,
        assignee_ref=None,
        eligible_candidate_groups=("auditores",),
        evidence_revision=7,
        evidence_digest="3" * 64,
        engine_due_at=None,
        allowed_actions=("claim",),
        allowed_inputs=(
            "decisao_auditor",
            "justificativa_clinica",
            "cid10_referencia",
            "fundamentacao_dut",
        ),
        read_only_evidence=None,
    )
    principal = HumanPrincipal(
        schema_version=1,
        tenant=scope.tenant,
        principal_ref="principal-1",
        issuer="https://identity.invalid",
        subject="subject-1",
        membership_revision=2,
        memberships=(
            MembershipBinding(membership_ref="membership-1", roles=("auditor",), groups=("auditores",)),
        ),
        session_ref="session-1",
        authenticated_at=moment,
        subject_bindings=(),
    )
    command = AssignmentCommand(
        schema_version=1,
        command_id="command-1",
        operation="claim",
        task_id="task-1",
        process_definition_key=snapshot.process_definition_key,
        process_definition_version=snapshot.process_definition_version,
        process_definition_id=snapshot.process_definition_id,
        process_definition_digest=snapshot.process_definition_digest,
        task_definition_key=snapshot.task_definition_key,
        form_key=snapshot.form_key,
        form_version=snapshot.form_version,
        form_digest=snapshot.form_digest,
        expected_task_revision=snapshot.task_revision,
        expected_evidence_revision=snapshot.evidence_revision,
        expected_evidence_digest=snapshot.evidence_digest,
        expected_membership_revision=principal.membership_revision,
        expected_authority_revision=1,
    )
    return AuthorizedAssignment(
        scope=scope,
        workload_ref=scope.workload_ref,
        principal=principal,
        snapshot=snapshot,
        authority_revision=1,
        command=command,
    )


def _read_context(scope, assignment):  # type: ignore[no-untyped-def]
    """A real `GovernedAssignmentReadContext`, as the native client would return."""
    from maezo.gateway.human.models import (
        AuthoritativeTask,
        GovernedAssignmentContext,
        GovernedAssignmentReadContext,
    )

    snapshot = assignment.snapshot
    deadline = datetime.now(UTC) + timedelta(minutes=10)
    return GovernedAssignmentReadContext(
        principal=assignment.principal,
        scope=scope,
        task=AuthoritativeTask(
            tenant=scope.tenant,
            snapshot=snapshot,
            active=True,
            authority_revision=1,
            required_roles=("auditor",),
            required_subject_bindings=(),
            required_consent_scopes=(),
            valid_until=deadline,
        ),
        context=GovernedAssignmentContext(
            schema_version="portal-assignment-context.v2",
            task_id=snapshot.task_id,
            process_definition_key=snapshot.process_definition_key,
            process_definition_version=str(snapshot.process_definition_version),
            process_definition_id=snapshot.process_definition_id,
            process_definition_digest=snapshot.process_definition_digest,
            task_definition_key=snapshot.task_definition_key,
            form_key=snapshot.form_key,
            form_version=str(snapshot.form_version),
            form_digest=snapshot.form_digest,
            expected_task_revision=str(snapshot.task_revision),
            expected_evidence_revision=str(snapshot.evidence_revision),
            expected_evidence_digest=snapshot.evidence_digest,
            expected_membership_revision=str(assignment.principal.membership_revision),
            expected_authority_revision="1",
            assignee_ref=None,
            allowed_operations=("claim",),
            valid_until=deadline,
            binding_ref="binding-1",
            binding_version="1",
            binding_digest="4" * 64,
            policy_ref="policy-1",
            policy_version="1",
            policy_digest="5" * 64,
            source_revision="1",
            generation_digest="6" * 64,
        ),
    )


def _phi_connection(scope):  # type: ignore[no-untyped-def]
    from pathlib import Path

    from maezo.gateway.human.decision_custody_connection import (
        PhiDatabaseDeployment,
        PhiPostgresConnection,
    )
    from maezo.gateway.human.transport import HumanTLSIdentity

    directory = Path(__file__).resolve().parent
    return PhiPostgresConnection(
        deployment=PhiDatabaseDeployment(
            scope=scope,
            host="phi.invalid",
            port=5432,
            database="phi",
            writer_role="phi_writer",
            owner_role="phi_owner",
            client_certificate_sha256="7" * 64,
            active_key_id="phi-key-1",
            readable_key_ids=("phi-key-1",),
            valid_until=datetime.now(UTC) + timedelta(hours=1),
            evidence_digest="8" * 64,
        ),
        identity=HumanTLSIdentity(
            scope=scope,
            ca_file=directory / "unused-ca.pem",
            certificate_file=directory / "unused-cert.pem",
            private_key_file=directory / "unused-key.pem",
        ),
        timeout_seconds=5,
    )


async def test_phi_decision_authorization_rechecks_membership_between_preserve_and_resolve(
    tmp_path, monkeypatch
):
    """T00-09 — revoking membership between `preserve` and `resolve` refuses the second."""
    from maezo.gateway.human.decision import DecisionCustodyRecord
    from maezo.gateway.human.decision_custody import CustodyAccess
    from maezo.gateway.human.decision_custody_connection import DecisionCustodyError
    from maezo.gateway.human.phi_decision_authorization import EngineBackedPhiDecisionAuthorization

    _, runtime = _plane(tmp_path)
    scope = runtime.scope
    assignment = _legacy_assignment(scope)
    authorization = EngineBackedPhiDecisionAuthorization(
        client=runtime.assignment._client, connection=_phi_connection(scope)
    )

    record = DecisionCustodyRecord(
        scope=scope,
        principal_ref=assignment.principal.principal_ref,
        task_id=assignment.command.task_id,
        command_id=assignment.command.command_id,
        request_digest="9" * 64,
        custody_ref="custody-1",
        content_digest="a" * 64,
        valid_until=datetime.now(UTC) + timedelta(minutes=10),
    )
    context = _read_context(scope, assignment)
    reads: list[str] = []

    async def engine_grants(principal, task_id):  # type: ignore[no-untyped-def]
        reads.append(task_id)
        return context

    monkeypatch.setattr(runtime.assignment._client, "read_context", engine_grants)
    preserve = await authorization.authorize(
        CustodyAccess(record=record, principal=assignment.principal, operation="preserve")
    )
    assert preserve.access.operation == "preserve"
    assert preserve.valid_until <= record.valid_until

    # `resolve` is a SEPARATE authorization: it must hit the engine again.
    await authorization.authorize(
        CustodyAccess(record=record, principal=assignment.principal, operation="resolve")
    )
    assert len(reads) == 2, "each custody operation re-reads authoritative membership"

    # Membership revoked between preserve and resolve: the second call refuses.
    async def engine_revokes(principal, task_id):  # type: ignore[no-untyped-def]
        raise GatewayRefusalError("production_capabilities_unavailable")

    monkeypatch.setattr(runtime.assignment._client, "read_context", engine_revokes)
    with pytest.raises(DecisionCustodyError):
        await authorization.authorize(
            CustodyAccess(record=record, principal=assignment.principal, operation="resolve")
        )

    # A principal DTO alone never grants: a context for another principal is refused.
    other = assignment.principal.model_copy(update={"principal_ref": "principal-2"})

    async def engine_answers_for_another(principal, task_id):  # type: ignore[no-untyped-def]
        return context

    monkeypatch.setattr(runtime.assignment._client, "read_context", engine_answers_for_another)
    with pytest.raises(DecisionCustodyError):
        await authorization.authorize(
            CustodyAccess(
                record=record.model_copy(update={"principal_ref": "principal-2"}),
                principal=other,
                operation="resolve",
            )
        )


def test_human_plane_is_dark_without_the_new_capability_literal():
    """Compatibility — no new literal, no human plane, and no half-configured profile."""
    from maezo.gateway.staff_cases.production_config import (
        PortalProductionSettings,
        PortalStaffBootstrapError,
    )
    from maezo.portal.api.production import HUMAN_CAPABILITIES

    staff = dict(
        tenant="tenant_j1",
        issuer="https://identity.invalid",
        staff_material_directory="/run/maezo-staff-materials/current",
        staff_material_version_id="staffmaterial" + "0" * 23,
        staff_public_manifest_sha256="1" * 64,
        staff_root_key_sha256="2" * 64,
        staff_designation_sha256="3" * 64,
        staff_native_configuration_sha256="4" * 64,
        staff_scope={
            "tenant": "tenant_j1",
            "environment": "producao",
            "engine_name": "engine-cibseven-1",
            "database_incarnation": "incarnation-1",
        },
        staff_native_origin="https://staff.invalid",
        staff_native_server_spki_sha256="5" * 64,
        staff_read_key_sha256="6" * 64,
        staff_witness_key_sha256="7" * 64,
        staff_maximum_seconds=5,
    )

    # Today's profile is unchanged and must not carry human material.
    today = PortalProductionSettings(capabilities="identity,staff_cases", **staff)
    assert today.human_material_directory is None

    # The new profile requires the material directory...
    with pytest.raises(PortalStaffBootstrapError):
        PortalProductionSettings(capabilities=HUMAN_CAPABILITIES, **staff)
    # ...and the directory is meaningless without the literal.
    with pytest.raises(PortalStaffBootstrapError):
        PortalProductionSettings(
            capabilities="identity,staff_cases",
            human_material_directory="/run/maezo-human-materials/current",
            **staff,
        )
    complete = PortalProductionSettings(
        capabilities=HUMAN_CAPABILITIES,
        human_material_directory="/run/maezo-human-materials/current",
        **staff,
    )
    assert complete.capabilities == HUMAN_CAPABILITIES


def test_document_and_communication_slots_are_left_unbound():
    """Documents are WP-J1-04 and the communication publication source is unbuilt.

    The composition root binds neither: the routes refuse rather than answer from a
    stub, and this test fails the moment someone wires a placeholder into them.
    """
    from maezo.portal.api import production as portal_production

    source = inspect.getsource(portal_production)
    assert "document_service_factory = " not in source
    assert "communication_service_factory = " not in source
    # They are read only to refuse an ambiguous composition.
    assert "application.state.document_service_factory is not None" in source
    assert "application.state.communication_service_factory is not None" in source
