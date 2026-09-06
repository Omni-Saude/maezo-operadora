"""Unit tests for `maezo.agents.lucas.adapters.WhatsAppServerSender` (LUC-08).

LUC-08 (gap `IDEMPOTENCY-KEY-MISSING`): Lucas's outbound WhatsApp seam carried no idempotency
key, so an engine re-delivery of the same turn could resend the informational message or the
escalation ACK. These tests prove two things a symbol grep cannot: (1) the adapter FORWARDS the
key it is given — an adapter that accepted `idempotency_key` and dropped it on the floor would be
the forbidden accepted-but-ignored workaround the brief names explicitly; (2) the key reaches a
component that ACTUALLY dedupes — the REAL `WhatsAppServer.send_message` claiming it against a
fake `DedupRegistry` (the same double `tests/unit/tools/test_mcp_whatsapp_idempotency.py` uses
for the `WEBHOOK-WAMID-DEDUP` gap), never a mock that only proves the call shape.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from maezo.agents.lucas.adapters import WhatsAppServerSender
from maezo.tools.mcp_whatsapp.server import WhatsAppServer, WhatsAppSettings
from tests.support.dedup_fakes import FakeDedupRegistry

_KEY = "ESC-amh-wa:amh:deadbeef:respond_member"


def _settings() -> WhatsAppSettings:
    return WhatsAppSettings(whatsapp_token="tok-" + "q" * 28, phone_number_id="1234567890")


def _mock_httpx_client() -> AsyncMock:
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(
        return_value={"messaging_product": "whatsapp", "messages": [{"id": "wamid.out1"}]}
    )
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.post = AsyncMock(return_value=mock_response)
    return client


async def test_whatsapp_server_sender_forwards_idempotency_key_to_send_message() -> None:
    """The adapter is the LOWEST layer in Lucas's own package that owns the send — it must
    FORWARD the key unchanged, never swallow it (LUC-08 build step 2)."""
    server = WhatsAppServer()
    server.send_message = AsyncMock(return_value={"messages": [{"id": "wamid.1"}]})  # type: ignore[method-assign]

    sender = WhatsAppServerSender(server)
    result = await sender.send("some-hash", "ola", idempotency_key=_KEY)

    server.send_message.assert_awaited_once_with("some-hash", "ola", idempotency_key=_KEY)
    assert result == {"messages": [{"id": "wamid.1"}]}


async def test_the_key_reaches_a_component_that_actually_dedupes() -> None:
    """LUC-08 non-negotiable: 'the key MUST reach a component that actually dedupes — a
    parameter that is accepted and ignored is forbidden'. Proven end to end through the REAL
    adapter and the REAL `WhatsAppServer`, against a fake durable store: a replayed send with the
    SAME key must not reach the Cloud API a second time."""
    registry = FakeDedupRegistry()
    server = WhatsAppServer(settings=_settings(), dedup=registry)
    sender = WhatsAppServerSender(server)
    client = _mock_httpx_client()

    with patch("httpx.AsyncClient", return_value=client):
        first = await sender.send("deadbeef", "Aqui esta a 2a via do seu boleto.", idempotency_key=_KEY)
        second = await sender.send("deadbeef", "Aqui esta a 2a via do seu boleto.", idempotency_key=_KEY)

    assert client.post.await_count == 1, "the replay must not reach the Cloud API at all"
    assert "messages" in first
    assert second == {"suppressed_duplicate": True, "idempotency_key": _KEY}


# ---------------------------------------------------------------------------
# §Delta W4-HYGIENE F2 — the PRODUCTION wiring, not just the adapter in isolation
# ---------------------------------------------------------------------------


class _FakeAgentSettings:
    """The duck-typed settings surface `build_agent_seams` reads (it imports no settings class —
    the five roots carry four different types). Only the fields this test's path touches."""

    tenant_id = "amh"
    cibseven_base_url = ""
    cibseven_auth_token = ""
    database_url = "postgresql://maezo:maezo@127.0.0.1:1/maezo"


