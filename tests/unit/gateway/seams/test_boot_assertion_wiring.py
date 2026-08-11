"""§5.5's boot assertion, proved at the CONSEQUENCE level at each of the four wiring roots.

`tool_registry.effect_seams_gated` — the primitive — is already well proved by
`test_seam_proofs.py::test_the_boot_assertion_goes_red_when_a_raw_seam_is_smuggled_in`: it is an
`isinstance` check against a class this package owns, it is non-vacuous, and it cannot be forged
by an attribute. None of that says the primitive is WIRED, and a boot assertion nobody calls is
worth exactly nothing.

That gap was real, not theoretical: four mutants that neuter the CONSEQUENCE of a failed
assertion — not the assertion itself — survived the whole suite.

  (c) `webhooks/service.py`      `if not gated: state.dispatcher = None`  ->  `if False:`
  (d) `notifications_bridge.py`  `if not gated: raise RuntimeError`       ->  neutered
  (a) `agent_runtime/service.py` the check DROPPED from the readiness set
  (e) `worker_runtime/service.py` the check DROPPED from the readiness set

Each survivor is a different shape of the same defect: the daemon still COMPUTES gatedness,
still logs it, and then serves traffic anyway. This file asserts what each root DOES about a
failed assertion, which is the only part a deployment can feel.

Root (b) (`a2a_composition`) is not here: it has no consequence of its own — it hands its three
constructions to the registry and the gatedness of the result is asserted by the registry's own
proofs plus `test_a2a_composition.py`.
"""

from __future__ import annotations

from typing import Any

import pytest
import structlog

from maezo.gateway.seams import SeamContext
from maezo.gateway.seams.cibseven import gate_cibseven
from maezo.gateway.seams.dmn import gate_dmn
from maezo.gateway.seams.inference import gate_inference
from maezo.gateway.tool_registry import effect_seams_gated
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _seam(principal: str = "helena") -> SeamContext:
    """A real context from the ONE sanctioned constructor — never a hand-rolled stub.

    What is gated must be gated the way production gates it; a locally-built `SeamContext` would
    make these proofs about this file rather than about the roots.
    """
    from maezo.gateway.tool_registry import build_agent_seam_context

    return build_agent_seam_context(tenant="amh", agent_id=principal)


# =================================================================================================
# (c) webhooks/service.py — THE LIVE PATH. An ungated seam DROPS the dispatcher (=> 501).
# =================================================================================================


def _webhook_settings() -> Any:
    from maezo.platform.webhooks.whatsapp.settings import WhatsAppWebhookSettings

    return WhatsAppWebhookSettings(
        WHATSAPP_APP_SECRET="s3cret",
        WHATSAPP_VERIFY_TOKEN="verify",
        DATABASE_URL="postgresql://u:p@localhost:5432/db",
    )


class _StubDispatcher:
    """Just the three attributes the boot assertion reads off a built dispatcher."""

    def __init__(self, *, dmn: Any, cibseven: Any, inference: Any) -> None:
        self.dmn = dmn
        self.cibseven = cibseven
        self.inference = inference


class _RawInference:
    """A raw provider — NOT a `GatedSeam`. The smuggling vector."""

    async def generate(self, prompt: str, **_kwargs: Any) -> str:
        return "ok"


async def _run_webhook_bringup(monkeypatch: pytest.MonkeyPatch, *, smuggle: bool) -> Any:
    """Drive the REAL `_bring_up_dependencies` with a dispatcher we control the gatedness of."""
    import maezo.platform.webhooks.service as svc

    seam = _seam()
    dispatcher = _StubDispatcher(
        dmn=gate_dmn(FakeDmnTransport(), seam),
        cibseven=gate_cibseven(FakeCibSevenTransport(), seam),
        # The ONE variable between the two runs: a raw provider where a gated one belongs.
        inference=_RawInference() if smuggle else gate_inference(_RawInference(), seam),
    )
    monkeypatch.setattr(svc, "_build_dispatcher", lambda _s: (dispatcher, dispatcher.cibseven))

    # Neutralized so the checkpointer leg cannot be what sets `dispatcher = None`. Without this the
    # mutant would "pass" the test for the wrong reason — a provisioning failure downstream of the
    # assertion produces the same `dispatcher is None` the assertion is supposed to produce.
    async def _no_checkpointer(_state: Any) -> None:
        return None

    monkeypatch.setattr(svc, "_provision_dispatch_checkpointer", _no_checkpointer)

    state = svc.WebhookState(settings=_webhook_settings())
    with structlog.testing.capture_logs() as logs:
        await svc._bring_up_dependencies(state)
    return state, logs


