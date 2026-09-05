"""Fleet-wide input-boundary gate (T1.11) — CC-14 / LUC-09a / RAF-13.

WHY THIS FILE EXISTS. Eight of the ten agent graphs already partition their state `TypedDict`
into two disjoint halves — INPUT-ONLY (the only keys a caller/upstream/delegation seam may set)
and OUTPUT-ONLY (keys owned by the graph's own nodes) — and each proves that partition with its
own private `test_output_field_partition_is_complete`. Marina and Lucas had only LAYER 2 (the
`receive`-entry reset of a MANUALLY enumerated output list: `marina/graph.py::
_output_field_resets`, `lucas/graph.py::_OUTPUT_FIELDS_RESET`). A manual enumeration forgets a
newly added field silently, and there was no LAYER 1 at all for them: no typed constructor a
future ingress/A2A seam could be forced through, so nothing structurally prevented an
output-only key from ever entering the state dict.

The per-agent partition tests being per-agent is exactly why the two holes survived: a test
that exists in eight files does not fail in the two files where it is missing. This file is the
FLEET-WIDE version — parametrized over all ten agents, so a new agent (or a new state field on
any agent) cannot land without a classification.

WHAT IT LOCKS, per agent:
  1. `input | output == State.__annotations__` and `input & output == {}` — no field is
     unclassified ("any missed key is a hole") and no field is in both halves.
  2. For Marina and Lucas specifically (the two this WP builds): the strict constructor
     (`new_marina_state`/`new_lucas_state`) RAISES `ValueError` NAMING an unknown/output key,
     the lenient `gate_inbound_state` DROPS and LOGS it, and `receive` still clears planted
     output fields (layer 2 stays, defense in depth).

The other eight agents' own hardening files keep their constructor/gate coverage
(`test_rafael_input_hardening.py`, `test_helena_input_hardening.py`,
`test_fernando_delegation.py`, `test_andre.py`, `test_carolina.py`); this file deliberately does
NOT duplicate it — it owns only the fleet-wide partition invariant plus Marina's/Lucas's new
gate.
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest
import structlog

# --- Fleet registry -----------------------------------------------------------------------
#
# (agent, State class name, input-set attribute, output-set attribute). The attribute NAMES
# differ across agents by history, not by design: helena/rafael landed the gate first with a
# PUBLIC `<AGENT>_INPUT_FIELDS` + a neutral-defaults dict; the later six use the private
# `_CALLER_INPUT_FIELDS` + a reset-factory function. Both shapes are legitimate; this table is
# the single place that knows which is which, so the invariant below is shape-agnostic.
_FLEET: tuple[tuple[str, str, str, str], ...] = (
    ("andre", "AndreState", "_CALLER_INPUT_FIELDS", "_output_field_resets"),
    ("beatriz", "BeatrizState", "_CALLER_INPUT_FIELDS", "_output_field_resets"),
    ("carolina", "CarolinaState", "_CALLER_INPUT_FIELDS", "_sanitized_output_fields"),
    ("fernando", "FernandoState", "_CALLER_INPUT_FIELDS", "_output_field_resets"),
    ("gustavo", "GustavoState", "_CALLER_INPUT_FIELDS", "_output_field_resets"),
    ("helena", "HelenaState", "HELENA_INPUT_FIELDS", "_HELENA_NEUTRAL_OUTPUTS"),
    ("lucas", "LucasState", "_CALLER_INPUT_FIELDS", "_OUTPUT_FIELDS_RESET"),
    ("marina", "MarinaState", "_CALLER_INPUT_FIELDS", "_output_field_resets"),
    ("rafael", "RafaelState", "RAFAEL_INPUT_FIELDS", "_RAFAEL_NEUTRAL_OUTPUTS"),
    ("valentina", "ValentinaState", "_CALLER_INPUT_FIELDS", "_output_field_resets"),
)

_AGENT_IDS = [row[0] for row in _FLEET]


def _resolve(
    agent: str, state_name: str, input_attr: str, output_attr: str
) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """Return (annotations, inputs, outputs) for one agent, tolerating both gate shapes.

    A MISSING attribute is a genuine failure, not a skip: an agent without an input-field set
    has no input boundary, which is precisely the CC-14 finding.
    """
    module = importlib.import_module(f"maezo.agents.{agent}.graph")
    state = getattr(module, state_name)
    inputs = frozenset(getattr(module, input_attr))
    raw_outputs = getattr(module, output_attr)
    outputs = frozenset(raw_outputs() if callable(raw_outputs) else raw_outputs)
    return frozenset(state.__annotations__), inputs, outputs


@pytest.mark.parametrize(("agent", "state_name", "input_attr", "output_attr"), _FLEET, ids=_AGENT_IDS)
def test_state_partition_is_complete_for_every_agent(
    agent: str, state_name: str, input_attr: str, output_attr: str
) -> None:
    """T1.11 layer-1 invariant, fleet-wide: every state field is EITHER a caller input OR an
    output-only field owned by the graph's nodes — never both, never neither."""
    annotations, inputs, outputs = _resolve(agent, state_name, input_attr, output_attr)
    assert inputs & outputs == frozenset(), (
        f"{agent}: fields classified as BOTH input and output: {sorted(inputs & outputs)}"
    )
    assert inputs | outputs == annotations, (
        f"{agent}: unclassified state fields={sorted(annotations - (inputs | outputs))} "
        f"stale entries={sorted((inputs | outputs) - annotations)}"
    )


