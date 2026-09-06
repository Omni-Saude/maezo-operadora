"""Unit tests for `agents.beatriz.delegation` — the inbound `fraude.investigate` edge (BEA-09).

Mirrors `tests/unit/agents/test_fernando_delegation.py` (engine-free, PG-free — `make_beatriz_
handler` runs the REAL `beatriz.graph.build(config)` graph against fakes, no I/O). The origin side
(`delegate_fraude_investigation`) is NOT called from `tools/workers/fraude.py` (see
`agents/beatriz/delegation.py`'s module docstring — that worker is a disclosed placeholder and an
owner-gated call site outside this work package); these tests cover the TARGET side end to end
plus the origin helpers' own contract in isolation.
"""

from __future__ import annotations

from typing import Any

import pytest

from maezo.agents.beatriz.delegation import (
    ORIGIN_WORKER,
    TARGET_AGENT,
    TASK_TYPE_FRAUDE_INVESTIGATE,
    TASK_TYPES,
    build_fraude_investigation_envelope,
    fraude_task_id,
    make_beatriz_handler,
    state_from_envelope,
)
from maezo.agents.beatriz.graph import _CALLER_INPUT_FIELDS, _business_key, build

# A free-text narrative and raw-PHI-shaped keys are planted in `case_meta`: the allowlist must
# drop every one of them by omission (ADR-0006, no-PHI-in-custody).
_PHI_PLANTS: dict[str, Any] = {
    "narrativa": "beneficiario relata internacao em 2026-05 por quadro clinico X",
    "cpf": "00000000000",
    "nome_beneficiario": "Fulano de Tal",
}

_CASE_META: dict[str, Any] = {
    "origem_encaminhamento": "contas",
    "entidade_tipo": "prestador",
    "entidade_pseudo_id": "ent-p1",
    "prestador_id": "prest-1",
    "beneficiario_pseudo_id": "pseudo-b1",
    "numero_contrato": "CTR-1",
    "encaminhado_por_id": "analista-h1",
    "competencia": "2026-06",
    "feature_snapshot_ref": "snap://prov-1/2026-06",
    "patient_summary_ref": "pseudo-b1",
    "intensidade_investigacao": "APROFUNDADA",
    "score_indicadores": 7,
    "indicio_fraude_sinalizado": True,
    # list-shaped — structurally excluded (module docstring's disclosed residual)
    "evidencia_refs": [{"ref": "evd://tiss/lote-1", "hash": "h-1", "origem": "tiss"}],
    "indicadores_presentes": ["upcoding_ceiling", "peer_deviation"],
    **_PHI_PLANTS,
}


class _FakeInference:
    def __init__(self, responses: list[str] | None = None) -> None:
        self._responses = list(responses) if responses else ["dossie de investigacao sintetico"]
        self.calls: list[tuple[str, bool]] = []

    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        # CC-12 x integracao lote3 (LOTE3-INTEGRATION-FAKES-TASK-KIND): assinatura acompanha o
        # Protocol real (`runtime/inference::InferenceProvider.generate`), mesma especie do
        # defeito f1bc87f.
        task_kind: str | None = None,
    ) -> str:
        self.calls.append((prompt, phi))
        return self._responses.pop(0) if self._responses else ""


def _envelope(*, numero_caso: str = "000001", case_meta: dict[str, Any] | None = None) -> Any:
    return build_fraude_investigation_envelope(
        tenant="t1", numero_caso=numero_caso, case_meta=case_meta if case_meta is not None else _CASE_META
    )


def _handler(*, inference: Any = None) -> Any:
    return make_beatriz_handler(inference or _FakeInference())


# --- task types / task_id / envelope builder ----------------------------------------------------


def test_task_types_match_the_agent_card_contract() -> None:
    assert {"fraude.investigate"} == TASK_TYPES


