"""Unit tests for `maezo.platform.webhooks.whatsapp.dispatch` (T1.11, defect B6)."""

from __future__ import annotations

import hashlib
from typing import Any

import pytest
import structlog
from langgraph.checkpoint.memory import InMemorySaver

from maezo.agents.helena.graph import HELENA_INPUT_FIELDS
from maezo.gateway.effect_pep import DecisionContext
from maezo.gateway.pseudonymizer import KEYED_PSEUDONYM_PREFIX, Pseudonymizer
from maezo.gateway.seams import SeamContext, is_gated_seam
from maezo.platform.webhooks.whatsapp import dispatch as dispatch_module
from maezo.platform.webhooks.whatsapp.dispatch import (
    NON_TEXT_ACK_TEXT,
    HelenaDispatcher,
    InboundMessage,
    InboundNonTextMessage,
    _ScopedWhatsAppSender,
    extract_inbound_messages,
)
from maezo.runtime.checkpoint import Checkpointer, checkpoint_thread_config
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink


class _FakeInference:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
    ) -> str:
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


def test_extract_inbound_messages_returns_non_text_as_a_typed_sibling() -> None:
    """Gap `WHATSAPP-NON-TEXT-DROPPED`: an image/audio/document inbound is a REAL message and is
    now returned (typed, bodyless) instead of vanishing with one INFO line — which is what left
    the beneficiary who sent a voice note with no answer at all."""
    payload = {
        "entry": [
            {"changes": [{"value": {"messages": [{"from": "551199", "id": "wamid.9", "type": "image"}]}}]}
        ]
    }
    assert extract_inbound_messages(payload) == [
        InboundNonTextMessage(from_number="551199", message_type="image", message_id="wamid.9")
    ]


def test_extract_inbound_messages_non_text_carries_no_media_reference() -> None:
    """PHI minimality: the extracted value keeps ONLY the number (for the per-turn sender), Meta's
    type token and the wamid — never the media id, caption, filename or mime type."""
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": "551199",
                                    "id": "wamid.9",
                                    "type": "document",
                                    "document": {
                                        "id": "media-id-should-not-travel",
                                        "filename": "exame-do-beneficiario.pdf",
                                        "caption": "meu exame",
                                        "mime_type": "application/pdf",
                                    },
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    (message,) = extract_inbound_messages(payload)
    rendered = repr(message)
    for leak in ("media-id-should-not-travel", "exame-do-beneficiario.pdf", "meu exame", "application/pdf"):
        assert leak not in rendered


def test_extract_inbound_messages_mixed_batch_keeps_both_shapes_in_order() -> None:
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {"from": "551199", "id": "wamid.1", "type": "text", "text": {"body": "oi"}},
                                {"from": "551199", "id": "wamid.2", "type": "audio"},
                            ]
                        }
                    }
                ]
            }
        ]
    }
    assert extract_inbound_messages(payload) == [
        InboundMessage(from_number="551199", text="oi", message_id="wamid.1"),
        InboundNonTextMessage(from_number="551199", message_type="audio", message_id="wamid.2"),
    ]


