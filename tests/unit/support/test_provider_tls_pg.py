"""Pure provenance and mocked CLI lifecycle; these tests never qualify PostgreSQL."""

from __future__ import annotations

import copy
import json
import ssl
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.support import provider_tls_pg as helper


def descriptor(token="a" * 32, owner="provider-authority-source"):
    return {
        "Id": "b" * 64,
        "Name": "/" + helper.CONTAINER_NAME_PREFIX + token,
        "Config": {
            "Image": helper.POSTGRES_IMAGE,
            "Labels": {
                helper.OWNER_LABEL: owner,
                helper.TOKEN_LABEL: token,
                helper.CONTRACT_LABEL: helper.SELF_PROVISIONED_TLS_PG_CONTRACT_VERSION,
                "maezo.test-only": owner,
            },
            "Cmd": ["bash", "-ceu", helper._START_COMMAND],
        },
        "State": {"Running": True},
        "NetworkSettings": {"Ports": {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "15432"}]}},
    }


def validate(value):
    return helper.validate_inspected_container(
        value,
        expected_name=helper.CONTAINER_NAME_PREFIX + "a" * 32,
        expected_owner="provider-authority-source",
        expected_token="a" * 32,
    )


def test_actual_descriptor_contract_has_loopback_port_and_owned_identity():
    value = descriptor()
    before = copy.deepcopy(value)
    published = validate(value)
    assert published.host == "127.0.0.1" and published.port == 15432
    assert published.container_id == "b" * 64
    assert value == before


@pytest.mark.parametrize(
    "mutation",
    ["id", "name", "owner", "token", "contract", "image", "tls", "running", "host", "port", "bindings"],
)
def test_unowned_nonloopback_or_unverified_descriptor_is_refused(mutation):
    value = descriptor()
    if mutation == "id":
        value["Id"] = "unknown"
    elif mutation == "name":
        value["Name"] = "/foreign"
    elif mutation in {"owner", "token", "contract"}:
        key = {"owner": helper.OWNER_LABEL, "token": helper.TOKEN_LABEL, "contract": helper.CONTRACT_LABEL}[
            mutation
        ]
        value["Config"]["Labels"][key] = "foreign"
    elif mutation == "image":
        value["Config"]["Image"] = "postgres:latest"
    elif mutation == "tls":
        value["Config"]["Cmd"] = ["postgres", "-c", "ssl=off"]
    elif mutation == "running":
        value["State"]["Running"] = False
    elif mutation == "host":
        value["NetworkSettings"]["Ports"]["5432/tcp"][0]["HostIp"] = "0.0.0.0"
    elif mutation == "port":
        value["NetworkSettings"]["Ports"]["5432/tcp"][0]["HostPort"] = "0"
    else:
        value["NetworkSettings"]["Ports"]["5432/tcp"].append({"HostIp": "127.0.0.1", "HostPort": "15433"})
    with pytest.raises(helper.FixtureProvenanceError):
        validate(value)


def test_generated_certificate_context_verifies_hostname_and_keeps_private_key_private(tmp_path):
    context = helper.server_certificate(tmp_path)
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert (tmp_path / "server.key").stat().st_mode & 0o777 == 0o600


def test_cli_failure_diagnostic_omits_captured_credentials_and_private_key(monkeypatch):
    def failed(*args, **kwargs):
        return SimpleNamespace(
            returncode=1,
            stdout=b"SYNTHETIC_PASSWORD_DO_NOT_LOG",
            stderr=b"SYNTHETIC_PRIVATE_KEY_DO_NOT_LOG",
        )

    monkeypatch.setattr(helper.subprocess, "run", failed)
    with pytest.raises(helper.FixtureProvenanceError) as error:
        helper.docker("cp", "synthetic-file", "synthetic-container:/tmp/server.key")
    assert str(error.value) == "TestOnly Docker cp failed"
    assert "SYNTHETIC_PASSWORD" not in str(error.value)
    assert "SYNTHETIC_PRIVATE_KEY" not in str(error.value)


@pytest.mark.parametrize("failure", [None, "create", "copy", "start", "readiness", "foreign_cleanup"])
async def test_portable_create_copy_start_and_own_only_cleanup(monkeypatch, tmp_path, failure):
    calls = []
    name = None
    token = None
    password = None
    closed = False

    def cli(*args):
        nonlocal name, token, password
        calls.append(args)
        if args[0] == "create":
            name = args[args.index("--name") + 1]
            token = name.removeprefix(helper.CONTAINER_NAME_PREFIX)
            env_path = Path(args[args.index("--env-file") + 1])
            password = env_path.read_text().strip().split("=", 1)[1]
            assert env_path.stat().st_mode & 0o777 == 0o600
            assert password not in " ".join(args)
            assert "--volume" not in args and "--env" not in args
            if failure == "create":
                raise helper.FixtureProvenanceError("TestOnly create outcome uncertain")
            return "b" * 64
        if args[0] == "cp" and failure == "copy":
            raise helper.FixtureProvenanceError("TestOnly copy failed")
        if args[0] == "start" and failure == "start":
            raise helper.FixtureProvenanceError("TestOnly start failed")
        if args[0] == "inspect":
            value = descriptor(token=token)
            if failure == "foreign_cleanup" and len([c for c in calls if c[0] == "inspect"]) > 1:
                value["Config"]["Labels"][helper.OWNER_LABEL] = "foreign"
            return json.dumps([value])
        if args[0] == "rm":
            assert args == ("rm", "--force", "b" * 64)
        return ""

    async def close():
        nonlocal closed
        closed = True

    async def connect(**kwargs):
        assert kwargs["host"] == "127.0.0.1" and kwargs["port"] == 15432
        assert kwargs["ssl"].verify_mode == ssl.CERT_REQUIRED and kwargs["ssl"].check_hostname
        if failure == "readiness":
            raise OSError("Synthetic unavailable")
        return SimpleNamespace(close=close)

    async def no_wait(_):
        return None

    monkeypatch.setattr(helper, "docker", cli)
    monkeypatch.setattr(helper.asyncpg, "connect", connect)
    monkeypatch.setattr(helper.asyncio, "sleep", no_wait)

    async def exercise():
        async with helper.owned_tls_postgres(tmp_path, owner="provider-authority-source") as pg:
            assert pg.port == 15432 and pg.host == "127.0.0.1"
            assert pg.url_for("synthetic-reader", "synthetic-password").port == 15432
            assert password not in repr(pg)

    if failure:
        with pytest.raises(helper.FixtureProvenanceError):
            await exercise()
    else:
        await exercise()
    assert calls[0][0] == "create"
    assert not (tmp_path / name / "postgres.env").exists()
    assert not (tmp_path / name / "server.key").exists()
    if failure == "foreign_cleanup":
        assert not any(c[0] == "rm" for c in calls)
    else:
        assert any(c[0] == "rm" for c in calls)
    if failure is None or failure == "foreign_cleanup":
        assert closed
    if failure is None:
        assert [c[0] for c in calls[:5]] == ["create", "cp", "cp", "start", "inspect"]
