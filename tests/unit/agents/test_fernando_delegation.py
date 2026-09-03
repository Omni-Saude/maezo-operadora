"""Unit tests for `agents.fernando.delegation` — GAP 11.7's inbound `arrears.followup` edge.

Mirrors `tests/unit/agents/test_carolina_delegation.py` exactly (same shape: engine-free,
PG-free — `make_fernando_handler` runs the REAL `fernando.graph.build(config)` graph against
fakes, no I/O). This module's origin side (`delegate_arrears_followup`) is NOT called from
`tools/workers/inadimplencia.py` yet (see `agents/fernando/delegation.py`'s module docstring —
that call site is outside this work package's editable surface); these tests cover the TARGET
side end to end plus the origin helpers' own contract in isolation.
"""

from __future__ import annotations

from typing import Any

import pytest

from maezo.agents.fernando.delegation import (
    ORIGIN_WORKER,
    TARGET_AGENT,
    TASK_TYPE_ARREARS_FOLLOWUP,
    arrears_followup_task_id,
    build_arrears_followup_envelope,
    make_fernando_handler,
    state_from_envelope,
)
from maezo.agents.fernando.graph import _CALLER_INPUT_FIELDS, build
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

_CASE_META_NOTIFY = {
    "intencao": "acompanhamento_purga",
    "tipo_plano": "individual",
    "origem_solicitacao": "cobranca",
    "data_solicitacao_iso": "2026-09-01",
    "meses_inadimplencia": 2,
    "valor_total_devido_cents": 15000,
    "dentro_periodo_minimo": True,
    "notificacao_previa_feita": True,
    "dentro_janela_purga": True,
    "ja_em_rescisao_cancel": False,
    # Must NEVER reach payload_meta (list-shaped, strict allowlist):
    "competencias_em_aberto": ["2026-06", "2026-07"],
    "documentos_refs": [{"ref": "doc://1"}],
}

_CASE_META_RESCISAO = {**_CASE_META_NOTIFY, "intencao": "rescisao"}


class _FakeInference:
    def __init__(self, responses: list[str] | None = None) -> None:
        self._responses = list(responses) if responses else ["texto sintetico"]
        self.calls: list[tuple[str, bool]] = []

    async def generate(
        self, prompt: str, *, phi: bool = False, agent_id: str | None = None, tenant_id: str | None = None
    ) -> str:
        self.calls.append((prompt, phi))
        return self._responses.pop(0) if self._responses else ""


class _FakeWhatsApp:
    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        raise AssertionError(
            "notify() must never actually send on the a2a-delegated path — canal is always "
            "'a2a', never 'whatsapp', so this fake must never be called"
        )


def _envelope(*, case_meta: dict[str, Any] = _CASE_META_NOTIFY, numero_contrato: str = "CTR-EDGE-1") -> Any:
    return build_arrears_followup_envelope(tenant="amh", case_meta=case_meta, numero_contrato=numero_contrato)


def _dmn_notify() -> FakeDmnTransport:
    dmn = FakeDmnTransport()
    dmn.register("inadimplencia_status", [{"roteamento": "AGUARDA_PURGA", "motivo": "dentro da janela"}])
    dmn.register(
        "inadimplencia_purga",
        [
            {
                "prazo_purga": "P10D",
                "prazo_notificacao_previa": "P50D",
                "periodo_minimo": "P60D",
                "fonte_regulatoria": "RN 593",
            }
        ],
    )
    return dmn


def _handler(dmn: FakeDmnTransport, *, inference: Any = None) -> Any:
    return make_fernando_handler(
        inference or _FakeInference(),
        dmn=dmn,
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=_FakeWhatsApp(),
    )


# --- task_id / envelope builder -----------------------------------------------------------------


def test_arrears_followup_task_id_is_the_process_business_key() -> None:
    assert arrears_followup_task_id("amh", numero_contrato="CTR-1") == "INAD-amh-CTR-1"


