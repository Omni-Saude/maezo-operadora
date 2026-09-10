"""PFSU-01: identity, request scope and TLS guards before any client exists."""

import ssl
from pathlib import Path

import httpx
import pytest
from tests.unit.gateway.human.test_durable_projection import wire
from tests.unit.gateway.human.test_durable_transport import signer

from maezo.gateway.human.transport import (
    EngineUnavailableError,
    HumanTLSIdentity,
    MTLSHumanEngineTransport,
)


@pytest.mark.parametrize(
    "target", ["identity-tenant", "identity-workload", "signer-tenant", "signer-workload"]
)
def test_configuration_identity_mismatch_before_tls_or_http(monkeypatch, target):
    signing, _, _ = signer()
    original = signing.scope
    mismatch = original.model_copy(
        update={"tenant" if target.endswith("tenant") else "workload_ref": "other"}
    )
    identity_scope = mismatch if target.startswith("identity") else original
    if target.startswith("signer"):
        signing.scope = mismatch
    identity = HumanTLSIdentity(identity_scope, Path("/test/ca"), Path("/test/cert"), Path("/test/key"))
    effects = []
    monkeypatch.setattr(HumanTLSIdentity, "context", lambda _: effects.append("tls"))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: effects.append("http"))
    with pytest.raises(EngineUnavailableError):
        MTLSHumanEngineTransport(
            scope=original,
            endpoint="https://engine.test/human",
            identity=identity,
            signer=signing,
            timeout_seconds=5,
        )
    assert effects == []


@pytest.mark.parametrize("unsafe", ["hostname", "certificate"])
def test_unsafe_tls_context_refuses_before_client(monkeypatch, unsafe):
    signing, _, _ = signer()
    context = ssl.create_default_context()
    context.check_hostname = False
    if unsafe == "certificate":
        context.verify_mode = ssl.CERT_NONE
    identity = HumanTLSIdentity(signing.scope, Path("/test/ca"), Path("/test/cert"), Path("/test/key"))
    effects = []
    monkeypatch.setattr(HumanTLSIdentity, "context", lambda _: context)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: effects.append("http"))
    with pytest.raises(EngineUnavailableError):
        MTLSHumanEngineTransport(
            scope=signing.scope,
            endpoint="https://engine.test/human",
            identity=identity,
            signer=signing,
            timeout_seconds=5,
        )
    assert effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["tenant", "workload_ref", "task_id", "command_id"])
async def test_unauthorized_or_delimiter_command_refuses_before_http(monkeypatch, field):
    signing, _, _ = signer()
    identity = HumanTLSIdentity(signing.scope, Path("/test/ca"), Path("/test/cert"), Path("/test/key"))
    monkeypatch.setattr(HumanTLSIdentity, "context", lambda _: ssl.create_default_context())
    adapter = MTLSHumanEngineTransport(
        scope=signing.scope,
        endpoint="https://engine.test/human",
        identity=identity,
        signer=signing,
        timeout_seconds=5,
    )
    effects = []
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: effects.append("http"))
    # Adversarially corrupt an already validated immutable command to reach the
    # transport guard independently of HumanCommand's earlier validation.
    command = wire()
    object.__setattr__(command, field, "other" if field in ("tenant", "workload_ref") else "../escape")
    for operation in (adapter.dispatch, adapter.receipt):
        with pytest.raises(EngineUnavailableError):
            await operation(command)
    assert effects == []