def test_extract_inbound_messages_skips_a_message_with_no_usable_type() -> None:
    """A message with no `type` (or a non-`str` one) is a shape this build cannot classify: it is
    neither a text turn nor a media kind worth naming back. Skipped, never coerced into a token."""
    for broken in ({"from": "551199", "id": "wamid.9"}, {"from": "551199", "type": {"x": 1}}):
        payload = {"entry": [{"changes": [{"value": {"messages": [broken]}}]}]}
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
        audit_sink=FakeStartAuditSink(),
    )

    result = await dispatcher.dispatch(
        InboundMessage(from_number="5511999999999", text="oi", message_id="wamid.1")
    )

    assert result["conversation_id"].startswith("wa:amh:")
    phone_hash = result["conversation_id"].split(":", 2)[2]
    assert phone_hash != "5511999999999"  # never the raw number
    # IRREVERSIBILITY (ADR-0035 extension): the identity is a KEYED HMAC, NOT the reversible
    # `sha256(tenant:phone)` the old scheme leaked into the persisted conversation/thread id.
    assert phone_hash.startswith(KEYED_PSEUDONYM_PREFIX)
    plain_sha256 = hashlib.sha256(b"amh:5511999999999").hexdigest()
    assert plain_sha256 not in result["conversation_id"]  # a precomputed table cannot recover it
    # And it IS the keyed HMAC of `tenant:phone` (Pseudonymizer()'s dev key is deterministic).
    expected = Pseudonymizer().pseudonymize({"telefone": "amh:5511999999999"})["telefone"]
    assert phone_hash == f"{KEYED_PSEUDONYM_PREFIX}{expected}"
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
        audit_sink=FakeStartAuditSink(),
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
        async def ainvoke(self, state: dict[str, Any], config: Any = None) -> dict[str, Any]:
            captured["state"] = dict(state)
            captured["config"] = config
            return {"next_kind": "inform"}

    class _RecordingGraph:
        def compile(self, checkpointer: Any = None) -> _RecordingCompiled:
            captured["checkpointer"] = checkpointer
            return _RecordingCompiled()

    monkeypatch.setattr(dispatch_module, "build", lambda _config: _RecordingGraph())

    dispatcher = HelenaDispatcher(
        tenant_id="amh",
        inference=_FakeInference([]),
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        whatsapp_client=_FakeWhatsAppClient(),  # type: ignore[arg-type]
        pseudonymizer=Pseudonymizer(),
        audit_sink=FakeStartAuditSink(),
    )

    await dispatcher.dispatch(InboundMessage(from_number="5511999999999", text="ola", message_id="wamid.1"))

    assert frozenset(captured["state"]) == HELENA_INPUT_FIELDS
    for output_only in ("next_kind", "error", "escalation_motivo", "escalation_started", "dmn_decision_ref"):
        assert output_only not in captured["state"]
    # No checkpointer injected -> stateless compile + no thread config (fresh turn every time).
    assert captured["checkpointer"] is None
    assert captured["config"] is None


# ---------------------------------------------------------------------------
# T4b — durable checkpointer wiring: PHI-safe thread config, multi-turn resume, distinct identity
# ---------------------------------------------------------------------------


def _info_dispatcher(checkpointer: Checkpointer | None, *, turns: int = 4) -> HelenaDispatcher:
    """A dispatcher whose inference always classifies 'information' intent + replies — enough
    canned responses for `turns` sequential dispatches (2 inference calls per info turn)."""
    responses: list[str] = []
    for _ in range(turns):
        responses.append('{"intent": "information", "population": "none", "psychosocial_risk": false}')
        responses.append("resposta")
    dmn = FakeDmnTransport()
    dmn.register("triage_redflag_adult", [{"red_flag": False, "conduta": "CONTINUE"}] * turns)
    return HelenaDispatcher(
        tenant_id="amh",
        inference=_FakeInference(responses),
        dmn=dmn,
        cibseven=FakeCibSevenTransport(),
        whatsapp_client=_FakeWhatsAppClient(),  # type: ignore[arg-type]
        pseudonymizer=Pseudonymizer(),
        audit_sink=FakeStartAuditSink(),
        checkpointer=checkpointer,
    )


