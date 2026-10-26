"""W7-7.3 fence — the provenance/transport COMPOSITION contract of `start_process_idempotent`.

COMPLEMENT, not duplicate, of the existing chokepoint coverage:

- `tests/unit/tools/test_start_process_fence.py:68` pins `audit_sink`/`provenance` as required
  keyword-only; `:105` pins audit-before-effect; `:150` pins never-double-start;
- `tests/unit/tools/test_start_process_dedup_gate.py` pins the duplicate-call outcomes
  (`:206-220`: same key twice -> one engine start, second call returns the SAME instance with
  `StartOutcome.ALREADY_ACTIVE`; `:568-579` for the `EXCLUSIVE` family);
- `tests/unit/gateway/intake/native/test_dispatch.py:344` pins the human-provenance-on-agent-
  transport refusal.

What NO existing test pins is the REMAINING composition guards at the head of the chokepoint
(`src/maezo/tools/mcp_cibseven/transport.py:1931-1942`):

- a `HumanIntakeProvenance` start is short-circuited into `start_human` ONLY when the transport
  is the dedicated `HumanIntakeStartTransport` AND `audit_sink` is IDENTICAL (`is`) to
  `transport.dispatcher.store` — the portal's durable dispatch store IS the audit sink; there is
  no second durable channel (`transport.py:1932-1933`);
- an intake transport paired with AGENT provenance is refused outright, whatever the sink
  (`transport.py:1941-1942`);
- the remaining parameters keep their keyword-only, no-default call shape
  (`transport.py:1823-1830`) — the existing fence pins only two of the five.

If a future edit relaxes the identity probe, drops the intake refusal, or changes the call
shape, the fences below go red ON PURPOSE. Per W7-7.3 that alarm is the signal to land the
justified contract change and the fence update in the SAME PR.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any, cast

import pytest

from maezo.gateway.human.auth_transport import AuthUnavailableError
from maezo.gateway.intake.native_dispatch import (
    HumanIntakeProvenance,
    HumanIntakeStartTransport,
)
from maezo.tools.mcp_cibseven.transport import AgentDecisionProvenance, start_process_idempotent
from tests.unit.gateway.intake.native.test_wire_transport import command


class _ProveReachedError(Exception):
    """Sentinel: the call got PAST the composition guards and reached the dispatcher seam.

    Deliberately NOT a `RuntimeError` (and so not an `AuthUnavailableError`, which is one —
    `gateway/human/auth_transport.py:48`), so the positive control cannot be confused with a
    refusal.
    """


def _provenance() -> AgentDecisionProvenance:
    return AgentDecisionProvenance(
        agent_id="andre",
        agent_version="andre@v0",
        tenant_id="amh",
        decision_basis={"route": "human_review"},
        model_id="claude-x",
        prompt_version="sys@v1",
    )


def _human_provenance() -> HumanIntakeProvenance:
    c = command()
    return HumanIntakeProvenance(
        kind="human_intake",
        scope=c.scope,
        actor=c.actor,
        intake_ref=c.intake_ref,
        command_id=c.command_id,
        admission=c.admission,
        guide_identity_ref=c.guide_identity_ref,
        definition=c.definition,
        input_pins=c.input_pins,
        start_facts_ref=c.start_facts_ref,
        start_facts_digest=c.start_facts_digest,
        projected_variables_digest=c.projected_variables_digest,
    )


def _intake_transport(store: object) -> HumanIntakeStartTransport:
    """A real `HumanIntakeStartTransport` over a placeholder dispatcher store.

    The composition guards only probe `type(transport)` and the IDENTITY of
    `transport.dispatcher.store` (`transport.py:1931-1933`), so the store never needs to be a
    real dispatch store in the refusal tests.
    """

    return HumanIntakeStartTransport(
        dispatcher=cast(Any, SimpleNamespace(store=store)),
        workload_ref="auth-sender",
        projection=lambda variables: command().projected_variables_digest,
    )


def test_call_shape_keeps_transport_positional_and_the_rest_keyword_only() -> None:
    """Complement of `test_start_process_fence.py:68` (which pins only sink/provenance): the
    OTHER three parameters keep keyword-only, no-default shape too (`transport.py:1823-1830`) —
    a silently added default or a positional-friendly reordering would change every caller's
    call-time contract."""
    params = inspect.signature(start_process_idempotent).parameters
    assert list(params)[0] == "transport", params
    for name in ("process_key", "business_key", "variables", "audit_sink", "provenance"):
        assert params[name].kind is inspect.Parameter.KEYWORD_ONLY, name
        assert params[name].default is inspect.Parameter.empty, name


async def test_agent_provenance_on_the_intake_transport_is_refused() -> None:
    """`transport.py:1941-1942` — an intake transport is NOT an agent transport, whatever sink is
    passed: the dedicated-capability rule has no structural fallback."""
    store: object = object()
    with pytest.raises(AuthUnavailableError):
        await start_process_idempotent(
            _intake_transport(store),
            process_key="SP-OP-AUTH-001",
            business_key="AUTH-tenant-GUIA-001",
            variables={},
            audit_sink=store,  # even the "right-looking" sink changes nothing here
            provenance=_provenance(),
        )


async def test_human_provenance_requires_the_sink_to_be_the_dispatcher_store() -> None:
    """`transport.py:1932-1933` — the audit sink of a portal start must be IDENTICAL to
    `transport.dispatcher.store`; any other sink (even a well-behaved one) refuses."""
    with pytest.raises(AuthUnavailableError):
        await start_process_idempotent(
            _intake_transport(object()),
            process_key="SP-OP-AUTH-001",
            business_key="AUTH-tenant-GUIA-001",
            variables={},
            audit_sink=object(),  # NOT transport.dispatcher.store
            provenance=_human_provenance(),
        )


async def test_human_provenance_with_the_matching_store_reaches_the_intake_path() -> None:
    """Positive control for the fence above: the CORRECT composition (intake transport + the very
    store object hanging off its dispatcher) passes BOTH guards and routes into `start_human`
    (`gateway/intake/native_dispatch.py:334-355`) — proved here by reaching the dispatcher's
    `prove` seam. If this ever reports `AuthUnavailableError` instead of `_ProveReachedError`, the
    short-circuit documented at `transport.py:1400` regressed."""

    class _Store:
        async def prove(self, _command: object) -> None:
            raise _ProveReachedError

    dispatcher_store = _Store()
    transport = HumanIntakeStartTransport(
        dispatcher=cast(Any, SimpleNamespace(store=dispatcher_store)),
        workload_ref="auth-sender",
        projection=lambda variables: command().projected_variables_digest,
    )
    with pytest.raises(_ProveReachedError):
        await start_process_idempotent(
            transport,
            process_key="SP-OP-AUTH-001",
            business_key="AUTH-tenant-GUIA-001",
            variables={},
            audit_sink=dispatcher_store,  # the SAME object: the identity the guard demands
            provenance=_human_provenance(),
        )
