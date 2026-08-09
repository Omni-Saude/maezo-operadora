"""Unit tests for `agents.andre.delegation` — the ADEQUACAO dossier A2A edge (DL-0033 real wiring).

Mirrors `tests/unit/agents/test_rafael_delegation.py`: engine-free, PG-free —
`make_andre_handler` runs the REAL `andre.graph.build(config)` graph against fakes, no I/O.
The SHARED-task_type disambiguation (`analytics.population` -> `flow` via `envelope.origin`) and
the EXPLICIT-flow requirement (his graph silently defaults a missing `flow` to `pagto_dossier`)
are the edge-specific proofs here. Live-Postgres idempotent-replay proof DEFERRED to the PR CI
lane (mirroring `test_a2a_edge_live_pg.py`) — no docker in this build environment.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from maezo.agents.andre.delegation import (
    DEGRADED_TOKENS,
    ORIGIN_PAGTO_WORKER,
    ORIGIN_WORKER,
    TARGET_AGENT,
    TASK_TYPE_POPULATION_ANALYTICS,
    _flow_for,
    adequacao_task_id,
    build_adequacao_dossier_envelope,
    build_pagto_dossier_envelope,
    make_andre_handler,
    pagto_task_id,
    state_from_envelope,
)
from maezo.agents.andre.graph import _CALLER_INPUT_FIELDS, _business_key, build
from maezo.tools.mcp_cibseven.transport import (
    CibSevenError,
    FakeCibSevenTransport,
    ProcessInstance,
)
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

_CASE_META = {
    "ciclo_avaliacao": "2026-Q3",
    "tipo_carater": "eletivo",
    "gap_adequacao": "GAP_CRITICO",
    "roteamento_remediacao": "ANALISE_HUMANA",
    "tempo_acesso_apurado_min": 95,
    "distancia_apurada_km": 42.5,
    "prestadores_disponiveis": 1,
    "cobertura_geo_suficiente": False,
    "dados_geo_completos": True,
    # Never forwarded (not in the allowlist):
    "observacoes_livres": "texto livre",
}


class _FakeInference:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    async def generate(self, prompt: str, *, phi: bool = False) -> str:
        self.calls.append((prompt, phi))
        assert phi is True, "Andre's dossier LLM call must be phi=True (security_zone: phi)"
        return "dossie de remediacao sintetico"


def _envelope(*, ciclo: str | None = "2026-Q3") -> Any:
    return build_adequacao_dossier_envelope(
        tenant="amh",
        regiao_saude="SP-01",
        especialidade="cardiologia",
        case_meta=_CASE_META,
        ciclo_avaliacao=ciclo,
    )


# --- task_id / envelope builder ---------------------------------------------------------------


def test_adequacao_task_id_is_the_cell_key() -> None:
    assert adequacao_task_id("amh", "SP-01", "cardiologia") == "ADEQ-amh-SP-01-cardiologia"
    assert adequacao_task_id("amh", "SP-01", "cardiologia", "2026-Q3") == "ADEQ-amh-SP-01-cardiologia-2026-Q3"


def test_envelope_uses_the_shared_task_type_and_worker_origin() -> None:
    """SHARED type (orchestrator decision): `analytics.population` — already accepted by Andre's
    card; NO new task_type is minted and spec/agents is untouched. `origin` is what carries the
    flow disambiguation."""
    envelope = _envelope()
    assert envelope.task_type == TASK_TYPE_POPULATION_ANALYTICS == "analytics.population"
    assert envelope.origin == ORIGIN_WORKER == "adequacao-worker"
    assert envelope.target == TARGET_AGENT == "andre"
    assert envelope.task_id == "ADEQ-amh-SP-01-cardiologia-2026-Q3"
    assert envelope.payload_ref == "process://ADEQ-amh-SP-01-cardiologia-2026-Q3"


def test_payload_meta_is_a_strict_non_phi_allowlist_of_cell_aggregates() -> None:
    meta = dict(_envelope().payload_meta)
    assert meta["regiao_saude"] == "SP-01"
    assert meta["gap_adequacao"] == "GAP_CRITICO"
    assert meta["tempo_acesso_apurado_min"] == "95"
    assert meta["distancia_apurada_km"] == "42.5"
    assert meta["cobertura_geo_suficiente"] == "false"
    assert "observacoes_livres" not in meta
    assert all(isinstance(v, str) for v in meta.values())


# --- origin -> flow disambiguation (the shared-task_type crux) --------------------------------


def test_flow_for_maps_adequacao_worker_origin_to_adequacao_dossier() -> None:
    assert _flow_for(_envelope()) == "adequacao_dossier"


def test_flow_for_any_other_origin_falls_back_to_andres_default_flow() -> None:
    foreign = replace(_envelope(), origin="pagto-worker", delegation_chain=("pagto-worker", "andre"))
    assert _flow_for(foreign) == "pagto_dossier"


def test_state_from_envelope_sets_flow_explicitly_for_adequacao_origin() -> None:
    state = state_from_envelope(_envelope())

    assert set(state) <= _CALLER_INPUT_FIELDS
    assert state["flow"] == "adequacao_dossier"  # EXPLICIT — never the graph's missing-key default
    assert state["tenant_id"] == "amh"
    assert state["canal"] == "a2a"
    assert state["regiao_saude"] == "SP-01"
    assert state["especialidade"] == "cardiologia"
    assert state["ciclo_avaliacao"] == "2026-Q3"
    assert state["tempo_acesso_apurado_min"] == 95
    assert state["distancia_apurada_km"] == 42.5
    assert state["prestadores_disponiveis"] == 1
    assert state["cobertura_geo_suficiente"] is False
    assert state["dados_geo_completos"] is True


def test_state_from_envelope_sets_default_flow_explicitly_for_foreign_origin() -> None:
    """The graph's `_flow` silently treats a MISSING `flow` key as `pagto_dossier` — the
    delegation layer must never rely on that: even the default flow is set EXPLICITLY."""
    foreign = replace(_envelope(), origin="pagto-worker", delegation_chain=("pagto-worker", "andre"))
    state = state_from_envelope(foreign)
    assert "flow" in state
    assert state["flow"] == "pagto_dossier"


# --- make_andre_handler -----------------------------------------------------------------------


async def test_handler_adequacao_flow_anchors_the_cell_and_routes_human_no_process_start() -> None:
    """Andre's adequacao flow: ALWAYS human (`gestao-rede`, `UT_DecisaoFallback`), NEVER a
    process start (the ADEQ key only anchors) — the handler surfaces exactly that."""
    handler = make_andre_handler(
        _FakeInference(),
        dmn=FakeDmnTransport(),  # adequacao flow evaluates NO DMN — an empty fake proves it
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
    )
    output = await handler(_envelope())

    assert output.output_ref == "process://ADEQ-amh-SP-01-cardiologia-2026-Q3"
    assert output.meta["route"] == "human_review"
    assert output.meta["desfecho"] == "dossie_remediacao_humano"
    assert output.meta["motivo_humano"] == "dossie_remediacao"
    assert output.meta["grupo_destino"] == "gestao-rede"
    assert output.meta["process_started"] == "False"


async def test_handler_never_forwards_the_dossier_and_guardrail_fields_stay_none() -> None:
    """The L0-hard structural guardrails (`graph.py::_build_dossier`): re-run the SAME state
    directly through `build(config)` to inspect the full result — the handler never forwards the
    dossier, but this proves the deeper graph invariant it rests on."""
    dmn = FakeDmnTransport()
    cibseven = FakeCibSevenTransport()
    audit_sink = FakeStartAuditSink()
    state = state_from_envelope(_envelope())

    compiled = build(
        {"inference": _FakeInference(), "dmn": dmn, "cibseven": cibseven, "audit_sink": audit_sink}
    ).compile()
    result: dict[str, Any] = await compiled.ainvoke(state)

    assert result["dossier"]["decisao_pagamento"] is None
    assert result["dossier"]["preco_recomendado"] is None
    assert result["dossier"]["fhir_patient_id"] is None
    assert result["process_started"] is False  # adequacao NEVER starts a process

    handler = make_andre_handler(_FakeInference(), dmn=dmn, cibseven=cibseven, audit_sink=audit_sink)
    output = await handler(_envelope(ciclo="2026-Q4"))
    assert "dossier" not in output.meta
    assert "narrativa" not in output.meta
    assert all("decisao" not in str(v).lower() for v in output.meta.values())


async def test_handler_missing_cell_identity_fail_safes_to_human_never_a_payment_key() -> None:
    """A malformed adequacao envelope (no regiao/especialidade in meta) reaches Andre's own
    `receive` fail-safe: conservative human review, NO malformed business key, NO process."""
    envelope = build_adequacao_dossier_envelope(
        tenant="amh", regiao_saude="SP-01", especialidade="cardiologia", case_meta={}
    )
    object.__setattr__(envelope, "payload_meta", {})  # simulate a foreign, malformed producer
    handler = make_andre_handler(
        _FakeInference(),
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
    )
    output = await handler(envelope)

    assert output.meta["route"] == "human_review"
    assert output.meta["process_started"] == "False"
    assert not output.output_ref.startswith("process://PAGTO-")  # never a payment key


# --- PAGTO origin builder (the DEFAULT `pagto_dossier` flow, shared task_type) -----------------

_PAGTO_CASE_META = {
    "tipo_pagamento": "prestador_rede",
    "valor_pagamento_cents": 25_000_000,
    "moeda": "BRL",
    "dados_pagamento_validos": True,
    "lastro_confirmado": True,
    "dentro_teto_l2": False,
    "duplicidade_suspeita": False,
    # Never forwarded (not in the pagto allowlist):
    "observacoes_livres": "texto livre",
}


def _pagto_envelope(**overrides: Any) -> Any:
    kwargs: dict[str, Any] = {
        "tenant": "amh",
        "case_meta": _PAGTO_CASE_META,
        "ordem_pagamento_id": "OP-001",
    }
    kwargs.update(overrides)
    return build_pagto_dossier_envelope(**kwargs)


def test_pagto_task_id_is_the_payment_business_key() -> None:
    assert pagto_task_id("amh", ordem_pagamento_id="OP-001") == "PAGTO-amh-OP-001"
    assert (
        pagto_task_id("amh", numero_lote_tiss="L9", prestador_id="P3") == "PAGTO-amh-L9-P3"
    )  # CONTAS-001-adjudicated variant


@pytest.mark.parametrize(
    "kwargs",
    [
        {"tenant": "", "ordem_pagamento_id": "OP-001"},
        {"tenant": "   ", "ordem_pagamento_id": "OP-001"},
        {"tenant": "amh"},  # no ordem, no lote/prestador at all
        {"tenant": "amh", "ordem_pagamento_id": "  "},  # whitespace-only
        {"tenant": "amh", "numero_lote_tiss": "L9"},  # lote without prestador
        {"tenant": "amh", "prestador_id": "P3"},  # prestador without lote
        {"tenant": "amh", "numero_lote_tiss": "L9", "prestador_id": "  "},
    ],
)
def test_pagto_task_id_refuses_to_mint_a_degenerate_key(kwargs: dict[str, Any]) -> None:
    """GK-dossier finding 6: `PAGTO-amh--`-class outputs are STRUCTURALLY impossible. The builder
    guards itself (EB-4 R1 `non_blank` discipline) instead of trusting every caller to pre-check —
    the pagto worker's own `except` turns the raise into a DISCLOSED gap (DL-0037, the UT opens)."""
    tenant = kwargs.pop("tenant")
    with pytest.raises(ValueError, match="pagto business key requires"):
        pagto_task_id(tenant, **kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"tenant": "  ", "ordem_pagamento_id": "OP-001"},
        {"tenant": "amh", "ordem_pagamento_id": ""},
        {"tenant": "amh", "ordem_pagamento_id": "  ", "numero_lote_tiss": "L9"},
    ],
)
def test_pagto_envelope_builder_enforces_the_same_guards(kwargs: dict[str, Any]) -> None:
    """The ENVELOPE builder is not a way around the key guard — it raises for the same inputs, so
    no degenerate `payload_ref`/`task_id` can ever reach the dispatcher."""
    with pytest.raises(ValueError, match="pagto business key requires"):
        _pagto_envelope(**kwargs)