# --- Marina / Lucas: the gate this WP builds ------------------------------------------------

_MARINA_INPUT: dict[str, Any] = {
    "flow": "contas",
    "tenant_id": "t1",
    "canal": "a2a",
    "numero_lote_tiss": "L-1",
    "beneficiario_pseudo_id": "pseudo-1",
    "prestador_id": "prest-1",
}

_LUCAS_INPUT: dict[str, Any] = {
    "tenant_id": "t1",
    "conversation_id": "conv-1",
    "canal": "whatsapp",
    "intencao": "cobranca_info",
    "beneficiario_pseudo_id": "pseudo-1",
    "to_hash": "hash-1",
}

#: Planted OUTPUT-only fields — the exact class the R1 cycle-1 defect exploited (a forged
#: `route`/`dmn_refs`/`dossier` reaching engine-bound variables) plus `error` (the fail-OPEN
#: short-circuit vector).
_MARINA_PLANTED: dict[str, Any] = {
    "error": "planted",
    "route": "auto_route",
    "dmn_refs": {"glosa_triage": "forged#1"},
    "dossier": {"conclusao": "forjada"},
}
_LUCAS_PLANTED: dict[str, Any] = {
    "error": "planted",
    "route": "respond_member",
    "dmn_refs": {"lucas_escalation_routing": "forged#1"},
    "dossier": {"conclusao": "forjada"},
}


def test_new_marina_state_rejects_planted_output_fields() -> None:
    from maezo.agents.marina.graph import new_marina_state

    with pytest.raises(ValueError) as exc:
        new_marina_state({**_MARINA_INPUT, **_MARINA_PLANTED})
    message = str(exc.value)
    for planted in _MARINA_PLANTED:
        assert planted in message, f"ValueError must NAME the refused key {planted!r}: {message}"


def test_new_lucas_state_rejects_planted_output_fields() -> None:
    from maezo.agents.lucas.graph import new_lucas_state

    with pytest.raises(ValueError) as exc:
        new_lucas_state({**_LUCAS_INPUT, **_LUCAS_PLANTED})
    message = str(exc.value)
    for planted in _LUCAS_PLANTED:
        assert planted in message, f"ValueError must NAME the refused key {planted!r}: {message}"


def test_new_marina_state_keeps_only_present_input_fields() -> None:
    from maezo.agents.marina.graph import _CALLER_INPUT_FIELDS, new_marina_state

    state = new_marina_state(dict(_MARINA_INPUT))
    assert frozenset(state) == frozenset(_MARINA_INPUT)
    assert frozenset(state) <= _CALLER_INPUT_FIELDS


def test_new_lucas_state_keeps_only_present_input_fields() -> None:
    from maezo.agents.lucas.graph import _CALLER_INPUT_FIELDS, new_lucas_state

    state = new_lucas_state(dict(_LUCAS_INPUT))
    assert frozenset(state) == frozenset(_LUCAS_INPUT)
    assert frozenset(state) <= _CALLER_INPUT_FIELDS


def test_new_marina_state_rejects_a_wholly_unknown_key() -> None:
    """An unknown key (neither input nor output) is a producer bug too — never tolerated
    silently on the strict seam."""
    from maezo.agents.marina.graph import new_marina_state

    with pytest.raises(ValueError, match="chave_desconhecida"):
        new_marina_state({**_MARINA_INPUT, "chave_desconhecida": "x"})