def test_task_id_is_the_process_business_key() -> None:
    """MUTATION PROBE (dispatcher Guard 4): a `task_id` that is not `FRAUDE-{tenant}-{caso}`
    breaks idempotent replay — the same case would run Beatriz twice under two keys."""
    assert fraude_task_id("t1", "000001") == "FRAUDE-t1-000001"


def test_envelope_contract_matches_beatriz_card() -> None:
    envelope = _envelope()
    assert envelope.task_type == TASK_TYPE_FRAUDE_INVESTIGATE == "fraude.investigate"
    assert envelope.origin == ORIGIN_WORKER == "fraude-worker"
    assert envelope.target == TARGET_AGENT == "beatriz"
    assert envelope.task_id == "FRAUDE-t1-000001"
    assert envelope.payload_ref == "process://FRAUDE-t1-000001"
    assert envelope.delegation_chain == ("fraude-worker", "beatriz")


def test_envelope_task_id_equals_the_graphs_own_business_key() -> None:
    envelope = _envelope()
    assert envelope.task_id == _business_key(state_from_envelope(envelope))


# --- payload_meta: strict non-PHI allowlist -----------------------------------------------------


def test_payload_meta_never_carries_free_text_or_raw_phi() -> None:
    """MUTATION PROBE (ADR-0006): adding `narrativa`/`cpf`/`nome_beneficiario` to `_STRING_META_
    KEYS` turns this RED. Allowlist, not blocklist — the plants are in `case_meta` and must be
    dropped by omission."""
    meta = dict(_envelope().payload_meta)
    for forbidden in _PHI_PLANTS:
        assert forbidden not in meta
    assert all(isinstance(value, str) for value in meta.values())


def test_payload_meta_excludes_the_list_shaped_facts_and_carries_the_scalars() -> None:
    """The DISCLOSED residual: `evidencia_refs`/`indicadores_presentes` do not fit a
    `Mapping[str, str]` and are excluded; the scalar routing facts survive."""
    meta = dict(_envelope().payload_meta)
    assert "evidencia_refs" not in meta
    assert "indicadores_presentes" not in meta
    assert meta["score_indicadores"] == "7"
    assert meta["intensidade_investigacao"] == "APROFUNDADA"
    assert meta["indicio_fraude_sinalizado"] == "true"


# --- state_from_envelope ------------------------------------------------------------------------


def test_state_from_envelope_maps_meta_into_beatriz_input_fields_only() -> None:
    state = state_from_envelope(_envelope())

    assert set(state) <= _CALLER_INPUT_FIELDS
    assert state["tenant_id"] == "t1"
    assert state["numero_caso"] == "000001"
    assert state["entidade_tipo"] == "prestador"
    assert state["score_indicadores"] == 7
    assert state["indicio_fraude_sinalizado"] is True
    assert "narrativa" not in state


def test_state_from_envelope_leaves_the_list_shaped_facts_absent() -> None:
    """Documented consequence of the seam, asserted rather than implied: `evidencia_refs` and
    `indicadores_presentes` are ABSENT — an artifact of a `Mapping[str, str]` payload, never a
    finding of "no evidence"/"no indicators"."""
    state = state_from_envelope(_envelope())
    assert "evidencia_refs" not in state
    assert "indicadores_presentes" not in state


def test_state_from_envelope_fails_closed_without_numero_caso() -> None:
    envelope = _envelope()
    object.__setattr__(envelope, "payload_meta", {"prestador_id": "prest-1"})
    with pytest.raises(ValueError, match="numero_caso"):
        state_from_envelope(envelope)


def test_state_from_envelope_drops_a_planted_output_field() -> None:
    """A caller planting an accusation-shaped `dossier`/`desfecho` in `payload_meta` cannot reach
    the state — the keys are never READ (allowlist by construction)."""
    envelope = _envelope()
    planted = {
        **dict(envelope.payload_meta),
        "dossier": "acusacao",
        "desfecho": "dossie_instruido",
        "business_key": "FRAUDE-t1-forged",
    }
    object.__setattr__(envelope, "payload_meta", planted)
    state = state_from_envelope(envelope)
    assert "dossier" not in state
    assert "desfecho" not in state
    assert "business_key" not in state


