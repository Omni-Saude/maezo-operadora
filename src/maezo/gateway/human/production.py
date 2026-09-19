"""Production composition root of the human gateway (WP-J1-00).

`create_production_gateway` refuses unconditionally by design and keeps refusing:
it has no way to know whether the ports handed to it are real. This module is the
other half of that contract — the one place that builds the human plane from
attested deployment materials and therefore *may* activate it.

Every port is bound to a concrete provider:

* the Q2 read plane — `EngineReadComposition` over `PortalReadClient`,
  `AeadQueueCursorCustody` and the material-backed read providers (#1, #2, #3);
* the command plane — `AssignmentRuntime` over `PostgresHumanOutbox`,
  `MTLSHumanEngineTransport`, `NativeAssignmentClient` and
  `NativeAssignmentReceiptAuthority`, with the installed `human-command`
  credential (#15) and the `human-assignment-read` lease (#16);
* the admission plane — `PostgresHumanAdmission` over the engine-backed
  `EvidenceReferenceSource` (#7).

Nothing here is a no-op, an in-memory double or a test fixture: this module imports
nothing from `tests/` and nothing from `maezo.runtime.inference`, and it holds no
business rule — eligibility, deadlines, candidate groups and outcomes stay in DMN,
`spec/policies/autonomy/` and the BPMN timers.
"""

from __future__ import annotations

import ssl
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import asyncpg  # type: ignore[import-untyped]
import httpx
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from maezo.gateway.documents.materials import (
    DocumentPlane,
    DocumentPlanePin,
    compose_document_plane,
    load_document_materials,
)

from .assignment_composition import AssignmentRuntime
from .assignment_receipt import NativeAssignmentReceiptAuthority
from .assignment_transport import AssignmentPrivateTransport, NativeAssignmentClient
from .command_materials import assignment_scope, assignment_signing_lease, install_command_credential
from .decision import BoundDecisionPorts
from .decision_binding import BindingConnection, PostgresDecisionBindingSource
from .decision_binding_qualification import QualificationVerifier
from .decision_custody import PostgresHumanDecisionCustody
from .decision_custody_connection import PhiPostgresConnection
from .decision_materials import (
    DECISION_MATERIAL_DIRECTORY,
    DecisionMaterialError,
    DecisionMaterialPin,
    DecisionMaterials,
    load_decision_materials,
)
from .engine_evidence import EngineEvidenceReferenceSource
from .engine_reads import EngineReadBundle, EngineReadComposition
from .errors import GatewayRefusalError
from .models import Scope
from .outbox import PostgresDecisionAdmission, PostgresHumanAdmission, PostgresHumanOutbox
from .phi_decision_authorization import EngineBackedPhiDecisionAuthorization
from .production_materials import (
    MATERIAL_DIRECTORY,
    HumanMaterialError,
    HumanMaterialPin,
    HumanMaterials,
    PrivateSurface,
    load_human_materials,
)
from .queue import CatalogTrustAnchor
from .queue_cursor import AeadQueueCursorCustody
from .read_credentials import ReadCredentialPartition
from .read_materials import MaterialLifetime, read_providers
from .read_transport import PortalReadClient
from .relay import RelaySettings
from .transport import HumanTLSIdentity, MTLSHumanEngineTransport


def unavailable() -> GatewayRefusalError:
    return GatewayRefusalError("production_capabilities_unavailable")


def _surface_identity(surface: PrivateSurface, scope: Scope, directory: str) -> HumanTLSIdentity:
    """Client mTLS material for one private engine surface, from the bundle only.

    The path comes from the verified `HumanMaterials`, and the loader accepts exactly
    one fixed path — so this cannot be pointed anywhere by configuration.
    """
    root = Path(directory)
    return HumanTLSIdentity(
        scope=scope,
        ca_file=root / surface.ca_file,
        certificate_file=root / surface.certificate_file,
        private_key_file=root / surface.private_key_file,
    )


def _surface_context(surface: PrivateSurface, scope: Scope, directory: str) -> ssl.SSLContext:
    context = _surface_identity(surface, scope, directory).context()
    if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED:
        raise unavailable()
    return context


