"""The deployed factory uses the existing app/identity root and owned staff lifetime."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from maezo.gateway.staff_cases.production_config import PortalStaffBootstrapError
from maezo.portal.api import __main__ as entry
from maezo.portal.api import production as p


def test_deployed_module_selects_production_root(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(entry.uvicorn, "run", lambda *a, **kw: calls.append((a, kw)))
    entry.main()
    assert calls == [
        (
            ("maezo.portal.api.production:create_production_app",),
            dict(factory=True, host="0.0.0.0", port=8080, access_log=False, proxy_headers=False),
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "identity", "staff", "body"])
async def test_same_app_root_lifetime_and_factory_cleanup(monkeypatch, failure) -> None:
    events = []
    identity = SimpleNamespace(mode="production", tenant="t", issuer="i")
    settings = SimpleNamespace(capabilities="identity,staff_cases", tenant="t", issuer="i")

    @asynccontextmanager
    async def original(app):
        events.append("identity:start")
        try:
            if failure == "identity":
                raise RuntimeError("synthetic-secret")
            yield
        finally:
            events.append("identity:close")

    app = FastAPI(lifespan=original)
    app.state.staff_case_service_factory = None
    untouched = object()
    app.state.case_service_factory = untouched

    @app.get("/existing")
    def existing():
        return {}

    service = object()

    @asynccontextmanager
    async def staff(config, actual_identity):
        assert actual_identity is identity and config is settings
        events.append("staff:start")
        try:
            if failure == "staff":
                raise RuntimeError("synthetic-secret")
            yield SimpleNamespace(service=service)
        finally:
            assert app.state.staff_case_service_factory is None
            events.append("staff:close")

    monkeypatch.setattr(p, "PortalSettings", lambda: identity)
    monkeypatch.setattr(p, "PortalProductionSettings", lambda: settings)
    monkeypatch.setattr(p, "create_app", lambda config: app)
    monkeypatch.setattr(p, "staff_runtime", staff)
    assert p.create_production_app() is app
    caught = None
    try:
        async with app.router.lifespan_context(app):
            assert app.state.staff_case_service_factory is service
            assert app.state.case_service_factory is untouched
            assert any(r.path == "/existing" for r in app.routes)
            if failure == "body":
                raise RuntimeError("synthetic-secret")
    except PortalStaffBootstrapError as exc:
        caught = exc
    assert (caught is None) == (failure is None)
    assert app.state.staff_case_service_factory is None
    assert events[-1] == "identity:close"
    if "staff:start" in events:
        assert events[-2] == "staff:close"
    if caught:
        assert str(caught) == "portal_staff_bootstrap_unavailable" and caught.__suppress_context__


def test_pre_lifespan_failure_is_sanitized(monkeypatch) -> None:
    def invalid():
        raise ValueError("synthetic-secret")

    monkeypatch.setattr(p, "PortalProductionSettings", invalid)
    with pytest.raises(PortalStaffBootstrapError, match="^portal_staff_bootstrap_unavailable$") as caught:
        p.create_production_app()
    assert caught.value.__suppress_context__


@pytest.mark.asyncio
@pytest.mark.parametrize("with_audit", [True, False], ids=["audit", "no-audit"])
async def test_human_profile_lends_the_read_audit_to_the_staff_case_detail(monkeypatch, with_audit) -> None:
    """DL-0149: with the human plane, the staff detail records each read in the SAME tenant chain.

    The staff factory is rebound to `staff.service(..., read_audit=<human plane's sink>)` for the
    life of the human slots and restored after. A human plane without a sink (best-effort, owner's
    decision of 19/10/2026) still starts and leaves the staff factory as installed.
    """
    identity = SimpleNamespace(mode="production", tenant="t", issuer="i")
    settings = SimpleNamespace(
        capabilities=p.HUMAN_CAPABILITIES,
        tenant="t",
        issuer="i",
        decision_material_directory=None,
        document_material_directory=None,
    )

    @asynccontextmanager
    async def original(app):
        yield

    app = FastAPI(lifespan=original)
    for name in (
        "staff_case_service_factory",
        "task_read_service_factory",
        "decision_service_factory",
        "document_service_factory",
        "communication_service_factory",
    ):
        setattr(app.state, name, None)
    calls = []

    def service(resolver, *, read_audit=None):
        calls.append((resolver, read_audit))
        return "service"

    @asynccontextmanager
    async def staff(config, actual_identity):
        yield SimpleNamespace(service=service)

    sink = object() if with_audit else None

    @asynccontextmanager
    async def human(pin, **kw):
        yield SimpleNamespace(
            scope=SimpleNamespace(tenant="t"),
            read=SimpleNamespace(build=lambda r: r, read_audit=sink),
            assignment=SimpleNamespace(build=lambda r: r),
            documents=None,
        )

    monkeypatch.setattr(p, "PortalSettings", lambda: identity)
    monkeypatch.setattr(p, "PortalProductionSettings", lambda: settings)
    monkeypatch.setattr(p, "create_app", lambda config: app)
    monkeypatch.setattr(p, "staff_runtime", staff)
    monkeypatch.setattr(p, "human_runtime", human)
    helpers = ("_human_material_pin", "_document_material_pin")
    helpers += ("_decision_material_pin", "_interim_completion")
    for helper in helpers:
        monkeypatch.setattr(p, helper, lambda s: None)
    p.create_production_app()
    if not with_audit:
        async with app.router.lifespan_context(app):
            assert app.state.staff_case_service_factory == service
            assert app.state.staff_case_service_factory("resolver") == "service"
            assert calls == [("resolver", None)]
        assert app.state.staff_case_service_factory is None
        return
    async with app.router.lifespan_context(app):
        assert app.state.staff_case_service_factory("resolver") == "service"
        assert calls == [("resolver", sink)]
    assert app.state.staff_case_service_factory is None