async def test_webhook_root_refuses_to_serve_when_a_seam_is_ungated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(c) THE LIVE PATH — the one root that actually runs traffic today.

    This receiver has no `/readyz` gate for dispatch: an unconfigured dispatcher degrades
    `/webhook` to its explicit 501, it does not colour readiness. So the ONLY consequence
    available here is dropping the dispatcher, and `state.dispatcher is None` IS the 501 path.

    A mutant that turns `if not gated:` into `if False:` leaves this daemon computing the
    assertion, logging nothing about it, and then serving real WhatsApp turns through an ungated
    effect seam — the exact deployment this leg exists to make impossible.
    """
    state, logs = await _run_webhook_bringup(monkeypatch, smuggle=True)

    assert state.dispatcher is None, (
        "an UNGATED effect seam did not drop the dispatcher — `/webhook` would serve live turns "
        "through it instead of answering its explicit 501"
    )
    assert state.dispatcher_error is not None and "not gated" in state.dispatcher_error

    refusals = [e for e in logs if e["event"] == "webhook_dispatcher_refused_ungated_effect_seams"]
    assert len(refusals) == 1, "the refusal must be announced exactly once"
    assert refusals[0]["log_level"] == "error"
    assert "inference=_RawInference" in refusals[0]["detail"], (
        "the refusal must name WHICH seam was ungated — an operator cannot act on 'something'"
    )
    assert not [e for e in logs if e["event"] == "webhook_effect_seams_gated"]


async def test_webhook_root_serves_when_every_seam_is_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    """The control. Without it, "refuses to serve" is indistinguishable from "never serves"."""
    state, logs = await _run_webhook_bringup(monkeypatch, smuggle=False)

    assert state.dispatcher is not None, "a fully gated dispatcher was dropped — the gate over-fires"
    assert state.dispatcher_error is None
    gated_lines = [e for e in logs if e["event"] == "webhook_effect_seams_gated"]
    assert len(gated_lines) == 1
    assert not [e for e in logs if e["event"] == "webhook_dispatcher_refused_ungated_effect_seams"]


# =================================================================================================
# (d) notifications_bridge.py — REFUSES TO CONSTRUCT.
# =================================================================================================


def _bridge_settings() -> Any:
    from maezo.platform.integrations.notifications_bridge import NotificationsBridgeSettings

    return NotificationsBridgeSettings(DATABASE_URL="postgresql://u:p@localhost:5432/db")


def test_bridge_root_refuses_to_start_when_the_engine_seam_is_ungated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(d) This daemon STARTS REGULATORY PROCESSES. Its consequence is refusing to exist.

    `build_bridge` already raises on a missing `DATABASE_URL` — refuse-to-construct is this root's
    established fail-closed idiom, and §5.5's assertion is expressed in it. A neutered `raise`
    leaves a bridge that starts SP-OP-* processes through an un-gated engine seam, which is worse
    than the missing-audit-sink case the DATABASE_URL check already refuses for.
    """
    import maezo.platform.integrations.notifications_bridge as bridge_mod

    monkeypatch.setattr(bridge_mod, "PostgresAuditSink", lambda *_a, **_k: object())
    # The smuggle: the sanctioned constructor returns something that is not a `GatedSeam`.
    monkeypatch.setattr(bridge_mod, "build_cibseven_seam", lambda **_k: FakeCibSevenTransport())

    with pytest.raises(RuntimeError) as excinfo:
        bridge_mod.build_bridge(_bridge_settings())

    message = str(excinfo.value)
    assert "not gated" in message, message
    assert "cibseven=FakeCibSevenTransport" in message, (
        "the refusal must name the ungated seam, not just that one existed"
    )


def test_bridge_root_constructs_when_the_engine_seam_is_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    """The control: the same root, the same settings, a GATED transport — and it builds."""
    import maezo.platform.integrations.notifications_bridge as bridge_mod

    monkeypatch.setattr(bridge_mod, "PostgresAuditSink", lambda *_a, **_k: object())
    monkeypatch.setattr(
        bridge_mod,
        "build_cibseven_seam",
        lambda **_k: gate_cibseven(FakeCibSevenTransport(), _seam("helena")),
    )

    bridge, transport = bridge_mod.build_bridge(_bridge_settings())
    assert bridge is not None
    from maezo.gateway.seams import is_gated_seam

    assert is_gated_seam(transport)