async def test_the_production_seam_forwards_the_key_from_the_graph_to_the_durable_store() -> None:
    """§Delta F2, part 1. What Lucas's graph actually holds is not this adapter — it is
    `GatedWhatsAppSender`, the gate `build_whatsapp_seam` wraps around it. That decorator
    declared `send(to_hash, text)`, so LUC-08's required kwarg raised `TypeError` INSIDE the
    graph's best-effort `except Exception`: the key never reached the store, the message was
    never sent, and the turn recorded a swallowed transport failure. Driven here through the
    REAL gate + REAL adapter + REAL `WhatsAppServer` against a fake store — the same end-to-end
    shape as the test above, one layer higher.
    """
    from maezo.gateway.tool_registry import build_agent_seam_context, build_whatsapp_seam

    registry = FakeDedupRegistry()
    seam = build_whatsapp_seam(
        seam=build_agent_seam_context(tenant="amh", agent_id="lucas"),
        adapter="lucas",
        dedup=registry,
    )
    # The gate wraps the REAL adapter over a REAL server; only the HTTP boundary is faked.
    seam._inner._server._settings = _settings()  # type: ignore[attr-defined]
    client = _mock_httpx_client()

    with patch("httpx.AsyncClient", return_value=client):
        first = await seam.send("deadbeef", "Aqui esta a 2a via.", idempotency_key=_KEY)
        second = await seam.send("deadbeef", "Aqui esta a 2a via.", idempotency_key=_KEY)

    assert [call for call in registry.calls if call[0] == "claim"], (
        "the key never reached the durable store through the production seam"
    )
    assert client.post.await_count == 1
    assert "messages" in first
    assert second == {"suppressed_duplicate": True, "idempotency_key": _KEY}


async def test_a_seam_built_without_a_dsn_refuses_a_keyed_send_instead_of_ignoring_it() -> None:
    """§Delta F2, part 2. A settings surface with no `database_url` wires no registry — that is
    an honest deployment state. What must NOT happen is the send going out anyway with the key
    dropped on the floor: the caller asked for once-only delivery and would get a 2xx with no
    guarantee. Zero POSTs is the assertion a log line alone never made."""
    from maezo.gateway.tool_registry import build_agent_seam_context, build_whatsapp_seam
    from maezo.tools.mcp_whatsapp.server import WhatsAppIdempotencyUnsupportedError

    seam = build_whatsapp_seam(
        seam=build_agent_seam_context(tenant="amh", agent_id="lucas"), adapter="lucas", dedup=None
    )
    seam._inner._server._settings = _settings()  # type: ignore[attr-defined]
    client = _mock_httpx_client()

    with (
        patch("httpx.AsyncClient", return_value=client),
        pytest.raises(WhatsAppIdempotencyUnsupportedError),
    ):
        await seam.send("deadbeef", "Aqui esta a 2a via.", idempotency_key=_KEY)

    assert client.post.await_count == 0, "an unhonourable key must never produce a delivery"


def test_the_agent_composition_root_wires_the_durable_registry_for_lucas() -> None:
    """§Delta F2, part 3 — the finding itself: Lucas's ONLY production construction site built a
    registry-less `WhatsAppServer`, so the key was accepted and ignored in the one wiring that
    matters. `build_agent_seams` owns the DSN already (it builds the `PostgresAuditSink` from the
    same field), and now hands it to the sender as `dedup=`, exactly as
    `platform/webhooks/service.py` does for Helena's live client.

    Asserted on the CONSTRUCTED OBJECT GRAPH rather than by sending: a real claim would open a
    socket, and what this test is about is the wiring decision, not the store's behaviour (which
    `test_the_production_seam_forwards_the_key_from_the_graph_to_the_durable_store` covers).
    """
    from maezo.gateway.tool_registry import build_agent_seams
    from maezo.platform.driver_idempotency import PostgresDriverIdempotencyRegistry

    deps = build_agent_seams(settings=_FakeAgentSettings(), agent_id="lucas")

    server = deps["whatsapp"]._inner._server
    assert isinstance(server._dedup, PostgresDriverIdempotencyRegistry)
    assert server._dedup.tenant == "amh"


def test_a_root_without_a_dsn_wires_no_registry_and_says_so_by_refusing() -> None:
    """The complement: no DSN, no registry — never a silently-degraded one. Paired with
    `test_a_seam_built_without_a_dsn_refuses_a_keyed_send_instead_of_ignoring_it`, this is the
    whole disclosure: absence is real, and absence is announced at the send."""
    from maezo.gateway.tool_registry import build_agent_seams

    class _NoDsnSettings(_FakeAgentSettings):
        database_url = ""

    deps = build_agent_seams(settings=_NoDsnSettings(), agent_id="lucas")

    assert deps["whatsapp"]._inner._server._dedup is None