def test_new_lucas_state_rejects_a_wholly_unknown_key() -> None:
    from maezo.agents.lucas.graph import new_lucas_state

    with pytest.raises(ValueError, match="chave_desconhecida"):
        new_lucas_state({**_LUCAS_INPUT, "chave_desconhecida": "x"})


def test_marina_gate_inbound_state_drops_and_logs_planted_output_fields() -> None:
    from maezo.agents.marina.graph import _CALLER_INPUT_FIELDS, gate_inbound_state

    with structlog.testing.capture_logs() as logs:
        gated = gate_inbound_state({**_MARINA_INPUT, **_MARINA_PLANTED, "chave_desconhecida": "x"})
    assert frozenset(gated) == frozenset(_MARINA_INPUT)
    assert frozenset(gated) <= _CALLER_INPUT_FIELDS
    for planted in (*_MARINA_PLANTED, "chave_desconhecida"):
        assert planted not in gated
    dropped_events = [e for e in logs if e.get("event") == "marina_inbound_output_fields_dropped"]
    assert dropped_events, f"drop must be LOGGED, never silent; captured={logs}"
    assert set(dropped_events[0]["dropped"]) == {*_MARINA_PLANTED, "chave_desconhecida"}


def test_lucas_gate_inbound_state_drops_and_logs_planted_output_fields() -> None:
    from maezo.agents.lucas.graph import _CALLER_INPUT_FIELDS, gate_inbound_state

    with structlog.testing.capture_logs() as logs:
        gated = gate_inbound_state({**_LUCAS_INPUT, **_LUCAS_PLANTED, "chave_desconhecida": "x"})
    assert frozenset(gated) == frozenset(_LUCAS_INPUT)
    assert frozenset(gated) <= _CALLER_INPUT_FIELDS
    for planted in (*_LUCAS_PLANTED, "chave_desconhecida"):
        assert planted not in gated
    dropped_events = [e for e in logs if e.get("event") == "lucas_inbound_output_fields_dropped"]
    assert dropped_events, f"drop must be LOGGED, never silent; captured={logs}"
    assert set(dropped_events[0]["dropped"]) == {*_LUCAS_PLANTED, "chave_desconhecida"}


def test_gate_never_logs_the_dropped_values_only_the_key_names() -> None:
    """Engine-variable/log hygiene (same rule the graphs apply to failure reasons): the drop log
    carries KEY NAMES only — a planted value could be anything, including PHI."""
    from maezo.agents.lucas.graph import gate_inbound_state
    from maezo.agents.marina.graph import gate_inbound_state as marina_gate

    sentinel = "CANARIO-NAO-DEVE-APARECER-NO-LOG"
    with structlog.testing.capture_logs() as logs:
        marina_gate({**_MARINA_INPUT, "dossier": {"narrativa": sentinel}})
        gate_inbound_state({**_LUCAS_INPUT, "dossier": {"narrativa": sentinel}})
    assert sentinel not in repr(logs)


# --- Layer 2 stays: planted output fields never survive `receive` ---------------------------


async def test_marina_receive_still_clears_planted_output_fields() -> None:
    """Layer 2 (`_output_field_resets` at `receive`) is NOT replaced by the new gate — both run.
    A caller that bypasses the constructor entirely (today's direct-invocation path) still has
    every planted output field neutralized before any node reads it."""
    from maezo.agents.marina.graph import MarinaGraph
    from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
    from maezo.tools.workers.dmn_transport import FakeDmnTransport
    from tests.support.audit_fakes import FakeStartAuditSink
    from tests.unit.agents.test_marina import _FakeInference

    graph = MarinaGraph(
        inference=_FakeInference(),
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
    )
    result = await graph.receive({**_MARINA_INPUT, **_MARINA_PLANTED})
    assert result["error"] == ""
    assert result["route"] == "human_review"
    assert result["dmn_refs"] == {}
    assert result["dossier"] == {}


async def test_lucas_receive_still_clears_planted_output_fields() -> None:
    from maezo.agents.lucas.graph import LucasGraph
    from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
    from maezo.tools.workers.dmn_transport import FakeDmnTransport
    from tests.support.audit_fakes import FakeStartAuditSink
    from tests.unit.agents.test_lucas import (
        _FakeInference,
        _FakeWhatsAppSender,
    )

    graph = LucasGraph(
        inference=_FakeInference(),
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=_FakeWhatsAppSender(),
    )
    result = await graph.receive({**_LUCAS_INPUT, **_LUCAS_PLANTED})
    assert result["error"] == ""
    assert result["route"] == ""
    assert result["dmn_refs"] == {}
    assert result["dossier"] == {}