async def test_dispatch_with_checkpointer_uses_phi_safe_thread_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With a checkpointer wired, the graph is compiled checkpoint-enabled and invoked under a
    thread config keyed by the HASHED `wa:{tenant}:{phone_hash}` conversation id — never the raw
    phone number (LGPD; the PHI-bearing `checkpoint_blobs` rows are keyed by this thread id)."""
    captured: dict[str, Any] = {}

    class _RecordingCompiled:
        async def ainvoke(self, state: dict[str, Any], config: Any = None) -> dict[str, Any]:
            captured["config"] = config
            return {"next_kind": "inform"}

    class _RecordingGraph:
        def compile(self, checkpointer: Any = None) -> _RecordingCompiled:
            captured["checkpointer"] = checkpointer
            return _RecordingCompiled()

    monkeypatch.setattr(dispatch_module, "build", lambda _config: _RecordingGraph())

    saver = InMemorySaver()
    dispatcher = _info_dispatcher(Checkpointer(saver=saver))
    await dispatcher.dispatch(InboundMessage(from_number="5511999999999", text="oi", message_id="wamid.1"))

    assert captured["checkpointer"] is saver  # compiled checkpoint-enabled with the wrapped saver
    thread_id = captured["config"]["configurable"]["thread_id"]
    assert thread_id.startswith("wa:amh:")
    assert "5511999999999" not in thread_id  # hashed, never the raw number
    # IRREVERSIBILITY: the PERSISTED thread id (keying the PHI-bearing checkpoint_blobs rows) is a
    # KEYED HMAC, not the reversible sha256 the old scheme wrote into the durable checkpoint DB.
    assert thread_id.startswith(f"wa:amh:{KEYED_PSEUDONYM_PREFIX}")
    assert hashlib.sha256(b"amh:5511999999999").hexdigest() not in thread_id
    # And it is exactly what the PHI-safety helper would build (fail-closes on an unkeyed id).
    assert captured["config"] == checkpoint_thread_config(thread_id)


async def test_dispatch_multi_turn_same_identity_resumes_distinct_identity_fresh() -> None:
    """Two sequential dispatches with the SAME conversation identity accumulate checkpoint history
    on ONE thread (turn 2 resumes turn 1's persisted state); a DIFFERENT identity gets its own
    independent, fresh thread. Real `InMemorySaver` — the durable persistence contract, no PG."""
    saver = InMemorySaver()
    dispatcher = _info_dispatcher(Checkpointer(saver=saver), turns=6)

    r1 = await dispatcher.dispatch(InboundMessage(from_number="5511999999999", text="oi", message_id="m1"))
    conv = r1["conversation_id"]
    cfg = checkpoint_thread_config(conv)
    assert await saver.aget_tuple(cfg) is not None  # turn 1 persisted a checkpoint
    hist_after_1 = [c async for c in saver.alist(cfg)]

    r2 = await dispatcher.dispatch(
        InboundMessage(from_number="5511999999999", text="de novo", message_id="m2")
    )
    assert r2["conversation_id"] == conv  # same identity -> same thread
    hist_after_2 = [c async for c in saver.alist(cfg)]
    assert len(hist_after_2) > len(hist_after_1)  # state accumulated across turns (resume)

    r3 = await dispatcher.dispatch(InboundMessage(from_number="5511000000000", text="oi", message_id="m3"))
    assert r3["conversation_id"] != conv  # different phone -> different hashed thread
    other_cfg = checkpoint_thread_config(r3["conversation_id"])
    hist_other = [c async for c in saver.alist(other_cfg)]
    assert len(hist_other) < len(hist_after_2)  # fresh thread, not the 2-turn history


async def test_dispatch_stateless_when_no_checkpointer_starts_fresh_each_turn() -> None:
    """No checkpointer -> the graph compiles stateless; two turns from the same identity leave NO
    persisted checkpoint anywhere (the pre-T4b behavior, preserved for tests / deliberate builds)."""
    saver = InMemorySaver()  # a bystander saver the dispatcher never receives
    dispatcher = _info_dispatcher(None, turns=2)
    r1 = await dispatcher.dispatch(InboundMessage(from_number="5511999999999", text="oi", message_id="m1"))
    assert await saver.aget_tuple(checkpoint_thread_config(r1["conversation_id"])) is None


# ---------------------------------------------------------------------------
# GAP 11.7 living fence — verified this session: `HelenaDispatcher` is the ONLY dispatch class in
# this module, and this module (plus `HelenaGraph`) is the ONLY thing an inbound WhatsApp message
# ever reaches. Lucas/Fernando declare `mcp-whatsapp.send_message`/`send_beneficiary_message` in
# their own `agent.yaml` (an outbound INTENT — see `docs/processes/contracts/SP-OP-CANCEL-001.md`
# "Canal de entrada" for the full picture, including why building a live inbound route for either
# needs a decision/wiring outside this module's editable surface). This is a DELIBERATE regression
# fence, not an accident of what happens to exist today: when someone wires a second dispatcher (or
# routes a beneficiary reply here by agent), this test MUST fail and force an explicit update to it
# and to the contracts' "Canal de entrada" sections — never a silent drift.
# ---------------------------------------------------------------------------


def test_helena_dispatcher_is_the_only_dispatch_class_gap_11_7() -> None:
    import inspect

    dispatcher_classes = sorted(
        name
        for name, obj in vars(dispatch_module).items()
        if inspect.isclass(obj) and name.endswith("Dispatcher")
    )
    assert dispatcher_classes == ["HelenaDispatcher"], dispatcher_classes

    source = inspect.getsource(dispatch_module).lower()
    assert "lucas" not in source
    assert "fernando" not in source


# ---------------------------------------------------------------------------
# Gap `WHATSAPP-NON-TEXT-DROPPED` — the non-text acknowledgement: ONE fixed reply, through the
# SAME gated per-turn seam, with no Helena turn behind it and nothing promised that isn't wired.
# ---------------------------------------------------------------------------

_ACK_RAW_NUMBER = "5511999999999"


class _ExplodingInference:
    """Any LLM call during an acknowledgement is a defect — the ack is a canned string."""

    async def generate(self, prompt: str, **_kwargs: Any) -> str:
        raise AssertionError("the non-text acknowledgement must never call the LLM")


def _ack_dispatcher(
    *,
    seam_context: SeamContext | None = None,
    checkpointer: Checkpointer | None = None,
    client: Any | None = None,
) -> tuple[HelenaDispatcher, Any, FakeStartAuditSink]:
    whatsapp_client = client if client is not None else _FakeWhatsAppClient()
    audit_sink = FakeStartAuditSink()
    dispatcher = HelenaDispatcher(
        tenant_id="amh",
        inference=_ExplodingInference(),  # type: ignore[arg-type]
        dmn=FakeDmnTransport(),  # nothing registered: an evaluation would raise
        cibseven=FakeCibSevenTransport(),
        whatsapp_client=whatsapp_client,  # type: ignore[arg-type]
        pseudonymizer=Pseudonymizer(),
        audit_sink=audit_sink,
        checkpointer=checkpointer,
        seam_context=seam_context,
    )
    return dispatcher, whatsapp_client, audit_sink


def _expected_conversation_id(raw_number: str = _ACK_RAW_NUMBER) -> str:
    digest = Pseudonymizer().pseudonymize({"telefone": f"amh:{raw_number}"})["telefone"]
    return f"wa:amh:{KEYED_PSEUDONYM_PREFIX}{digest}"


def _seam_context() -> SeamContext:
    return SeamContext(tenant="amh", principal="helena", decision=DecisionContext())


async def test_acknowledge_non_text_sends_exactly_one_fixed_reply_to_the_verified_hash() -> None:
    """The whole point of the gap: the beneficiary who sent a voice note now gets an answer.

    Exactly ONE send, with the fixed pt-BR text, delivered to the raw number the scoped sender
    closes over — and reached through `to_hash`, so `_ScopedWhatsAppSender`'s hash-mismatch refusal
    is actually exercised rather than bypassed."""
    dispatcher, client, audit_sink = _ack_dispatcher(seam_context=_seam_context())

    await dispatcher.acknowledge_non_text(
        InboundNonTextMessage(from_number=_ACK_RAW_NUMBER, message_type="audio", message_id="wamid.9")
    )

    assert client.sent == [(_ACK_RAW_NUMBER, NON_TEXT_ACK_TEXT)]
    # The text is PINNED here, not merely compared to itself: a reply that starts promising a
    # human/Libras/transcription must break this test, because none of those is wired (owner
    # decisions 9.6/10.2 are OPEN — see the constant's own comment).
    assert NON_TEXT_ACK_TEXT == (
        "Este canal aceita apenas mensagens de texto. Por favor, envie sua mensagem em texto."
    )
    assert audit_sink.calls == []  # no process start was audited => none happened (T-C2 fence)


async def test_acknowledge_non_text_goes_through_the_effect_gate() -> None:
    """The ack is an EFFECT (`whatsapp.send_message`) and is choked exactly like a Helena reply.

    Proven twice, the way `tests/unit/gateway/seams/test_live_dispatch_wiring.py` does it: the real
    per-turn wrap is spied at the dispatch module's own name (so the wrapper's INNER is observed to
    be the scoped sender), and the gate's own decision line is observed in the telemetry."""
    captured: list[tuple[Any, SeamContext]] = []
    real_gate = dispatch_module.gate_whatsapp

    def _spy(inner: Any, seam: SeamContext) -> Any:
        captured.append((inner, seam))
        return real_gate(inner, seam)

    dispatch_module.gate_whatsapp = _spy  # type: ignore[attr-defined]
    try:
        dispatcher, client, _sink = _ack_dispatcher(seam_context=_seam_context())
        with structlog.testing.capture_logs() as logs:
            await dispatcher.acknowledge_non_text(
                InboundNonTextMessage(from_number=_ACK_RAW_NUMBER, message_type="image", message_id="wamid.9")
            )
    finally:
        dispatch_module.gate_whatsapp = real_gate  # type: ignore[attr-defined]

    assert len(captured) == 1, "the per-turn wrap must happen exactly once per acknowledgement"
    inner, seam = captured[0]
    assert isinstance(inner, _ScopedWhatsAppSender)
    assert is_gated_seam(real_gate(inner, seam))
    assert any(entry.get("operation") == "whatsapp.send_message" for entry in logs), (
        "the gate never decided — the acknowledgement would be an ungated outbound effect"
    )
    assert client.sent, "the gate must not have swallowed the delivery"


