"""Deployed entry point for the same portal app and actual gateway staff bootstrap."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI

from maezo.gateway.documents.materials import DocumentPlanePin
from maezo.gateway.human.decision_materials import DecisionMaterialPin
from maezo.gateway.human.production import human_runtime
from maezo.gateway.human.production_materials import HumanMaterialPin
from maezo.gateway.staff_cases.production import staff_runtime
from maezo.gateway.staff_cases.production_config import PortalProductionSettings, PortalStaffBootstrapError
from maezo.portal.api.app import create_app
from maezo.portal.api.config import PortalSettings

#: The one profile that activates the human plane (WP-J1-00). Without this literal the
#: app is byte-for-byte the identity/staff app it was before: the plane is dark.
HUMAN_CAPABILITIES = "identity,staff_cases,human"


def create_production_app() -> FastAPI:
    try:
        settings = PortalProductionSettings()  # type: ignore[call-arg]
        identity = PortalSettings()  # type: ignore[call-arg]
        if identity.mode != "production" or (identity.tenant, identity.issuer) != (
            settings.tenant,
            settings.issuer,
        ):
            raise PortalStaffBootstrapError()
        app = create_app(identity)
        original = app.router.lifespan_context

        @asynccontextmanager
        async def lifespan(application: FastAPI) -> AsyncIterator[None]:
            try:
                async with original(application):
                    if settings.capabilities == "identity":
                        yield
                    else:
                        async with staff_runtime(settings, identity) as runtime:
                            if application.state.staff_case_service_factory is not None:
                                raise PortalStaffBootstrapError()
                            application.state.staff_case_service_factory = runtime.service
                            try:
                                if settings.capabilities != HUMAN_CAPABILITIES:
                                    yield
                                else:
                                    async with _human_slots(application, settings):
                                        yield
                            finally:
                                application.state.staff_case_service_factory = None
            except Exception:
                raise PortalStaffBootstrapError() from None

        app.router.lifespan_context = lifespan
        return app
    except Exception:
        raise PortalStaffBootstrapError() from None


@asynccontextmanager
async def _human_slots(application: FastAPI, settings: PortalProductionSettings) -> AsyncIterator[None]:
    """Bind the human read, command and document planes for the life of the application.

    `human_runtime` starts the assignment relay before yielding, so the first gateway
    this app builds already has a running relay behind it; on the way out it awaits
    the in-flight delivery before its own pools close. The document slot (WP-J1-04)
    binds the concrete `DocumentAuthority`/`ProtectedDocumentProvider` only when the
    deployment names the document material plane; otherwise it stays `None` and the
    routes refuse exactly as `main` ships them. The communication slot stays `None`
    (its publication source is not built yet) — the routes refuse rather than answer
    from a stub.

    `decision_material_directory` (WP-J1-06 Phase 0) decides whether the decision
    ports bind. Passing `None` through is the deployment saying "no decision plane":
    the gateway then keeps refusing `POST /tasks/{id}/decisions`, which is not a stub
    but the same fail-closed refusal `main` ships.
    """
    pin = _human_material_pin(settings)
    document = _document_material_pin(settings)
    async with AsyncExitStack() as resources:
        # The tenant cross-check travels INSIDE the pin, so a bundle for another tenant
        # is refused while parsing the manifest — before a pool is opened, before the
        # first query and before the command relay starts. The assertion below is the
        # cheap belt to that braces, not the control.
        # The decision (WP-J1-06) and document (WP-J1-04) planes ride the same call and
        # are loaded only after that verification succeeds; `None` means the plane is
        # dark and the gateway keeps refusing those operations.
        runtime = await resources.enter_async_context(
            human_runtime(
                pin,
                decision_directory=settings.decision_material_directory,
                decision_pin=_decision_material_pin(settings),
                document_directory=None if document is None else settings.document_material_directory,
                document_pin=document,
            )
        )
        if runtime.scope.tenant != settings.tenant:
            raise PortalStaffBootstrapError()
        # Never displace an already-bound factory: an ambiguous composition is a refusal.
        if (
            application.state.task_read_service_factory is not None
            or application.state.decision_service_factory is not None
            or application.state.document_service_factory is not None
            or application.state.communication_service_factory is not None
        ):
            raise PortalStaffBootstrapError()
        application.state.task_read_service_factory = runtime.read.build
        application.state.decision_service_factory = runtime.assignment.build
        # WP-J1-04: the metadata authority and custody provider. Never a byte-serving
        # stub — document bytes stay behind `/api/v1/phi/documents/{ref}/content`, served
        # by the separately deployed PHI application `runtime.documents.phi_service` is
        # the factory for.
        application.state.document_service_factory = (
            None if runtime.documents is None else runtime.documents.service
        )
        try:
            yield
        finally:
            application.state.task_read_service_factory = None
            application.state.decision_service_factory = None
            application.state.document_service_factory = None


def _human_material_pin(settings: PortalProductionSettings) -> HumanMaterialPin:
    """The deployment's out-of-band anchor for the human material bundle.

    `complete_profile` already refuses a half-configured human profile; this repeats
    the narrowing so the pin can never be built from a partially present one, and so
    the type checker sees three non-optional facts.
    """
    if (
        settings.human_material_directory is None
        or settings.human_material_version_id is None
        or settings.human_public_manifest_sha256 is None
    ):
        raise PortalStaffBootstrapError()
    return HumanMaterialPin(
        tenant=settings.tenant,
        material_version_id=settings.human_material_version_id,
        public_manifest_sha256=settings.human_public_manifest_sha256,
    )


def _decision_material_pin(settings: PortalProductionSettings) -> DecisionMaterialPin | None:
    """The deployment's out-of-band anchor for the decision material bundle.

    `None` exactly when the plane is dark. `complete_profile` already refuses a
    half-configured decision profile; this repeats the narrowing so the pin can never
    be built from a partially present one.
    """
    if settings.decision_material_directory is None:
        return None
    if settings.decision_material_version_id is None or settings.decision_public_manifest_sha256 is None:
        raise PortalStaffBootstrapError()
    return DecisionMaterialPin(
        tenant=settings.tenant,
        material_version_id=settings.decision_material_version_id,
        public_manifest_sha256=settings.decision_public_manifest_sha256,
    )


def _document_material_pin(settings: PortalProductionSettings) -> DocumentPlanePin | None:
    """The document plane's out-of-band anchor (WP-J1-04).

    `None` exactly when the plane is dark — the document routes then keep refusing.
    `complete_profile` already refuses a half-configured document profile; this repeats
    the narrowing so the pin can never be built from a partially present one.
    """
    if settings.document_material_directory is None:
        return None
    if settings.document_material_version_id is None or settings.document_public_manifest_sha256 is None:
        raise PortalStaffBootstrapError()
    return DocumentPlanePin(
        tenant=settings.tenant,
        plane_version_id=settings.document_material_version_id,
        public_manifest_sha256=settings.document_public_manifest_sha256,
    )
