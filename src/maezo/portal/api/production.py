"""Deployed entry point for the same portal app and actual gateway staff bootstrap."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from maezo.gateway.staff_cases.production import staff_runtime
from maezo.gateway.staff_cases.production_config import PortalProductionSettings, PortalStaffBootstrapError
from maezo.portal.api.app import create_app
from maezo.portal.api.config import PortalSettings


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
                                yield
                            finally:
                                application.state.staff_case_service_factory = None
            except Exception:
                raise PortalStaffBootstrapError() from None

        app.router.lifespan_context = lifespan
        return app
    except Exception:
        raise PortalStaffBootstrapError() from None