def compose_decision_ports(
    decision: DecisionMaterials,
    *,
    scope: Scope,
    client: NativeAssignmentClient,
    outbox: PostgresHumanOutbox,
) -> BoundDecisionPorts:
    """Bind #22 binding, #23 custody and #25 admission from the decision material plane.

    WP-J1-00 §2.4 left these three unbound, and `HumanGateway.submit_decision` refuses
    outright while `decision_ports is None` — so on `main` today every APROVAR and every
    NEGAR ends in `form_projection_unavailable`. Nothing about that refusal was wrong;
    what was missing is this composition.

    Pure wiring, like `compose_human_plane`: no connection is opened here. Both
    connections are explicit, mTLS-pinned and purpose-separated — the binding plane is
    opened `reader`-only (it never installs qualification) and the PHI plane is the only
    one that ever holds clinical text. #24 (`EngineBackedPhiDecisionAuthorization`, built
    by WP-J1-00) is the authorization the custody provider re-checks on every operation.
    """
    manifest = decision.manifest
    if manifest.scope != scope:
        # All three ports must carry the gateway's own scope or `HumanGateway`
        # refuses with `credential_scope_mismatch`; fail here, with the material named.
        raise DecisionMaterialError()
    root = Path(decision.directory)
    binding_identity = HumanTLSIdentity(
        scope=scope,
        ca_file=root / "binding-ca.pem",
        certificate_file=root / "binding-client-certificate.pem",
        private_key_file=root / "binding-client-key.pem",
    )
    binding = PostgresDecisionBindingSource(
        BindingConnection(
            database=manifest.binding_database,
            identity=binding_identity,
            verifier=QualificationVerifier(decision.root),
            mode="reader",
            timeout_seconds=manifest.binding_timeout_seconds,
        )
    )
    phi_connection = PhiPostgresConnection(
        deployment=manifest.phi.deployment(scope),
        identity=HumanTLSIdentity(
            scope=scope,
            ca_file=root / "phi-ca.pem",
            certificate_file=root / "phi-client-certificate.pem",
            private_key_file=root / "phi-client-key.pem",
        ),
        timeout_seconds=manifest.phi_timeout_seconds,
    )
    custody = PostgresHumanDecisionCustody(
        connection=phi_connection,
        keys=decision.ciphers(),
        authorization=EngineBackedPhiDecisionAuthorization(client=client, connection=phi_connection),
    )
    return BoundDecisionPorts(binding=binding, custody=custody, admission=PostgresDecisionAdmission(outbox))


@dataclass(frozen=True, repr=False)
class HumanRuntime:
    """The compositions `create_app` binds, plus the lifetime that revokes them.

    `documents` is WP-J1-04's plane: `None` keeps the document routes refusing exactly
    as `main` ships them, present it binds the concrete `DocumentAuthority` and
    `ProtectedDocumentProvider` — never a stand-in.
    """

    read: EngineReadComposition
    assignment: AssignmentRuntime
    lifetime: MaterialLifetime
    scope: Scope
    documents: DocumentPlane | None = None