def test_state_from_envelope_raises_if_a_meta_key_ever_escapes_the_input_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REG-06: the `unknown` guard at the end of `state_from_envelope` is a REAL, fail-closed
    check, not a `# pragma: no cover` promise — proven by making it fire for real instead of
    trusting the docstring's "structural, currently-unreachable" claim. `_STRING_META_KEYS` is
    monkeypatched to add a name outside `graph._CALLER_INPUT_FIELDS`, the same name is planted in
    `payload_meta` (the only way `raw` ever gains a key), and the guard is asserted to raise
    instead of silently widening the state.
    """
    import maezo.agents.beatriz.delegation as delegation_module

    bogus_key = "reg06_mutation_probe_unknown_key"
    assert bogus_key not in _CALLER_INPUT_FIELDS
    monkeypatch.setattr(
        delegation_module, "_STRING_META_KEYS", (*delegation_module._STRING_META_KEYS, bogus_key)
    )
    envelope = _envelope()
    planted = {**dict(envelope.payload_meta), bogus_key: "valor-fora-do-limite"}
    object.__setattr__(envelope, "payload_meta", planted)

    with pytest.raises(ValueError, match="non-input keys for Beatriz"):
        state_from_envelope(envelope)


# --- make_beatriz_handler -----------------------------------------------------------------------


async def test_handler_returns_the_process_reference_and_bounded_meta() -> None:
    output = await _handler()(_envelope())

    assert output.output_ref == "process://FRAUDE-t1-000001"
    assert output.meta["desfecho"] == "dossie_instruido"
    assert output.meta["error"] == ""


async def test_handler_never_forwards_the_dossier_content() -> None:
    """L0 hard (`zero_auto_accusation`): nothing leaving this handler can be read as an
    accusation. A CLOSED key set — adding `dossier`/`evidencia_normalizada` turns this RED."""
    output = await _handler()(_envelope())

    assert set(output.meta) == {
        "desfecho",
        "error",
        "gather_notes_count",
        "evidencia_normalizada_count",
    }
    assert all(isinstance(v, str) and len(v) <= 40 for v in output.meta.values())
    assert "decisao_fraude" not in output.meta
    assert "bundle_root" not in output.meta


async def test_handler_reports_the_empty_evidence_normalization_honestly() -> None:
    """The seam does not carry `evidencia_refs`, so a delegated turn normalizes ZERO pointers.
    That count is REPORTED (`evidencia_normalizada_count == "0"`) rather than hidden — it is a
    seam artifact, and a live origin call site must carry the list some other way."""
    output = await _handler()(_envelope())
    assert output.meta["evidencia_normalizada_count"] == "0"


async def test_handler_uses_the_real_fail_closed_build_contract() -> None:
    """`make_beatriz_handler` goes through the SAME `build(config)` every other caller uses.
    Her `build` REQUIRES only `inference` (she evaluates no DMN and starts no process), so the
    factory deliberately accepts no `dmn`/`cibseven` either."""
    state = state_from_envelope(_envelope())
    direct = build({"inference": _FakeInference()}).compile()
    direct_result: dict[str, Any] = await direct.ainvoke(state)

    handler_output = await _handler()(_envelope())

    assert direct_result["desfecho"] == "dossie_instruido"
    assert handler_output.meta["desfecho"] == "dossie_instruido"


def test_handler_factory_refuses_engine_transports_by_signature() -> None:
    """Beatriz's action surface is TIGHT (`spec/agents/beatriz/agent.yaml`): no
    `mcp-dmn.evaluate`, no `mcp-cibseven.start_process`. Offering those transports on this
    factory would silently widen it beyond the allowlist."""
    import inspect

    params = set(inspect.signature(make_beatriz_handler).parameters)
    assert params == {"inference", "fhir", "agent_version"}
