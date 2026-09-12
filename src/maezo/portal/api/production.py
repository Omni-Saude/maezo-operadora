"""Deployed entry point for the same portal app and actual gateway staff bootstrap."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI

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
    """Bind the human read and command planes for the life of the application.

    `human_runtime` starts the assignment relay before yielding, so the first gateway
    this app builds already has a running relay behind it; on the way out it awaits
    the in-flight delivery before its own pools close. The document slot stays `None`
    (WP-J1-04) and the communication slot stays `None` (its publication source is not
    built yet) — the routes refuse rather than answer from a stub.
    """
    pin = _human_material_pin(settings)
    async with AsyncExitStack() as resources:
        # The tenant cross-check travels INSIDE the pin, so a bundle for another tenant
        # is refused while parsing the manifest — before a pool is opened, before the
        # first query and before the command relay starts. The assertion below is the
        # cheap belt to that braces, not the control.
        runtime = await resources.enter_async_context(human_runtime(pin))
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
        try:
            yield
        finally:
            application.state.task_read_service_factory = None
            application.state.decision_service_factory = None


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