def test_pagto_envelope_normalizes_the_tenant_and_the_identity_keys() -> None:
    """A padded tenant must not split the identity: the envelope's `tenant` is the SAME normalized
    token the key was built from, otherwise the target would seed `tenant_id=" amh "` and derive a
    DIFFERENT business key (`PAGTO- amh -...`) — the finding-1 divergence from the other side.
    Whitespace-only identity values are omitted, never forwarded as blank-but-truthy."""
    envelope = _pagto_envelope(tenant=" amh ", ordem_pagamento_id=" OP-001 ")
    assert envelope.task_id == "PAGTO-amh-OP-001"
    assert envelope.tenant == "amh"
    assert envelope.payload_meta["ordem_pagamento_id"] == "OP-001"

    padded = _pagto_envelope(ordem_pagamento_id="  ", numero_lote_tiss="L9", prestador_id="P3")
    assert padded.task_id == "PAGTO-amh-L9-P3"
    assert "ordem_pagamento_id" not in padded.payload_meta  # never a blank-but-truthy value
    state = state_from_envelope(padded)
    assert _business_key(state) == "PAGTO-amh-L9-P3"  # target derives the SAME key


@pytest.mark.parametrize("valor", [1234.99, "25000000", 25_000_000.0, True])
def test_pagto_envelope_rejects_non_integer_money(valor: Any) -> None:
    """GK-dossier finding 8: money is INTEGER CENTAVOS (ADR-0018 part 2) and is never coerced.
    The old `int(...)` TRUNCATED — `1234.99` silently became `1234` and Andre's faixa/alcada
    routing ran on a value the process never had. Even a float that happens to be whole
    (`25_000_000.0`) and a `bool` (an `int` subclass) are refused: the type is the contract."""
    with pytest.raises(ValueError, match="INTEGER centavos"):
        _pagto_envelope(case_meta={**_PAGTO_CASE_META, "valor_pagamento_cents": valor})