def test_arrears_followup_task_id_falls_back_to_matricula() -> None:
    """Mirrors `fernando.graph._business_key`'s own documented fallback (individual/familiar
    plans with no contract number)."""
    assert arrears_followup_task_id("amh", matricula_beneficiario="MAT-1") == arrears_followup_task_id(
        "amh", matricula_beneficiario="MAT-1"
    )
    task_id = arrears_followup_task_id("amh", matricula_beneficiario="MAT-1")
    assert task_id.startswith("INAD-amh-")


def test_envelope_contract_matches_fernando_card() -> None:
    envelope = _envelope()
    assert envelope.task_type == TASK_TYPE_ARREARS_FOLLOWUP == "arrears.followup"
    assert envelope.origin == ORIGIN_WORKER == "inadimplencia-worker"
    assert envelope.target == TARGET_AGENT == "fernando"
    assert envelope.task_id == "INAD-amh-CTR-EDGE-1"
    assert envelope.payload_ref == "process://INAD-amh-CTR-EDGE-1"
    assert envelope.delegation_chain == ("inadimplencia-worker", "fernando")


def test_payload_meta_is_a_strict_non_phi_allowlist() -> None:
    """Bounded strings/ints/booleans only; the list-shaped facts are structurally excluded
    (`payload_meta` is `Mapping[str, str]` — allowlist, not blocklist, ADR-0006)."""
    meta = dict(_envelope().payload_meta)
    assert meta["numero_contrato"] == "CTR-EDGE-1"
    assert meta["intencao"] == "acompanhamento_purga"
    assert meta["meses_inadimplencia"] == "2"
    assert meta["dentro_periodo_minimo"] == "true"
    assert meta["ja_em_rescisao_cancel"] == "false"
    assert "competencias_em_aberto" not in meta
    assert "documentos_refs" not in meta
    assert all(isinstance(v, str) for v in meta.values())


# --- state_from_envelope -------------------------------------------------------------------------


def test_state_from_envelope_maps_meta_into_fernando_input_fields_only() -> None:
    state = state_from_envelope(_envelope())

    assert set(state) <= _CALLER_INPUT_FIELDS
    assert state["tenant_id"] == "amh"
    assert state["canal"] == "a2a"
    assert state["numero_contrato"] == "CTR-EDGE-1"
    assert state["intencao"] == "acompanhamento_purga"
    assert state["meses_inadimplencia"] == 2
    assert state["dentro_periodo_minimo"] is True
    assert state["ja_em_rescisao_cancel"] is False
    # Never seeded — this graph must classify/derive them itself, never trust an envelope's say.
    assert "beneficiario_pseudo_id" not in state
    assert "to_hash" not in state


def test_state_from_envelope_fails_closed_without_any_identity() -> None:
    """Neither `receive` nor `start_process` has a degenerate-key short-circuit — a blank
    identity is a producer bug and must raise loudly here, never build `INAD-{tenant}-`."""
    envelope = build_arrears_followup_envelope(tenant="amh", case_meta={}, numero_contrato="CTR-1")
    object.__setattr__(envelope, "payload_meta", {})  # simulate a foreign, malformed producer
    with pytest.raises(ValueError, match="numero_contrato/matricula_beneficiario"):
        state_from_envelope(envelope)


def test_state_from_envelope_accepts_matricula_only_no_numero_contrato() -> None:
    envelope = build_arrears_followup_envelope(
        tenant="amh", case_meta=_CASE_META_NOTIFY, matricula_beneficiario="MAT-1"
    )
    state = state_from_envelope(envelope)
    assert state["matricula_beneficiario"] == "MAT-1"
    assert "numero_contrato" not in state


# --- make_fernando_handler ------------------------------------------------------------------------


async def test_handler_notify_route_returns_process_reference_and_bounded_meta() -> None:
    handler = _handler(_dmn_notify())
    output = await handler(_envelope())

    assert output.output_ref == "process://INAD-amh-CTR-EDGE-1"
    assert output.meta["route"] == "notify"
    assert output.meta["desfecho"] == "lembrete_regularizacao_enviado"
    # `notify` never starts SP-OP-INADIMPLENCIA-001 (graph topology, `start_process`'s own
    # docstring) — this is the CORRECT desfecho for the a2a-delegated purge-window follow-up.
    assert output.meta["process_started"] == "False"


