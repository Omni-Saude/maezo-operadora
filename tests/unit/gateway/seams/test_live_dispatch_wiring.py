"""THE LIVE AGENT PATH — the four claims `tool_registry`'s O4 makes, proven (design §5.5).

This is the path counterexample C-A1 says the whole design turns on: the health-only agent-runtime
daemon never executes a turn (`agent_graph_execution_not_performed_here`), while the WhatsApp
webhook receiver builds its own deps and calls `helena.graph.build({...})` directly
(`platform/webhooks/whatsapp/dispatch.py:161`). Gating `_build_tool_deps` and stopping there would
gate exactly the traffic that does not exist.

It is also where the per-request knot lives, and O4's answer — split the lifetimes: build the
`DecisionContext` ONCE at bring-up, re-wrap the per-turn sender around it — is only worth anything
if all four of its proof obligations hold:

  1. the raw recipient never leaves `_ScopedWhatsAppSender` (I-3);
  2. a turn builds NO new decision context, so the gate costs one allocation (I-9);
  3. the principal stays closure-bound, never a call argument (A-8);
  4. the production composition root ALWAYS supplies the context, so the ungated branch is a
     test-only affordance and not a live hole (I-11).
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest
import structlog

from maezo.gateway.effect_pep import DecisionContext
from maezo.gateway.pseudonymizer import Pseudonymizer
from maezo.gateway.seams import SeamContext, is_gated_seam
from maezo.gateway.seams.whatsapp import GatedWhatsAppSender
from maezo.platform.webhooks.whatsapp.dispatch import (
    HelenaDispatcher,
    InboundMessage,
    _ScopedWhatsAppSender,
)
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

pytestmark = pytest.mark.anyio

_RAW_NUMBER = "5511987654321"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _FakeWhatsAppClient:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_message(self, to: str, text: str) -> dict[str, Any]:
        self.sent.append((to, text))
        return {"messages": [{"id": "wamid.fake"}]}


class _FakeInference:
    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)

    async def generate(self, prompt: str, **_kwargs: Any) -> str:
        return self._replies.pop(0) if self._replies else "fallback"


def _seam_context() -> SeamContext:
    return SeamContext(tenant="amh", principal="helena", decision=DecisionContext())


def _dispatcher(*, seam_context: SeamContext | None) -> tuple[HelenaDispatcher, _FakeWhatsAppClient]:
    client = _FakeWhatsAppClient()
    dispatcher = HelenaDispatcher(
        tenant_id="amh",
        inference=_FakeInference(
            ['{"intent": "information", "population": "none", "psychosocial_risk": false}', "resposta"]
        ),  # type: ignore[arg-type]
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        whatsapp_client=client,  # type: ignore[arg-type]
        pseudonymizer=Pseudonymizer(),
        audit_sink=FakeStartAuditSink(),
        seam_context=seam_context,
    )
    return dispatcher, client


# --- (1) + (2): the per-turn wrap, and what it costs -------------------------------------------


async def test_the_live_turn_wraps_its_scoped_sender_in_a_gated_seam() -> None:
    """O4, observed end to end: the object Helena's graph gets IS gated, and the inner is scoped.

    Captured by patching `gate_whatsapp` at the dispatch module's own name so the real per-turn
    construction runs and its arguments can be inspected — a mock of the dispatcher would prove
    nothing about the wiring under test.
    """
    import maezo.platform.webhooks.whatsapp.dispatch as dispatch_mod

    captured: list[tuple[Any, SeamContext]] = []
    real_gate = dispatch_mod.gate_whatsapp  # type: ignore[attr-defined]

    def _spy(inner: Any, seam: SeamContext) -> Any:
        captured.append((inner, seam))
        return real_gate(inner, seam)

    monkeypatched = "gate_whatsapp"
    setattr(dispatch_mod, monkeypatched, _spy)
    try:
        dispatcher, client = _dispatcher(seam_context=_seam_context())
        await dispatcher.dispatch(InboundMessage(from_number=_RAW_NUMBER, text="oi", message_id="m1"))
    finally:
        setattr(dispatch_mod, monkeypatched, real_gate)

    assert len(captured) == 1, "the per-turn wrap must happen exactly once per turn"
    inner, seam = captured[0]
    assert isinstance(inner, _ScopedWhatsAppSender), (
        "the wrapper's INNER must be the per-turn scoped sender — that is what keeps the raw "
        "recipient out of the gate (I-3)"
    )
    assert is_gated_seam(real_gate(inner, seam))
    assert seam.principal == "helena"
    # The turn still delivered to the REAL number through the scoped sender (parity: I-7).
    assert client.sent and client.sent[0][0] == _RAW_NUMBER


async def test_live_dispatch_wrapping_never_exposes_the_raw_recipient() -> None:
    """(1) I-3 on the one path that HAS a raw phone number in hand.

    `_ScopedWhatsAppSender` holds it for exactly this turn (`dispatch.py:100-118`). Since it is
    now the wrapper's inner, the gate sees strictly LESS than the pre-Onda-1 code did — assert
    that against every telemetry line the turn emits, not just against the value object.
    """
    dispatcher, _client = _dispatcher(seam_context=_seam_context())
    with structlog.testing.capture_logs() as logs:
        await dispatcher.dispatch(InboundMessage(from_number=_RAW_NUMBER, text="oi", message_id="m1"))

    rendered = repr(logs)
    assert _RAW_NUMBER not in rendered, "the raw recipient reached a log line"
    assert "oi" not in {entry.get("operation") for entry in logs}
    # And the gate DID run — otherwise the absence above would prove nothing.
    assert any(entry.get("operation") == "whatsapp.send_message" for entry in logs)


async def test_per_turn_wrapping_builds_no_decision_context() -> None:
    """(2) I-9: the expensive half is built ONCE, at bring-up — never per message.

    A per-turn `build_pep` would read `L0-core.yaml`, `_hard_frozen.yaml` and the tenant overlay
    from disk on every inbound WhatsApp message, on a path that holds the HTTP response open
    synchronously. That is option O1, and this is the test that rejects it.
    """
    import maezo.gateway.tool_registry as registry_mod

    calls: list[str] = []
    real = registry_mod.build_agent_seam_context

    def _spy(**kwargs: Any) -> SeamContext:
        calls.append(str(kwargs.get("agent_id")))
        return real(**kwargs)

    registry_mod.build_agent_seam_context = _spy
    try:
        dispatcher, _client = _dispatcher(seam_context=_seam_context())
        for index in range(3):
            await dispatcher.dispatch(
                InboundMessage(from_number=_RAW_NUMBER, text="oi", message_id=f"m{index}")
            )
    finally:
        registry_mod.build_agent_seam_context = real

    assert calls == [], "a turn built a DecisionContext — the two lifetimes are not split"


# --- (3): the principal is closure-bound ---------------------------------------------------------


def test_the_gated_sender_takes_no_principal_from_its_caller() -> None:
    """(3) A-8. `send(to_hash, text)` — there is nowhere for a forged principal to enter."""
    params = set(inspect.signature(GatedWhatsAppSender.send).parameters)
    assert params == {"self", "to_hash", "text"}


# --- (4): the production root always supplies the context ---------------------------------------


def test_production_webhook_root_always_supplies_a_seam_context() -> None:
    """(4) I-11. The ungated branch in `dispatch()` must be unreachable from the real root.

    `seam_context` is `SeamContext | None` only so the ~dozen unit tests that build a dispatcher
    directly keep working. Asserted on the SOURCE rather than by running `_build_dispatcher`
    (which needs a DSN and constructs real transports): the root must pass the keyword, and the
    value must come from the registry.
    """
    import ast

    src = Path(inspect.getsourcefile(_build_dispatcher_module())).read_text(encoding="utf-8")  # type: ignore[arg-type]
    tree = ast.parse(src)
    fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_build_dispatcher"
    )
    ctor = next(
        node
        for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "HelenaDispatcher"
    )
    passed = {kw.arg for kw in ctor.keywords}
    assert "seam_context" in passed, (
        "the production webhook root does not pass `seam_context` — the live WhatsApp seam would "
        "run UNGATED, which is the one thing this leg exists to prevent"
    )
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "build_agent_seam_context"
        for node in ast.walk(fn)
    ), "the root must take the context from the registry, not build one by hand"


def _build_dispatcher_module() -> Any:
    import maezo.platform.webhooks.service as svc

    return svc


async def test_a_dispatcher_without_a_seam_context_announces_it_loudly() -> None:
    """The disclosed residual, made loud rather than silent.

    A `None` context means the turn's WhatsApp seam is not choked. That must never be quiet: the
    error line is what distinguishes a test fixture from a deployed wiring defect.
    """
    dispatcher, _client = _dispatcher(seam_context=None)
    with structlog.testing.capture_logs() as logs:
        await dispatcher.dispatch(InboundMessage(from_number=_RAW_NUMBER, text="oi", message_id="m1"))

    ungated = [entry for entry in logs if entry["event"] == "helena_dispatch_whatsapp_seam_ungated"]
    assert len(ungated) == 1
    assert ungated[0]["log_level"] == "error"
    assert _RAW_NUMBER not in repr(ungated), "even the ungated warning must not carry the recipient"
