"""Unit tests for `maezo.platform.webhooks.whatsapp.dispatch` (T1.11, defect B6)."""

from __future__ import annotations

from typing import Any

import pytest

from maezo.agents.helena.graph import HELENA_INPUT_FIELDS
from maezo.gateway.pseudonymizer import Pseudonymizer
from maezo.platform.webhooks.whatsapp import dispatch as dispatch_module
from maezo.platform.webhooks.whatsapp.dispatch import (
    HelenaDispatcher,
    InboundMessage,
    _ScopedWhatsAppSender,
    extract_inbound_messages,
)
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport


class _FakeInference:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    async def generate(self, prompt: str, *, phi: bool = False) -> str:
        return self._responses.pop(0) if self._responses else ""


class _FakeWhatsAppClient:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_message(self, to: str, text: str) -> dict[str, Any]:
        self.sent.append((to, text))
        return {"messages": [{"id": "wamid.reply.1"}]}


# ---------------------------------------------------------------------------
# extract_inbound_messages — never fabricates a turn out of a non-text/malformed payload
# ---------------------------------------------------------------------------


def test_extract_inbound_messages_single_text_message() -> None:
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": "5511999999999",
                                    "id": "wamid.1",
                                    "type": "text",
                                    "text": {"body": "ola"},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    messages = extract_inbound_messages(payload)
    assert messages == [InboundMessage(from_number="5511999999999", text="ola", message_id="wamid.1")]


def test_extract_inbound_messages_skips_non_text() -> None:
    payload = {"entry": [{"changes": [{"value": {"messages": [{"from": "551199", "type": "image"}]}}]}]}
    assert extract_inbound_messages(payload) == []


def test_extract_inbound_messages_ignores_status_callbacks() -> None:
    payload = {"entry": [{"changes": [{"value": {"statuses": [{"status": "delivered"}]}}]}]}
    assert extract_inbound_messages(payload) == []


def test_extract_inbound_messages_malformed_payload_returns_empty_never_raises() -> None:
    assert extract_inbound_messages({"entry": "not-a-list-of-dicts"}) == []
    assert extract_inbound_messages("not-even-a-dict") == []  # type: ignore[arg-type]
    assert extract_inbound_messages(None) == []  # type: ignore[arg-type]


def test_extract_inbound_messages_drops_message_without_from_or_text() -> None:
    payload = {"entry": [{"changes": [{"value": {"messages": [{"type": "text", "text": {"body": "oi"}}]}}]}]}
    assert extract_inbound_messages(payload) == []


# ---------------------------------------------------------------------------
# _ScopedWhatsAppSender — hash mismatch fails closed, never sends to an unverified destination
# ---------------------------------------------------------------------------


async def test_scoped_sender_sends_to_raw_number_using_hash_as_key() -> None:
    client = _FakeWhatsAppClient()
    sender = _ScopedWhatsAppSender(raw_to="5511999999999", expected_hash="abc123", client=client)  # type: ignore[arg-type]

    await sender.send("abc123", "ola beneficiario")

    assert client.sent == [("5511999999999", "ola beneficiario")]


async def test_scoped_sender_refuses_hash_mismatch() -> None:
    client = _FakeWhatsAppClient()
    sender = _ScopedWhatsAppSender(raw_to="5511999999999", expected_hash="abc123", client=client)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="hash mismatch"):
        await sender.send("wrong-hash", "text")
    assert client.sent == []


# ---------------------------------------------------------------------------
# HelenaDispatcher — one full turn, pseudonymized state, hash-of-a-hash beneficiario_pseudo_id
# ---------------------------------------------------------------------------