def test_pagto_envelope_accepts_integer_money_unchanged() -> None:
    envelope = _pagto_envelope(case_meta={**_PAGTO_CASE_META, "valor_pagamento_cents": 1234})
    assert envelope.payload_meta["valor_pagamento_cents"] == "1234"
    assert state_from_envelope(envelope)["valor_pagamento_cents"] == 1234


def test_pagto_envelope_reuses_shared_task_type_with_pagto_worker_origin() -> None:
    """The pagto edge REUSES `analytics.population` (no new task_type minted); the `pagto-worker`
    origin is what routes it — via `_flow_for`'s DEFAULT branch — to Andre's `pagto_dossier`."""
    envelope = _pagto_envelope()
    assert envelope.task_type == TASK_TYPE_POPULATION_ANALYTICS == "analytics.population"
    assert envelope.origin == ORIGIN_PAGTO_WORKER == "pagto-worker"
    assert envelope.target == TARGET_AGENT == "andre"
    assert envelope.task_id == "PAGTO-amh-OP-001"
    assert envelope.payload_ref == "process://PAGTO-amh-OP-001"


def test_pagto_payload_meta_is_a_strict_non_phi_allowlist() -> None:
    meta = dict(_pagto_envelope().payload_meta)
    assert meta["ordem_pagamento_id"] == "OP-001"
    assert meta["tipo_pagamento"] == "prestador_rede"
    assert meta["valor_pagamento_cents"] == "25000000"  # INTEGER-CENTAVOS as string
    assert meta["dentro_teto_l2"] == "false"
    assert meta["dados_pagamento_validos"] == "true"
    assert "observacoes_livres" not in meta
    assert all(isinstance(v, str) for v in meta.values())