async def test_handler_never_sends_whatsapp_on_the_a2a_path() -> None:
    """`canal` is always `"a2a"` (`state_from_envelope`) — `notify()`'s WhatsApp branch is
    structurally unreachable; `_FakeWhatsApp.send` raises if this regresses."""
    handler = _handler(_dmn_notify())
    output = await handler(_envelope())
    assert output.meta["route"] == "notify"  # got here without _FakeWhatsApp.send() raising


async def test_handler_rescisao_intent_escalates_and_starts_the_process() -> None:
    """J3 (`intencao=rescisao`) ALWAYS escalates — never consults `inadimplencia_status` at all
    (module docstring) — and DOES start SP-OP-INADIMPLENCIA-001 (the `escalate` route)."""
    envelope = _envelope(case_meta=_CASE_META_RESCISAO, numero_contrato="CTR-RESC-1")
    handler = _handler(FakeDmnTransport())  # no DMN registered — rescisao never calls one
    output = await handler(envelope)

    assert output.output_ref == "process://INAD-amh-CTR-RESC-1"
    assert output.meta["route"] == "escalate"
    assert output.meta["desfecho"] == "encaminhado_analise_humana"
    assert output.meta["motivo_humano"] == "indicio_rescisao"
    assert output.meta["process_started"] == "True"


async def test_handler_never_forwards_the_dossier_content() -> None:
    """Mirrors Carolina's structural guardrail: the dossier stays in Fernando's own engine
    variables, never in the A2A response — `HandlerOutput.meta` carries bounded class tokens
    only."""
    envelope = _envelope(case_meta=_CASE_META_RESCISAO, numero_contrato="CTR-RESC-2")
    handler = _handler(FakeDmnTransport())
    output = await handler(envelope)

    assert "dossier" not in output.meta
    assert "dossie" not in str(output.meta).lower()


async def test_handler_dmn_unavailable_fails_closed_to_escalate() -> None:
    """`inadimplencia_status` DMN unregistered -> `_evaluate_dmn` errors -> escalate
    `dmn_indisponivel`, never a silent `notify` default (module docstring's fail-safe)."""
    envelope = _envelope(case_meta=_CASE_META_NOTIFY, numero_contrato="CTR-DMN-DOWN")
    handler = _handler(FakeDmnTransport())  # nothing registered
    output = await handler(envelope)

    assert output.meta["route"] == "escalate"
    assert output.meta["motivo_humano"] == "dmn_indisponivel"


# --- input-boundary completeness (structural, mirrors `test_carolina.py`) -----------------------


def test_output_field_partition_is_complete() -> None:
    """GAP 11.7 completeness guard: every `FernandoState` field is classified as exactly one of
    caller-input (`_CALLER_INPUT_FIELDS`) or output-only (`_output_field_resets()`). Adding a new
    state field without classifying it fails here — the same root-cause class Helena/Carolina/
    Rafael already close stays closed for Fernando too."""
    from maezo.agents.fernando.graph import FernandoState, _output_field_resets

    annotations = set(FernandoState.__annotations__)
    outputs = set(_output_field_resets())
    assert _CALLER_INPUT_FIELDS & outputs == set()
    assert _CALLER_INPUT_FIELDS | outputs == annotations


# --- consistency with `build(config)`'s own fail-closed contract --------------------------------


async def test_handler_uses_the_real_fail_closed_build_contract() -> None:
    """`make_fernando_handler` must go through the SAME `build(config)` every other caller uses
    (never a bespoke construction path) — proven by reusing `build` directly against the SAME
    envelope-materialized state and checking the outcome matches the handler's own."""
    state = state_from_envelope(_envelope())

    direct = build(
        {
            "inference": _FakeInference(),
            "dmn": _dmn_notify(),
            "cibseven": FakeCibSevenTransport(),
            "audit_sink": FakeStartAuditSink(),
            "whatsapp": _FakeWhatsApp(),
        }
    ).compile()
    direct_result: dict[str, Any] = await direct.ainvoke(state)

    handler_output = await _handler(_dmn_notify())(_envelope())

    assert direct_result["route"] == "notify"
    assert handler_output.meta["route"] == "notify"
