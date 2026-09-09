"""ADR-0049/DL-0048: link the dedicated task configuration to its real consumer.

Terraform's companion tests evaluate the actual HCL with an offline AWS provider.
These tests exercise the unchanged Python settings and the inline ECS probe; no
engine, AWS API or database is mocked as an integration claim.
"""

from __future__ import annotations

import http.client
import json
import re
import textwrap
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from maezo.portal.api.app import create_app
from maezo.portal.api.config import PortalSettings
from maezo.portal.api.store import LocalTestIdentityStore

ROOT = Path(__file__).resolve().parents[3]
TASK = ROOT / "deploy/aws-ecs/envs/dev-sa-east-1/service-portal.tf"
# Synthetic only; no credential or provisioned external identity is claimed.
INPUTS = {
    "tenant": "portaltest",
    "issuer": "https://cognito-idp.sa-east-1.amazonaws.com/sa-east-1_TestPool",
    "cognito_origin": "https://login.example.test",
    "human_client_id": "human123",
    "human_client_purpose": "dedicated-human-code-pkce",
    "machine_client_id": "machine123",
    "public_origin": "https://portal.example.test",
}


def deployment_environment() -> dict[str, str]:
    """Read actual literal/source-field pairs, refusing an unrecognized new shape."""
    text = TASK.read_text()
    block = text.split("    environment = [", 1)[1].split("\n    ]", 1)[0]
    entries = re.findall(r'\{ name = "([A-Z_]+)", value = (.*?) \}', block)
    assert len(entries) == block.count("{ name =") == 9
    values = {}
    for name, value in entries:
        values[name] = (
            INPUTS[value.removeprefix("each.value.")]
            if value.startswith("each.value.")
            else json.loads(value)
        )
    return values


@pytest.fixture
def task_environment(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    # Don't inherit any real portal credential/configuration from the test runner.
    import os

    for name in list(os.environ):
        if name.startswith("MAEZO_PORTAL_"):
            monkeypatch.delenv(name)
    values = deployment_environment()
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return values


def test_production_task_consumes_settings_and_requires_dedicated_dsn(task_environment) -> None:
    with pytest.raises(ValidationError, match="persistent session database required"):
        PortalSettings()
    settings = PortalSettings(database_url="postgresql+asyncpg://fixture@db.test/portal")
    assert settings.mode == "production"
    assert settings.tenant == INPUTS["tenant"]
    assert settings.client_id == INPUTS["human_client_id"]
    assert settings.machine_client_id == INPUTS["machine_client_id"]
    assert settings.callback_url == INPUTS["public_origin"] + "/api/v1/portal/auth/callback"


@pytest.mark.parametrize("field", list(INPUTS))
def test_missing_each_required_task_setting_refuses_boot(
    task_environment, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    source_field = {"human_client_id": "client_id", "human_client_purpose": "client_purpose"}.get(
        field, field
    )
    monkeypatch.delenv("MAEZO_PORTAL_" + source_field.upper())
    with pytest.raises(ValidationError, match="Field required"):
        PortalSettings(database_url="postgresql+asyncpg://fixture@db.test/portal")


def test_deployment_keeps_production_override_and_tenant_boundaries(task_environment) -> None:
    settings = PortalSettings(database_url="postgresql+asyncpg://fixture@db.test/portal")
    with pytest.raises(ValueError, match="production dependency overrides prohibited"):
        create_app(settings, store=LocalTestIdentityStore(settings.tenant))
    with pytest.raises(ValueError, match="store boundary"):
        create_app(settings.model_copy(update={"mode": "local-test"}), store=LocalTestIdentityStore("other"))


def probe_source() -> str:
    return textwrap.dedent(TASK.read_text().split("<<-PY\n", 1)[1].split("\n      PY", 1)[0])


async def test_probe_matches_real_unauthenticated_app_and_host(task_environment, monkeypatch) -> None:
    # ASGI in explicitly local-test mode: actual route/middleware, no live socket/IdP/DB.
    settings = PortalSettings(mode="local-test")
    app = create_app(settings, store=LocalTestIdentityStore(settings.tenant))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=settings.public_origin) as client,
    ):
        result = await client.get("/api/v1/portal/session")
        assert result.status_code == 401
        wrong_host = await client.get("/api/v1/portal/session", headers={"Host": "127.0.0.1:8080"})
        assert wrong_host.status_code == 400
    run_probe(monkeypatch, result.status_code, result.content, result.headers["cache-control"])


def run_probe(monkeypatch, status: int, body: bytes, cache_control: str, expected: int = 0) -> None:
    class ProbeConnection:
        def __init__(self, host, port, *, timeout):
            assert (host, port, timeout) == ("127.0.0.1", 8080, 3)

        def request(self, method, path, *, headers):
            assert (method, path) == ("GET", "/api/v1/portal/session")
            assert headers == {"Host": "portal.example.test"}

        def getresponse(self):
            return type(
                "Response",
                (),
                {
                    "status": status,
                    "read": lambda self: body,
                    "getheader": lambda self, key: cache_control,
                },
            )()

        def close(self):
            pass

    monkeypatch.setattr(http.client, "HTTPConnection", ProbeConnection)
    with pytest.raises(SystemExit) as result:
        exec(compile(probe_source(), str(TASK), "exec"), {})
    assert result.value.code == expected


@pytest.mark.parametrize(
    ("status", "body", "cache"),
    [
        (200, {"erro": "Não foi possível validar a sessão."}, "no-store"),
        (400, {"erro": "Não foi possível validar a sessão."}, "no-store"),
        (404, {"detail": "Not Found"}, "no-store"),
        (500, {"erro": "Não foi possível validar a sessão."}, "no-store"),
        (401, {"erro": "wrong body"}, "no-store"),
        (401, {"erro": "Não foi possível validar a sessão."}, "public"),
    ],
)
def test_probe_refuses_wrong_status_body_or_cache(task_environment, monkeypatch, status, body, cache):
    run_probe(monkeypatch, status, json.dumps(body).encode(), cache, expected=1)


def test_entrypoint_is_the_deployed_module_with_exact_security_options(monkeypatch) -> None:
    from maezo.portal.api import __main__ as entrypoint

    called = []
    monkeypatch.setattr(entrypoint.uvicorn, "run", lambda *args, **kwargs: called.append((args, kwargs)))
    assert '["python", "-m", "maezo.portal.api"]' in TASK.read_text()
    entrypoint.main()
    assert called == [
        (
            ("maezo.portal.api.app:create_app",),
            {"factory": True, "host": "0.0.0.0", "port": 8080, "access_log": False, "proxy_headers": False},
        )
    ]