def test_pagto_origin_routes_to_default_pagto_dossier_flow() -> None:
    assert _flow_for(_pagto_envelope()) == "pagto_dossier"


def test_state_from_envelope_sets_pagto_dossier_flow_explicitly_and_materializes_facts() -> None:
    state = state_from_envelope(_pagto_envelope())
    assert set(state) <= _CALLER_INPUT_FIELDS
    assert state["flow"] == "pagto_dossier"  # EXPLICIT — never the graph's missing-key default
    assert state["tenant_id"] == "amh"
    assert state["canal"] == "a2a"
    assert state["ordem_pagamento_id"] == "OP-001"
    assert state["tipo_pagamento"] == "prestador_rede"
    assert state["valor_pagamento_cents"] == 25_000_000  # parsed back to INTEGER-CENTAVOS
    assert state["dados_pagamento_validos"] is True
    assert state["dentro_teto_l2"] is False


def _pagto_dmn(*, faixa_valor: str = "ALCADA_L1", grupo: str = "aprovacao-financeira-l1") -> FakeDmnTransport:
    dmn = FakeDmnTransport()
    dmn.register("pagto_admissibility", [{"roteamento": "SEGUE_ROTEAMENTO", "motivo": "test"}])
    dmn.register("pagto_alcada", [{"faixa_valor": faixa_valor, "grupo_aprovador": grupo, "tier_minimo": 1}])
    dmn.register("pagto_sla", [{"sla_aprovacao": "P2D", "sla_alerta": "P1D", "fonte": "politica"}])
    return dmn