def compose_human_plane(
    material: HumanMaterials,
    *,
    pool: asyncpg.Pool,
    source_engine: AsyncEngine,
    lifetime: MaterialLifetime,
    decision: DecisionMaterials | None = None,
    document: DocumentPlane | None = None,
) -> HumanRuntime:
    """Bind every human port to its concrete provider. Pure wiring: no I/O happens here.

    Separating the wiring from resource acquisition is what makes the binding
    provable: a test can assert which provider sits behind each port without a
    database, and the assertion is about the same code production runs.
    """
    manifest = material.manifest
    scope = manifest.scope
    read_admission, read_credentials, cursor_keys = read_providers(material, lifetime)
    command = install_command_credential(material, lifetime)
    signing = assignment_signing_lease(material, lifetime)

    read_context = _surface_context(manifest.read_surface, scope, material.directory)
    command_identity = _surface_identity(manifest.command_surface, scope, material.directory)
    # Fail closed now if the command surface material cannot produce a verifying context.
    _surface_context(manifest.command_surface, scope, material.directory)
    read_key_id = manifest.key("portal-task-read").key_id
    cursor_key_id = manifest.current_cursor().key_id
    anchor = CatalogTrustAnchor(
        scope=scope, catalog_ref=manifest.catalog_ref, publisher_ref=manifest.publisher_ref
    )

    # --- command plane -----------------------------------------------------------
    outbox = PostgresHumanOutbox(scope=scope, pool=pool)
    engine_transport = MTLSHumanEngineTransport(
        scope=scope,
        endpoint=command.endpoint,
        identity=command_identity,
        signer=command.signer,
        timeout_seconds=command.timeout_seconds,
    )
    assignment_transport = AssignmentPrivateTransport(
        origin=manifest.read_surface.origin,
        tls_context=read_context,
        server_spki_sha256=manifest.read_surface.server_spki_sha256,
        signing=signing,
        timeout_seconds=manifest.read_surface.timeout_seconds,
    )
    client = NativeAssignmentClient(
        transport=assignment_transport,
        source_engine=source_engine,
        command_scope=scope,
        engine_name=manifest.engine_name,
        database_incarnation=manifest.database_incarnation,
    )
    receipt_authority = NativeAssignmentReceiptAuthority(
        transport=assignment_transport, source_engine=source_engine, command_scope=scope
    )
    # #22/#23/#25 — bound only when the decision material plane is deployed. Absent it
    # the ports stay `None` and `submit_decision` keeps refusing: dark by default, and
    # never a stand-in provider to make the refusal go away.
    decision_ports = (
        None
        if decision is None
        else compose_decision_ports(decision, scope=scope, client=client, outbox=outbox)
    )
    assignment = AssignmentRuntime(
        scope=scope,
        client=client,
        outbox=outbox,
        transport=engine_transport,
        credentials=command.partition,
        receipt_authority=receipt_authority,
        decision_ports=decision_ports,
        relay_settings=RelaySettings(
            lease_seconds=manifest.relay_lease_seconds,
            retry_seconds=manifest.relay_retry_seconds,
            poll_seconds=manifest.relay_poll_seconds,
        ),
    )

    # --- read plane --------------------------------------------------------------
    transport_pool: httpx.AsyncBaseTransport = httpx.AsyncHTTPTransport(verify=read_context, trust_env=False)
    admission_port = PostgresHumanAdmission(outbox, EngineEvidenceReferenceSource(client))

    def new_bundle() -> EngineReadBundle:
        # A fresh partition per request: the read partition is explicitly one
        # request's monotonic lifetime, never a shared one.
        partition = ReadCredentialPartition(
            scope=scope,
            engine_name=manifest.engine_name,
            key_id=read_key_id,
            credentials=read_credentials,
            admission=read_admission,
        )
        return EngineReadBundle(
            client=PortalReadClient(
                origin=manifest.read_surface.origin,
                tls_context=read_context,
                server_spki_sha256=manifest.read_surface.server_spki_sha256,
                partition=partition,
                timeout_seconds=manifest.read_surface.timeout_seconds,
                transport=transport_pool,
            ),
            anchor=anchor,
            cursor=AeadQueueCursorCustody(partition=partition, provider=cursor_keys, key_id=cursor_key_id),
        )

    read = EngineReadComposition(
        new_bundle=new_bundle,
        command_credentials=command.partition,
        command_admission=admission_port,
        transport_pool=transport_pool,
    )
    if document is not None and document.tenant != scope.tenant:
        raise unavailable()
    return HumanRuntime(read=read, assignment=assignment, lifetime=lifetime, scope=scope, documents=document)