async def test_dispatcher_derives_conversation_id_and_pseudo_id_never_raw_phone() -> None:
    dmn = FakeDmnTransport()
    dmn.register("triage_redflag_adult", [{"red_flag": False, "conduta": "CONTINUE"}])
    whatsapp_client = _FakeWhatsAppClient()
    dispatcher = HelenaDispatcher(
        tenant_id="amh",
        inference=_FakeInference(
            ['{"intent": "information", "population": "none", "psychosocial_risk": false}', "resposta"]
        ),
        dmn=dmn,
        cibseven=FakeCibSevenTransport(),
        whatsapp_client=whatsapp_client,  # type: ignore[arg-type]
        pseudonymizer=Pseudonymizer(),
    )

    result = await dispatcher.dispatch(
        InboundMessage(from_number="5511999999999", text="oi", message_id="wamid.1")
    )

    assert result["conversation_id"].startswith("wa:amh:")
    phone_hash = result["conversation_id"].split(":", 2)[2]
    assert phone_hash != "5511999999999"  # never the raw number
    assert result["beneficiario_pseudo_id"] != phone_hash  # hash-of-a-hash, not identical to it
    assert whatsapp_client.sent, "Helena must reply over WhatsApp using the resolved raw number"
    assert whatsapp_client.sent[0][0] == "5511999999999"


async def test_dispatcher_red_flag_message_starts_escalation() -> None:
    dmn = FakeDmnTransport()
    dmn.register(
        "triage_redflag_adult",
        [{"red_flag": True, "prioridade": "P1", "conduta": "ESCALATE_EMERGENCY", "motivo": "dor toracica"}],
    )
    cibseven = FakeCibSevenTransport()
    dispatcher = HelenaDispatcher(
        tenant_id="amh",
        inference=_FakeInference(
            [
                '{"intent": "symptom", "population": "adult", "sintoma_codigo": "dor_toracica", '
                '"intensidade": "grave", "psychosocial_risk": false}',
                "resumo",
                "um humano vai continuar",
            ]
        ),
        dmn=dmn,
        cibseven=cibseven,
        whatsapp_client=_FakeWhatsAppClient(),  # type: ignore[arg-type]
        pseudonymizer=Pseudonymizer(),
    )

    result = await dispatcher.dispatch(
        InboundMessage(from_number="5511988887777", text="dor forte no peito", message_id="wamid.2")
    )

    assert result["escalation_started"] is True
    assert result["escalation_business_key"].startswith("ESC-amh-wa:amh:")


# ---------------------------------------------------------------------------
# Input-boundary gate (T1.11 layer 2) — the state entering Helena's graph from the dispatch
# seam carries ONLY the declared INPUT fields; no caller-planted output field can reach it.
# ---------------------------------------------------------------------------


async def test_dispatch_constructs_state_with_only_input_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """The dispatcher assembles state via the typed `new_helena_state` constructor, so the state
    handed to the compiled graph contains EXACTLY `HELENA_INPUT_FIELDS` — never an output-only
    key (`next_kind`/`error`/`escalation_*`/`dmn_decision_ref`). We capture the exact initial
    state by intercepting the graph the dispatcher builds."""
    captured: dict[str, Any] = {}

    class _RecordingCompiled:
        async def ainvoke(self, state: dict[str, Any]) -> dict[str, Any]:
            captured["state"] = dict(state)
            return {"next_kind": "inform"}

    class _RecordingGraph:
        def compile(self) -> _RecordingCompiled:
            return _RecordingCompiled()

    monkeypatch.setattr(dispatch_module, "build", lambda _config: _RecordingGraph())

    dispatcher = HelenaDispatcher(
        tenant_id="amh",
        inference=_FakeInference([]),
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        whatsapp_client=_FakeWhatsAppClient(),  # type: ignore[arg-type]
        pseudonymizer=Pseudonymizer(),
    )

    await dispatcher.dispatch(InboundMessage(from_number="5511999999999", text="ola", message_id="wamid.1"))

    assert frozenset(captured["state"]) == HELENA_INPUT_FIELDS
    for output_only in ("next_kind", "error", "escalation_motivo", "escalation_started", "dmn_decision_ref"):
        assert output_only not in captured["state"]