class _RecordingCibSeven(FakeCibSevenTransport):
    """Records every `start_process_instance` attempt — the RED proof for the origin no-op."""

    def __init__(self) -> None:
        super().__init__()
        self.start_calls: list[str] = []

    async def start_process_instance(
        self, process_key: str, business_key: str, variables: dict[str, Any]
    ) -> Any:
        self.start_calls.append(business_key)
        return await super().start_process_instance(process_key, business_key, variables)


async def test_handler_pagto_worker_origin_routes_human_but_never_starts_a_second_instance() -> None:
    """GK-dossier finding 1b (was: this test asserted `process_started == "True"` and so PINNED the
    duplicate-instance defect). The `pagto-worker` origin means the delegation was originated by
    `operadora.pagto.prepare_approval_dossier` from INSIDE an ALREADY-RUNNING SP-OP-PAGTO-001
    instance — Andre must NEVER start a second one (a second instance = a second
    `UT_AprovacaoAlcada` approval/release path). The dossier still lands in his DEFAULT
    `pagto_dossier` flow, evaluates the pagto DMN chain and routes the ALCADA_L1 faixa to the human
    approver (`aprovacao-financeira-l1`) — the business key only ANCHORS. NEVER an automatic
    release (L1 hard: the release is born only in the UT)."""
    cibseven = _RecordingCibSeven()
    handler = make_andre_handler(
        _FakeInference(),
        dmn=_pagto_dmn(),
        cibseven=cibseven,
        audit_sink=FakeStartAuditSink(),
    )
    output = await handler(_pagto_envelope())

    assert output.output_ref == "process://PAGTO-amh-OP-001"
    assert output.meta["route"] == "human_review"
    assert output.meta["motivo_humano"] == "aprovacao_alcada"
    assert output.meta["grupo_destino"] == "aprovacao-financeira-l1"
    # RED PROOF: the start was never even ATTEMPTED — not merely deduped downstream.
    assert output.meta["process_started"] == "False"
    assert cibseven.start_calls == []
    # L1 hard: no release/decision token ever leaves this seam.
    assert all("decisao" not in str(v).lower() for v in output.meta.values())
    assert all(str(v).upper() not in {"APROVAR", "RECUSAR", "CANCELAR"} for v in output.meta.values())


async def test_handler_pagto_flow_still_starts_the_process_for_a_non_worker_origin() -> None:
    """The origin no-op is SCOPED: `start_process` stays fully functional for every OTHER origin of
    the `pagto_dossier` flow (an autonomous/foreign originator legitimately opens the case)."""
    cibseven = _RecordingCibSeven()
    handler = make_andre_handler(
        _FakeInference(),
        dmn=_pagto_dmn(),
        cibseven=cibseven,
        audit_sink=FakeStartAuditSink(),
    )
    output = await handler(replace(_pagto_envelope(), origin="autonomous-originator"))

    assert output.meta["process_started"] == "True"
    assert cibseven.start_calls == ["PAGTO-amh-OP-001"]


async def test_engine_business_key_anchors_the_live_contas_variant_instance_no_duplicate() -> None:
    """GK-dossier finding 1a. The live instance is keyed with the contract's CONTAS variant
    (`PAGTO-{tenant}-{numero_lote_tiss}-{prestador_id}`, SP-OP-PAGTO-001 §Business key) while an
    `ordem_pagamento_id` is ALSO in scope — the ordem-FIRST derivation would mint
    `PAGTO-amh-OP-001`, miss `start_process_idempotent`'s exact-match lookup and start a SECOND
    live instance. Threading the ENGINE's authoritative key makes the lookup hit its OWN instance:
    exactly one instance, `already_existed=True`, and no start attempt at all.

    Uses a non-worker origin deliberately: this proves the KEY anchor (brace) in isolation, with
    the origin no-op (belt) out of the way."""
    live_key = "PAGTO-amh-L9-P3"
    cibseven = _RecordingCibSeven()
    cibseven.seed_instance(
        ProcessInstance(
            instance_id="pi-live-1",
            process_key="SP-OP-PAGTO-001",
            business_key=live_key,
            state="ACTIVE",
        )
    )
    handler = make_andre_handler(
        _FakeInference(),
        dmn=_pagto_dmn(),
        cibseven=cibseven,
        audit_sink=FakeStartAuditSink(),
    )
    envelope = _pagto_envelope(
        ordem_pagamento_id="OP-001",  # ordem ALSO in scope — the divergence trigger
        numero_lote_tiss="L9",
        prestador_id="P3",
        business_key=live_key,
    )
    assert envelope.task_id == live_key  # the engine key wins over the ordem-first derivation

    output = await handler(replace(envelope, origin="autonomous-originator"))

    assert output.output_ref == f"process://{live_key}"
    assert output.meta["process_started"] == "True"
    assert cibseven.start_calls == []  # the idempotent lookup HIT — nothing was started
    assert await cibseven.find_active_instance(live_key) is not None
    assert await cibseven.find_active_instance("PAGTO-amh-OP-001") is None  # no duplicate