@asynccontextmanager
async def human_runtime(
    pin: HumanMaterialPin,
    *,
    decision_directory: str | None = None,
    decision_pin: DecisionMaterialPin | None = None,
    document_directory: str | None = None,
    document_pin: DocumentPlanePin | None = None,
) -> AsyncIterator[HumanRuntime]:
    """Compose the human plane; every resource opened here is closed here.

    Ordering is the contract, and it begins with trust: `load_human_materials` refuses
    an unpinned, wrong-tenant, expired or withdrawn bundle *before* the exit stack is
    entered, so no pool is opened, no query runs and no relay starts on material this
    deployment has not anchored out of band. There is no seam that supplies materials
    directly — that would skip both the filesystem custody and the pin.

    After that: the relay starts before any gateway is built, and on the way out the
    in-flight delivery is awaited before the pools it uses close. Pools owned by other
    packages (identity, staff, intake) are never touched.

    `decision_directory` names the decision material plane (WP-J1-06 Phase 0). Naming
    it is the whole activation: absent, the decision ports stay unbound and the gateway
    refuses decisions exactly as it does on `main`; present, it must be the one fixed
    path AND carry its own out-of-band `decision_pin`, or the plane fails to start. It
    is loaded only AFTER the human bundle has been pinned and verified — a decision
    plane is an addition to a trusted human plane, never a way to reach one.

    `document_directory` names the document material plane (WP-J1-04) under the same
    discipline: absent, the document routes keep refusing exactly as `main` ships; present,
    its own pinned bundle is loaded, `compose_document_plane` binds the concrete
    authority and provider, and this same exit stack closes the plane's engines.

    All bundles, and the agreement between their scopes, are settled BEFORE the exit
    stack: a decision or document bundle from another tenant is refused with zero
    connections attempted, not after a pool is already open (V14 MINOR-4).
    """
    lifetime = MaterialLifetime()
    try:
        material = load_human_materials(MATERIAL_DIRECTORY, pin)
        decision = None
        if decision_directory is not None:
            if decision_pin is None:
                raise unavailable()
            decision = load_decision_materials(decision_directory, decision_pin)
            # Cross-plane agreement, still before any resource is acquired. The two
            # bundles are separately pinned and separately signed; nothing but this
            # says they describe the same deployment.
            if decision.manifest.scope != material.manifest.scope:
                raise unavailable()
        document = None
        if document_directory is not None:
            if document_pin is None:
                raise unavailable()
            document = compose_document_plane(load_document_materials(document_directory, document_pin))
            if document.tenant != material.manifest.scope.tenant:
                raise unavailable()
        async with AsyncExitStack() as resources:
            pool = await asyncpg.create_pool(
                dsn=material.outbox_url.render_as_string(hide_password=False).replace(
                    "postgresql+asyncpg://", "postgresql://", 1
                ),
                min_size=1,
                max_size=8,
            )
            if pool is None:
                raise unavailable()
            resources.push_async_callback(pool.close)

            source_engine: AsyncEngine = create_async_engine(
                material.source_url, echo=False, hide_parameters=True, pool_size=2, max_overflow=0
            )
            resources.push_async_callback(source_engine.dispose)
            if document is not None:
                # The document plane's engines are owned by the same lifetime that bound
                # them: no connection outlives the composition that opened it.
                for engine in document.materials.engines:
                    resources.push_async_callback(engine.dispose)

            runtime = compose_human_plane(
                material,
                pool=pool,
                source_engine=source_engine,
                lifetime=lifetime,
                decision=decision,
            )
            # The relay must be running before any gateway is built from this runtime.
            await runtime.assignment.start()
            resources.push_async_callback(runtime.assignment.close)
            resources.push_async_callback(runtime.read.close)
            try:
                yield runtime
            finally:
                # Revoke every issued lease before the resources behind them go away.
                lifetime.close()
    except (GatewayRefusalError, HumanMaterialError, DecisionMaterialError):
        raise
    except Exception:
        raise unavailable() from None
    finally:
        lifetime.close()


def assignment_read_scope(materials: HumanMaterials) -> Scope:
    """Exposed for evidence: the assignment plane's own workload reference."""
    return assignment_scope(materials)


__all__ = [
    "DECISION_MATERIAL_DIRECTORY",
    "HumanRuntime",
    "assignment_read_scope",
    "compose_decision_ports",
    "compose_document_plane",
    "compose_human_plane",
    "human_runtime",
]