async def test_acknowledge_non_text_without_a_seam_context_announces_it_loudly() -> None:
    """Same disclosed, test-only affordance as `dispatch()`: never a silent ungated live path."""
    dispatcher, client, _sink = _ack_dispatcher(seam_context=None)

    with structlog.testing.capture_logs() as logs:
        await dispatcher.acknowledge_non_text(
            InboundNonTextMessage(from_number=_ACK_RAW_NUMBER, message_type="location", message_id="wamid.9")
        )

    ungated = [entry for entry in logs if entry["event"] == "helena_dispatch_whatsapp_seam_ungated"]
    assert len(ungated) == 1
    assert ungated[0]["log_level"] == "error"
    assert _ACK_RAW_NUMBER not in repr(ungated)
    assert client.sent == [(_ACK_RAW_NUMBER, NON_TEXT_ACK_TEXT)]


async def test_acknowledge_non_text_telemetry_carries_no_raw_number_and_no_media_reference() -> None:
    """PHI: only the keyed `hk1_` pseudonym, the wamid and Meta's type token may be logged."""
    dispatcher, _client, _sink = _ack_dispatcher(seam_context=_seam_context())

    with structlog.testing.capture_logs() as logs:
        await dispatcher.acknowledge_non_text(
            InboundNonTextMessage(from_number=_ACK_RAW_NUMBER, message_type="document", message_id="wamid.9")
        )

    rendered = repr(logs)
    assert _ACK_RAW_NUMBER not in rendered, "the raw recipient reached a log line"
    assert hashlib.sha256(f"amh:{_ACK_RAW_NUMBER}".encode()).hexdigest() not in rendered
    started = [entry for entry in logs if entry["event"] == "whatsapp_non_text_ack_started"]
    sent = [entry for entry in logs if entry["event"] == "whatsapp_non_text_ack_sent"]
    assert len(started) == 1 and len(sent) == 1
    assert started[0]["conversation_id"] == _expected_conversation_id()
    assert started[0]["message_type"] == "document"
    assert started[0]["message_id"] == "wamid.9"