async def test_handler_meta_discloses_dmn_degradation() -> None:
    """GK-dossier finding 4: an assess-chain DMN unavailable leaves the delegation STRUCTURALLY
    successful (Andre routes conservatively to a human) but internally degraded — `meta.degraded`
    discloses the class so the originating worker can flag it to the approver."""
    handler = make_andre_handler(
        _FakeInference(),
        dmn=FakeDmnTransport(),  # nothing registered -> the assess chain fails
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
    )
    output = await handler(_pagto_envelope())
    assert output.meta["degraded"] == "dmn_indisponivel"
    assert output.meta["route"] == "human_review"


async def test_handler_meta_discloses_engine_degradation_at_the_anchor_step() -> None:
    """The engine being unreachable at the anchor step is a DIFFERENT degradation class — matched
    by EQUALITY against `graph.ERROR_START_PROCESS_ENGINE_UNAVAILABLE`, never by text sniffing."""

    class _UnreachableCibSeven(FakeCibSevenTransport):
        async def start_process_instance(self, *args: Any, **kwargs: Any) -> Any:
            raise CibSevenError("engine down")

    handler = make_andre_handler(
        _FakeInference(),
        dmn=_pagto_dmn(),
        cibseven=_UnreachableCibSeven(),
        audit_sink=FakeStartAuditSink(),
    )
    # A non-worker origin so the anchor step is actually ATTEMPTED (the worker origin no-ops).
    output = await handler(replace(_pagto_envelope(), origin="autonomous-originator"))
    assert output.meta["degraded"] == "engine_inacessivel"
    assert output.meta["process_started"] == "False"


async def test_handler_meta_discloses_missing_runtime_context() -> None:
    """`receive`'s own fail-safe guards (missing identifiers / unrecognized flow) classify as
    `contexto_incompleto` — the catch-all bucket, still a BOUNDED token. (A blank TENANT cannot be
    tested through this seam at all: `DelegationEnvelope` rejects it outright, ADR-0004.)"""
    handler = make_andre_handler(
        _FakeInference(),
        dmn=_pagto_dmn(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
    )
    # Envelope stripped of every case identifier -> no idempotent PAGTO key -> fail-safe human.
    output = await handler(replace(_pagto_envelope(), payload_meta={}))
    assert output.meta["degraded"] == "contexto_incompleto"
    assert output.meta["route"] == "human_review"


async def test_handler_meta_degraded_is_empty_on_the_healthy_path() -> None:
    """The disclosure must not fire on a clean run, and it is ALWAYS one of the closed tokens."""
    handler = make_andre_handler(
        _FakeInference(),
        dmn=_pagto_dmn(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
    )
    output = await handler(_pagto_envelope())
    assert output.meta["degraded"] == ""
    assert output.meta["degraded"] in DEGRADED_TOKENS | {""}


async def test_engine_business_key_from_a_foreign_tenant_is_ignored() -> None:
    """ADR-0004: a threaded key that does not carry THIS tenant's `PAGTO-{tenant}-` prefix is not
    trusted — the derivation takes over, so a planted key can never anchor another tenant's case."""
    assert (
        pagto_task_id("amh", ordem_pagamento_id="OP-001", business_key="PAGTO-outra-op-X")
        == "PAGTO-amh-OP-001"
    )
    envelope = _pagto_envelope(business_key="PAGTO-outra-op-X")
    assert envelope.task_id == "PAGTO-amh-OP-001"
    assert "engine_business_key" not in envelope.payload_meta
    assert _business_key(state_from_envelope(envelope)) == "PAGTO-amh-OP-001"