# =================================================================================================
# (a) agent_runtime + (e) worker_runtime — THE CHECK IS IN THE READINESS SET, AND IT GOES RED.
# =================================================================================================


def _agent_state() -> Any:
    from maezo.runtime.agent_runtime.service import AgentState
    from maezo.runtime.agent_runtime.settings import AgentRuntimeSettings

    return AgentState(settings=AgentRuntimeSettings(agent_id="rafael"))


def _worker_state() -> Any:
    from maezo.runtime.worker_runtime.service import WorkerRuntimeSettings, WorkerState

    return WorkerState(settings=WorkerRuntimeSettings())


async def _resolve_checks(build: Any, state: Any) -> dict[str, Any]:
    """Await every readiness check and key the results by the NAME each reports.

    Keyed by `CheckResult.name`, deliberately, not by the Python function name: the two roots spell
    the closure differently (`effect_seams_gated` vs `effect_seams_gated_check`) while both must
    surface the SAME probe name to `/readyz`, and it is the surfaced name an operator and an alert
    rule bind to.
    """
    return {result.name: result for result in [await check() for check in build(state)]}


@pytest.mark.parametrize("root", ["agent_runtime", "worker_runtime"])
async def test_the_readiness_set_actually_contains_the_boot_assertion(root: str) -> None:
    """(a)+(e) The mutant here does not weaken the check — it simply does not RETURN it.

    A daemon whose readiness set omits `effect_seams_gated` reports READY while running ungated
    seams, and every proof that the check itself is correct stays green throughout. Presence in
    the returned set is therefore its own obligation.
    """
    if root == "agent_runtime":
        from maezo.runtime.agent_runtime.service import build_readiness_checks

        state = _agent_state()
    else:
        from maezo.runtime.worker_runtime.service import build_readiness_checks

        state = _worker_state()

    results = await _resolve_checks(build_readiness_checks, state)
    assert "effect_seams_gated" in results, (
        f"{root}: `effect_seams_gated` is NOT in the readiness set — /readyz would go green with "
        "un-gated effect seams, and I-11's runtime half would be unenforced at this root"
    )


@pytest.mark.parametrize("root", ["agent_runtime", "worker_runtime"])
async def test_the_boot_assertion_check_goes_red_on_a_smuggled_raw_dep(root: str) -> None:
    """(a)+(e) …and it is wired to the real probe's verdict, not hardcoded green.

    The snapshot is written through the REAL primitive over a dep map carrying a raw transport —
    the same smuggling shape the primitive's own test uses — so this asserts the whole chain the
    daemon relies on: raw dep -> `effect_seams_gated` -> state snapshot -> `/readyz` red, with
    the offending seam named in the detail.
    """
    if root == "agent_runtime":
        from maezo.runtime.agent_runtime.service import build_readiness_checks

        state = _agent_state()
    else:
        from maezo.runtime.worker_runtime.service import build_readiness_checks

        state = _worker_state()

    seam = _seam()
    smuggled = {"dmn": gate_dmn(FakeDmnTransport(), seam), "cibseven": FakeCibSevenTransport()}
    state.effect_seams_gated, state.effect_seams_detail = effect_seams_gated(smuggled)
    assert state.effect_seams_gated is False, "fixture guard: the dep map must really be ungated"

    red = (await _resolve_checks(build_readiness_checks, state))["effect_seams_gated"]
    assert red.healthy is False, f"{root}: a raw transport left /readyz GREEN"
    assert "cibseven=FakeCibSevenTransport" in (red.detail or ""), (
        f"{root}: the readiness detail does not name the ungated seam"
    )

    # …and the same wiring reports healthy when the dep map really is gated (non-vacuity control).
    state.effect_seams_gated, state.effect_seams_detail = effect_seams_gated(
        {"dmn": gate_dmn(FakeDmnTransport(), seam), "cibseven": gate_cibseven(FakeCibSevenTransport(), seam)}
    )
    green = (await _resolve_checks(build_readiness_checks, state))["effect_seams_gated"]
    assert green.healthy is True, f"{root}: a fully gated dep map still reported red"