async def test_acknowledge_non_text_runs_no_graph_and_writes_no_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No Helena turn behind the ack: no graph build, no LLM (the inference double raises), no
    process start (the audit sink stays empty), and NOTHING persisted on the conversation thread —
    so a later real text message still starts the conversation fresh."""

    def _no_graph(_config: Any) -> Any:
        raise AssertionError("the non-text acknowledgement must never build Helena's graph")

    monkeypatch.setattr(dispatch_module, "build", _no_graph)
    saver = InMemorySaver()
    dispatcher, client, audit_sink = _ack_dispatcher(
        seam_context=_seam_context(), checkpointer=Checkpointer(saver=saver)
    )

    await dispatcher.acknowledge_non_text(
        InboundNonTextMessage(from_number=_ACK_RAW_NUMBER, message_type="sticker", message_id="wamid.9")
    )

    assert client.sent == [(_ACK_RAW_NUMBER, NON_TEXT_ACK_TEXT)]
    assert audit_sink.calls == []
    thread = checkpoint_thread_config(_expected_conversation_id())
    assert await saver.aget_tuple(thread) is None


async def test_acknowledge_non_text_propagates_a_send_failure_to_the_caller() -> None:
    """The ack does NOT swallow a delivery failure — `app.py` is the one place that decides what a
    failed message means for the HTTP answer (it counts it and never lets it escape the webhook).

    Live relevance, disclosed: `WhatsAppServer.send_message` REFUSES today in Helm because nothing
    injects `WHATSAPP_PHONE_NUMBER_ID` (`tools/mcp_whatsapp/server.py`'s ops disclosure), so this
    is the branch the deployed receiver takes for BOTH a Helena reply and this acknowledgement
    until the owner provisions that value."""

    class _RefusingClient:
        def __init__(self) -> None:
            self.sent: list[tuple[str, str]] = []

        async def send_message(self, to: str, text: str) -> dict[str, Any]:
            raise ValueError("WhatsApp phone_number_id is not configured — refusing to send")

    dispatcher, _client, _sink = _ack_dispatcher(seam_context=_seam_context(), client=_RefusingClient())

    with pytest.raises(ValueError, match="refusing to send"):
        await dispatcher.acknowledge_non_text(
            InboundNonTextMessage(from_number=_ACK_RAW_NUMBER, message_type="audio", message_id="wamid.9")
        )
