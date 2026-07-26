"""Unit tests for `maezo.platform.webhooks.service` — T4b durable-checkpointer wiring into the
Helena webhook dispatch path, with F2 fail-closed (refuse-to-serve) discipline.

No engine, no network: `_build_dispatcher`'s transports are lazy (asyncpg pool never opened for a
dummy DSN), and the ONE bring-up I/O (the checkpointer connect+setup) is stubbed at the shared
`Checkpointer.connect_and_setup` seam — exactly mirroring `agent_runtime`'s own checkpointer tests.
"""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver

import maezo.platform.webhooks.service as svc
from maezo.platform.webhooks.service import WebhookState, _bring_up_dependencies
from maezo.platform.webhooks.whatsapp.settings import WhatsAppWebhookSettings
from maezo.runtime.checkpoint import Checkpointer

# `_build_dispatcher` requires a DSN (the T-C2 audit sink) — a dummy one suffices, construction is
# lazy (no connection opened). The checkpointer connect against it is stubbed per-test below.
_DSN = "postgresql+asyncpg://user:pw@localhost:5432/maezo"


def _settings(**overrides: object) -> WhatsAppWebhookSettings:
    base: dict[str, object] = {
        "app_secret": "s3cret",
        "verify_token": "vt",
        "tenant_id": "amh",
        # A validly-configured receiver carries the PHI_HMAC_KEY (ADR-0031): a production-mode
        # dispatcher build now fails closed without it. These tests exercise the checkpointer/DSN
        # fences, not the pseudonymizer key gate, so supply a key by default.
        "phi_hmac_key": "test-phi-hmac-key",
    }
    base.update(overrides)
    return WhatsAppWebhookSettings(**base)  # type: ignore[arg-type]


@pytest.fixture
def _stub_connect_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the durable checkpointer connect+setup fail — the F2 branch then depends purely on
    `runtime_mode` (prod refuses to serve; local falls back to in-memory)."""

    async def _refuse(conn_string: str) -> object:
        raise ConnectionError("stubbed: no real Postgres in this unit suite")

    monkeypatch.setattr(svc.Checkpointer, "connect_and_setup", classmethod(lambda cls, dsn: _refuse(dsn)))


@pytest.fixture
def _stub_connect_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the durable checkpointer connect+setup SUCCEED (returns a wrapped saver) — proves the
    happy-path mapping onto the dispatcher without a real Postgres."""

    async def _ok(conn_string: str) -> Checkpointer:
        return Checkpointer(saver=InMemorySaver())

    monkeypatch.setattr(svc.Checkpointer, "connect_and_setup", classmethod(lambda cls, dsn: _ok(dsn)))


# ---------------------------------------------------------------------------
# Fail-closed: production refuses to serve rather than run Helena stateless
# ---------------------------------------------------------------------------


async def test_production_missing_dsn_refuses_to_serve() -> None:
    """PROD (`runtime_mode="kubernetes"`) with NO DATABASE_URL: `_build_dispatcher` already refuses
    (the T-C2 audit sink is mandatory) -> dispatcher None -> `/webhook` 501. No silent stateless."""
    state = WebhookState(settings=_settings(runtime_mode="kubernetes"))
    await _bring_up_dependencies(state)
    assert state.dispatcher is None
    assert state.checkpointer is None
    assert "DATABASE_URL" in (state.dispatcher_error or "")


async def test_production_checkpointer_setup_failure_refuses_to_serve(_stub_connect_fails: None) -> None:
    """PROD with a DSN but a failing checkpointer connect/setup: FAIL-CLOSED — the just-built
    dispatcher is DROPPED (refuse to serve), so `/webhook` returns its explicit 501."""
    state = WebhookState(settings=_settings(runtime_mode="kubernetes", database_url=_DSN))
    await _bring_up_dependencies(state)
    assert state.dispatcher is None  # refuse to serve
    assert state.checkpointer is None
    assert "fail-closed" in (state.dispatcher_error or "")


# ---------------------------------------------------------------------------
# Local/dev: in-memory fallback with a loud warning (dispatcher kept, serves)
# ---------------------------------------------------------------------------


async def test_local_checkpointer_setup_failure_falls_back_to_memory(_stub_connect_fails: None) -> None:
    """LOCAL (`runtime_mode="local"`) with a DSN but a failing connect: degrade to an in-memory
    saver so dev ergonomics work — the dispatcher is KEPT and wired with the memory backend."""
    state = WebhookState(settings=_settings(runtime_mode="local", database_url=_DSN))
    await _bring_up_dependencies(state)
    assert state.dispatcher is not None  # still serves
    assert state.checkpointer_backend == "memory"
    assert state.dispatcher.checkpointer is state.checkpointer


# ---------------------------------------------------------------------------
# Happy path: durable postgres checkpointer wired into the dispatcher
# ---------------------------------------------------------------------------


async def test_durable_checkpointer_wired_into_dispatcher(_stub_connect_ok: None) -> None:
    """DSN present + connect succeeds: the dispatcher is wired with the durable checkpointer and
    `/webhook` serves stateful turns (backend 'postgres')."""
    state = WebhookState(settings=_settings(runtime_mode="kubernetes", database_url=_DSN))
    await _bring_up_dependencies(state)
    assert state.dispatcher is not None
    assert state.checkpointer_backend == "postgres"
    assert state.dispatcher.checkpointer is state.checkpointer
