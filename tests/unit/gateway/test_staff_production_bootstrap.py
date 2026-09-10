"""Construction/lifetime and exact catalog controls, synthetic SQL only."""

from __future__ import annotations

import asyncio
import hashlib
import ssl
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.engine import make_url

from maezo.gateway.staff_cases import production as p
from maezo.gateway.staff_cases.materials import decode_bundle, verify_materials
from maezo.gateway.staff_cases.production_config import PortalStaffBootstrapError
from maezo.gateway.staff_cases.publisher import StaffNativeClient
from maezo.portal.api.config import PortalSettings
from tests.unit.gateway.test_staff_production_materials import bundle, material_fixture


def identity_settings(issuer: str) -> PortalSettings:
    return PortalSettings(
        tenant="tenant",
        issuer=issuer,
        cognito_origin="https://login.invalid",
        client_id="human",
        client_purpose="dedicated-human-code-pkce",
        machine_client_id="machine",
        public_origin="https://portal.invalid",
        database_url="postgresql+asyncpg://identity_login:synthetic@identity.invalid:5432/identity",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "engine2", "qualify1", "qualify2", "native", "body", "cancel"])
async def test_partial_startup_owns_and_closes_every_resource(monkeypatch, tmp_path, failure) -> None:
    settings, manifest, files = material_fixture()
    parsed, decoded = decode_bundle(bundle(manifest, files), settings)
    materials = verify_materials(settings, parsed, decoded, now=datetime.now(UTC))
    events = []
    counter = 0

    class Engine:
        dialect = SimpleNamespace(name="postgresql")
        echo = False
        sync_engine = SimpleNamespace(hide_parameters=True)

        def __init__(self, name):
            self.name = name

        async def dispose(self):
            events.append("close:" + self.name)

    def engine(*args):
        nonlocal counter
        counter += 1
        if failure == "engine2" and counter == 2:
            raise RuntimeError("synthetic-private")
        result = Engine(str(counter))
        events.append("open:" + result.name)
        return result

    async def qualify(engine, *args):
        events.append("qualify:" + engine.name)
        if failure == "qualify" + engine.name:
            raise RuntimeError("synthetic-private")

    class Native(StaffNativeClient):
        def __init__(self, **kwargs):
            if failure == "native":
                raise RuntimeError("synthetic-private")
            self.signer, self.authority = kwargs["signer"], kwargs["result_authority"]
            events.append("open:native")

        async def close(self):
            events.append("close:native")

    monkeypatch.setattr(p, "load_materials", lambda _: materials)
    monkeypatch.setattr(p, "_engine", engine)
    monkeypatch.setattr(p, "_qualify_login", qualify)
    monkeypatch.setattr(p, "StaffNativeClient", Native)
    monkeypatch.setattr(p, "_quiet", lambda: None)
    scratch = tmp_path / "scratch"
    scratch.mkdir(mode=0o700)
    monkeypatch.setattr(p, "SCRATCH_DIRECTORY", str(scratch))
    monkeypatch.setenv("TMPDIR", str(scratch))
    monkeypatch.setattr(p, "_owned", lambda *a, **kw: None)  # macOS uid, no deployment claim
    caught = None
    try:
        async with p.staff_runtime(settings, identity_settings(manifest["issuer"])) as runtime:
            assert runtime.native.authority is materials.authority
            assert runtime.witnesses.signer.authority is materials.authority
            events.append("yield")
            if failure == "body":
                raise RuntimeError("synthetic-private")
            if failure == "cancel":
                raise asyncio.CancelledError
    except (PortalStaffBootstrapError, asyncio.CancelledError) as exc:
        caught = exc
    assert (caught is None) == (failure is None)
    opened = [v[5:] for v in events if v.startswith("open:")]
    closed = [v[6:] for v in events if v.startswith("close:")]
    assert closed == list(reversed(opened))
    if caught is not None and failure != "cancel":
        assert str(caught) == "portal_staff_bootstrap_unavailable" and caught.__suppress_context__


def test_engine_uses_real_hostname_validating_ssl(monkeypatch, tmp_path) -> None:
    settings, manifest, files = material_fixture()
    parsed, _ = decode_bundle(bundle(manifest, files), settings)
    (tmp_path / "native-witness-ca.pem").write_bytes(files["native-witness-ca.pem"])
    monkeypatch.setattr(p, "MATERIAL_DIRECTORY", str(tmp_path))
    captured = {}

    def create(url, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(p, "create_async_engine", create)
    p._engine(make_url(files["native-witness-dsn.txt"].decode()), parsed.native_witness_connection, 5)
    tls = captured["connect_args"]["ssl"]
    assert tls.check_hostname and tls.verify_mode == ssl.CERT_REQUIRED
    assert captured["hide_parameters"] and not captured["echo"]
    assert captured["max_overflow"] == 0 and captured["connect_args"]["timeout"] == 5


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        None,
        "owner",
        "definition",
        "public_execute",
        "owner_member",
        "prosecdef",
        "executable",
        "proconfig",
        "login",
    ],
)
async def test_exact_installed_lock_function_qualification(monkeypatch, change) -> None:
    settings, manifest, files = material_fixture()
    manifest["session_lock_connection"]["function_pin"]["definition_sha256"] = hashlib.sha256(
        b"qualified"
    ).hexdigest()
    from tests.unit.gateway.test_staff_production_materials import settings_for

    parsed, _ = decode_bundle(bundle(manifest, files), settings_for(manifest))
    login = dict(
        login="lock_login",
        effective="lock_login",
        database="identity",
        rolsuper=False,
        rolbypassrls=False,
        rolcreaterole=False,
        rolcreatedb=False,
        rolreplication=False,
    )
    function = dict(
        oid=12,
        owner="portal_external_identity_reader",
        definition="qualified",
        prosecdef=True,
        executable=True,
        owner_member=False,
        public_execute=False,
        proconfig=["search_path=pg_catalog, portal_identity"],
    )
    if change == "login":
        login["login"] = "unexpected"
    elif change in {"public_execute", "owner_member"}:
        function[change] = True
    elif change in {"prosecdef", "executable"}:
        function[change] = False
    elif change == "proconfig":
        function[change] = ["search_path=public"]
    elif change:
        function[change] = "unexpected"
    calls = []

    class Result:
        def __init__(self, row):
            self.row = row

        def mappings(self):
            return self

        def one(self):
            return self.row

    class Conn:
        async def execute(self, sql):
            calls.append(str(sql))
            return Result(login if len(calls) == 1 else function)

    @asynccontextmanager
    async def tx(*args):
        yield Conn()

    monkeypatch.setattr(p, "transaction", tx)
    monkeypatch.setattr(p, "qualify_engine", lambda *args: None)
    if change:
        with pytest.raises(PortalStaffBootstrapError):
            await p._qualify_login(object(), parsed.session_lock_connection, 5)
    else:
        await p._qualify_login(object(), parsed.session_lock_connection, 5)
        assert len(calls) == 2 and "pg_get_functiondef" in calls[1]
        assert "SELECT * FROM portal_identity.lock_external_session" not in "".join(calls)
