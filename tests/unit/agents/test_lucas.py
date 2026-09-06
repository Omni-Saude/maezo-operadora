"""Unit tests for Lucas's REAL graph (T1.12 — atendimento e cobranca ao beneficiario).

Every node is exercised against fakes: `FakeDmnTransport` (never the local XML evaluator, ADR-
0028), `FakeCibSevenTransport` (never a fabricated process instance), and small in-file fakes for
inference/WhatsApp (no LLM SDK, no network). No live-engine integration suite ships with this
task — see the PR body for the honest disclosure of what live evidence (if any) was gathered.

Covers: J1 (cobranca_info), J2 (confirmacao_pagamento), J3 (inadimplencia/cancelamento) end to
end; one guard test per L0 invariant in `graph.py`'s module docstring; and the two "helena-class"
probes this task's charter calls out by name — a fail-closed "classification" guard
(`receive`'s `intencao` gate) and a tainted-value-never-reaches-engine-variables guard (adapted
from Helena's T1.11 R1 cycle-2 CPF-leak regression to Lucas's actual architecture: Lucas has no
LLM-driven structured extraction step, so the closest analogous untrusted-value surface is the
`lucas_billing_admissibility` DMN's own `roteamento` output).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from maezo.agents.lucas.graph import (
    AdmissibilidadeCobranca,
    LucasGraph,
    LucasState,
    MotivoCategoria,
    RoteamentoEscalacao,
    Route,
    _business_key,
    build,
)
from maezo.tools.mcp_cibseven.transport import CibSevenError, FakeCibSevenTransport, ProcessInstance
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

_AGENTS_ROOT = Path(__file__).parent.parent.parent.parent / "spec" / "agents"


class _FakeInference:
    """Deterministic, in-order fake — never a real LLM SDK call."""

    def __init__(self, responses: list[str] | None = None) -> None:
        self._responses = list(responses) if responses else []
        self.calls: list[tuple[str, bool]] = []
        #: AF-12: the `task_kind` of each call, in order. Recorded so a test can assert Lucas
        #: routes the dossier narrative differently from the informational message.
        self.task_kinds: list[str | None] = []

    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        task_kind: str | None = None,
    ) -> str:
        self.calls.append((prompt, phi))
        self.task_kinds.append(task_kind)
        return self._responses.pop(0) if self._responses else "texto padrao"


class _RaisingInference:
    """LLM seam that always raises — proves every drafting call is best-effort (never blocks
    routing/escalation)."""

    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        task_kind: str | None = None,
    ) -> str:
        raise RuntimeError("LLM provider unavailable")


class _FakeWhatsAppSender:
    """LUC-08: `send` now REQUIRES `idempotency_key` (`graph.WhatsAppSender`'s real shape) —
    recorded as the third element of each `sent` tuple so existing assertions can be extended
    without losing their original (to_hash, text) coverage."""

    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[tuple[str, str, str]] = []
        self._fail = fail

    async def send(self, to_hash: str, text: str, *, idempotency_key: str) -> dict[str, Any]:
        if self._fail:
            raise RuntimeError("transport down")
        self.sent.append((to_hash, text, idempotency_key))
        return {"ok": True}


def _base_state(**overrides: Any) -> LucasState:
    state: LucasState = {
        "tenant_id": "amh",
        "conversation_id": "wa:amh:deadbeef",
        "canal": "whatsapp",
        "beneficiario_pseudo_id": "pseudo-123",
        "to_hash": "deadbeef",
        "intencao": "cobranca_info",
        "tipo_solicitacao": "boleto",
    }
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def _graph(
    *,
    inference: Any | None = None,
    dmn: FakeDmnTransport | None = None,
    cibseven: FakeCibSevenTransport | None = None,
    audit_sink: Any | None = None,
    whatsapp: Any | None = None,
) -> LucasGraph:
    return LucasGraph(
        inference=inference or _FakeInference(),
        dmn=dmn or FakeDmnTransport(),
        cibseven=cibseven or FakeCibSevenTransport(),
        audit_sink=audit_sink or FakeStartAuditSink(),
        whatsapp=whatsapp or _FakeWhatsAppSender(),
    )


def _record_start(cibseven: FakeCibSevenTransport) -> list[dict[str, Any]]:
    """Wrap `start_process_instance` to capture the ENGINE-BOUND variables dict, mirroring
    Helena's `_RecordingCibSeven` pattern (`tests/unit/agents/test_helena.py`)."""
    recording: list[dict[str, Any]] = []
    original = cibseven.start_process_instance

    async def _recording_start(
        process_key: str, business_key: str, variables: dict[str, Any]
    ) -> ProcessInstance:
        recording.append(dict(variables))
        return await original(process_key, business_key, variables)

    cibseven.start_process_instance = _recording_start  # type: ignore[method-assign]
    return recording


# ---------------------------------------------------------------------------
# spec/agent.yaml sanity (unchanged contract surface)
# ---------------------------------------------------------------------------


def test_lucas_agent_yaml_exists() -> None:
    agent_path = _AGENTS_ROOT / "lucas" / "agent.yaml"
    assert agent_path.exists(), f"Lucas agent.yaml not found at {agent_path}"


def test_lucas_agent_yaml_has_required_fields() -> None:
    agent_path = _AGENTS_ROOT / "lucas" / "agent.yaml"
    with open(agent_path) as f:
        data = yaml.safe_load(f)
    assert data is not None
    assert data["id"] == "lucas"
    assert data["name"] == "Lucas Ferreira"
    assert "role" in data
    assert "tools" in data
    assert data.get("phase") == 2


# ---------------------------------------------------------------------------
# build(config) — fail-closed contract
# ---------------------------------------------------------------------------


def test_build_requires_all_dependencies() -> None:
    with pytest.raises(ValueError, match="missing required dependencies"):
        build({})


def test_build_with_full_config_compiles() -> None:
    graph = build(
        {
            "inference": _FakeInference(),
            "dmn": FakeDmnTransport(),
            "cibseven": FakeCibSevenTransport(),
            "audit_sink": FakeStartAuditSink(),
            "whatsapp": _FakeWhatsAppSender(),
        }
    )
    compiled = graph.compile()
    node_names = {n for n in compiled.get_graph().nodes if n not in ("__start__", "__end__")}
    assert {
        "receive",
        "gather",
        "assess",
        "respond_member",
        "escalate_human",
        "start_process",
        "complete",
    } <= node_names


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_business_key_format() -> None:
    state = _base_state(tenant_id="amh", conversation_id="wa:amh:abc123")
    assert _business_key(state) == "ESC-amh-wa:amh:abc123"


# ---------------------------------------------------------------------------
# receive — fail-closed guards
# ---------------------------------------------------------------------------


async def test_receive_missing_runtime_context_escalates_falha_tecnica() -> None:
    graph = _graph()
    result = await graph.receive({"intencao": "cobranca_info"})
    assert result["route"] == "escalate_human"
    assert result["motivo_humano"] == "falha_tecnica"
    # Truthiness matters post-F2: `error` is now ALWAYS present (reset to "") — the guard's own
    # real message must survive the reset merge.
    assert result["error"]


async def test_receive_unknown_intencao_escalates_ambiguidade_never_silent_default() -> None:
    """Helena-class probe #1 (fail-closed "classification"): Lucas has no free-text classify
    step — `intencao` arrives already set — but an unrecognized/missing value must NEVER be
    silently read as J1 (`cobranca_info`), the same discipline Helena's `_classify_llm` enforces
    for a classifier failure (T1.11 R1 cycle-1)."""
    graph = _graph()
    result = await graph.receive(_base_state(intencao="pagar_agora"))  # type: ignore[typeddict-item]
    assert result["route"] == "escalate_human"
    assert result["motivo_humano"] == "ambiguidade"
    # Engine-variable hygiene: the raw offending value is NEVER echoed into the error text.
    assert "pagar_agora" not in result["error"]


async def test_receive_missing_intencao_escalates_ambiguidade() -> None:
    graph = _graph()
    state = _base_state()
    del state["intencao"]
    result = await graph.receive(state)
    assert result["motivo_humano"] == "ambiguidade"


async def test_receive_valid_context_passes_through() -> None:
    graph = _graph()
    result = await graph.receive(_base_state())
    assert result["business_key"] == "ESC-amh-wa:amh:deadbeef"
    # R1 cycle-1 F2: receive resets every output-only field on entry (neutral resets below) —
    # a caller-planted value in any of them must not survive past this node.
    assert result["dmn_refs"] == {}
    assert result["route"] == ""
    assert result["error"] == ""
    assert result["dossier"] == {}
    assert result["process_started"] is False


# ---------------------------------------------------------------------------
# J1 — cobranca_info: boleto/2a via/vencimento (informational, never adverse)
# ---------------------------------------------------------------------------


async def test_j1_boleto_admissible_routes_respond_member() -> None:
    dmn = FakeDmnTransport()
    dmn.register(
        "lucas_billing_admissibility",
        [{"roteamento": "RESPONDER", "motivo": "Duvida informacional de boleto"}],
    )
    graph = _graph(dmn=dmn)

    result = await graph.assess(_base_state(tipo_solicitacao="boleto"))

    assert result["route"] == "respond_member"
    assert result["admissibilidade"] == "RESPONDER"
    assert result["dmn_refs"]["lucas_billing_admissibility"].startswith("lucas_billing_admissibility#")


async def test_j1_vencimento_lembrete_routes_respond_member() -> None:
    dmn = FakeDmnTransport()
    dmn.register(
        "lucas_billing_admissibility", [{"roteamento": "LEMBRETE", "motivo": "Lembrete de vencimento"}]
    )
    graph = _graph(dmn=dmn)

    result = await graph.assess(_base_state(tipo_solicitacao="vencimento"))

    assert result["route"] == "respond_member"
    assert result["admissibilidade"] == "LEMBRETE"


async def test_j1_full_turn_sends_whatsapp_never_starts_process() -> None:
    dmn = FakeDmnTransport()
    dmn.register("lucas_billing_admissibility", [{"roteamento": "RESPONDER"}])
    cibseven = FakeCibSevenTransport()
    sender = _FakeWhatsAppSender()
    inference = _FakeInference(["Aqui esta a 2a via do seu boleto."])
    compiled = (
        _graph(inference=inference, dmn=dmn, cibseven=cibseven, whatsapp=sender).compile_graph().compile()
    )

    result = await compiled.ainvoke(_base_state(tipo_solicitacao="2a_via"))

    assert result["route"] == "respond_member"
    assert result["mensagem_enviada"] is True
    assert result["desfecho"] == "resposta_informativa_enviada"
    assert sender.sent == [
        ("deadbeef", "Aqui esta a 2a via do seu boleto.", "ESC-amh-wa:amh:deadbeef:respond_member")
    ]
    assert result["process_started"] is False


# ---------------------------------------------------------------------------
# J2 — confirmacao_pagamento: Lucas READS the worker-pre-resolved conciliation, never computes it
# ---------------------------------------------------------------------------


async def test_j2_status_conciliado_true_routes_respond_member() -> None:
    dmn = FakeDmnTransport()
    dmn.register(
        "lucas_billing_admissibility", [{"roteamento": "RESPONDER", "motivo": "Pagamento conciliado"}]
    )
    graph = _graph(dmn=dmn)

    result = await graph.assess(
        _base_state(
            intencao="confirmacao_pagamento", tipo_solicitacao="status_pagamento", status_conciliado=True
        )
    )

    assert result["route"] == "respond_member"
    assert dmn.calls[0][1]["status_conciliado"] is True


async def test_j2_non_conciliated_with_ciclos_escalates_inadimplencia_detectada() -> None:
    dmn = FakeDmnTransport()
    dmn.register("lucas_billing_admissibility", [{"roteamento": "ESCALAR_HUMANO", "motivo": "atraso"}])
    dmn.register("lucas_escalation_routing", [{"roteamento": "COBRANCA_HUMANO"}])
    graph = _graph(dmn=dmn)

    result = await graph.assess(
        _base_state(
            intencao="confirmacao_pagamento",
            tipo_solicitacao="status_pagamento",
            status_conciliado=False,
            ciclos_sem_conciliacao=2,
        )
    )

    assert result["route"] == "escalate_human"
    assert result["motivo_humano"] == "inadimplencia_detectada"
    assert result["motivo_categoria"] == "outro"


async def test_j2_status_unresolved_by_worker_never_assumed_inadimplente() -> None:
    """Worker has not yet pre-resolved the conciliation (`status_conciliado=None`) — `gather`
    records the gap; the CATCH-ALL DMN row (never Python code) decides the conservative routing
    (never a false "admitted" outcome by assumption)."""
    dmn = FakeDmnTransport()
    dmn.register("lucas_billing_admissibility", [{"roteamento": "ESCALAR_HUMANO", "motivo": "catch-all"}])
    dmn.register("lucas_escalation_routing", [{"roteamento": "ATENDIMENTO_HUMANO"}])
    graph = _graph(dmn=dmn)

    gather_result = await graph.gather(
        _base_state(
            intencao="confirmacao_pagamento", tipo_solicitacao="status_pagamento", status_conciliado=None
        )
    )
    assert "conciliation status not yet pre-resolved" in gather_result["gather_notes"][0]

    assess_result = await graph.assess(
        _base_state(
            intencao="confirmacao_pagamento", tipo_solicitacao="status_pagamento", status_conciliado=None
        )
    )
    assert assess_result["route"] == "escalate_human"
    # Coerced to False for the DMN call (never a computed/guessed True) — the catch-all row (not
    # this code) is what actually decides the routing.
    assert dmn.calls[0][1]["status_conciliado"] is False


# ---------------------------------------------------------------------------
# J3 — inadimplencia | cancelamento | contestacao_cobranca | pedido_cancelamento: ALWAYS escalates
# ---------------------------------------------------------------------------


async def test_j3_intencao_always_escalates_never_calls_billing_dmn() -> None:
    """L0 guard: J3 always escalates, and never even reaches the billing DMN (no adverse
    desfecho can originate from a billing-admissibility read for this journey)."""
    dmn = FakeDmnTransport()
    dmn.register("lucas_escalation_routing", [{"roteamento": "COBRANCA_HUMANO"}])
    graph = _graph(dmn=dmn)

    result = await graph.assess(_base_state(intencao="inadimplencia", tipo_solicitacao="boleto"))

    assert result["route"] == "escalate_human"
    assert result["motivo_humano"] == "inadimplencia_detectada"
    assert [call[0] for call in dmn.calls] == ["lucas_escalation_routing"]


async def test_j3_cancelamento_escalates_pedido_cancelamento_to_contratos_humano() -> None:
    dmn = FakeDmnTransport()
    dmn.register("lucas_escalation_routing", [{"roteamento": "CONTRATOS_HUMANO"}])
    graph = _graph(dmn=dmn)

    result = await graph.assess(_base_state(intencao="cancelamento"))

    assert result["route"] == "escalate_human"
    assert result["motivo_humano"] == "pedido_cancelamento"
    assert result["grupo_humano"] == "gestao-contratos"


async def test_j3_contesta_cobranca_flag_escalates_even_under_cobranca_info_intencao() -> None:
    """A signaled contestation escalates regardless of `intencao` — never decided by Lucas."""
    dmn = FakeDmnTransport()
    dmn.register("lucas_escalation_routing", [{"roteamento": "COBRANCA_HUMANO"}])
    graph = _graph(dmn=dmn)

    result = await graph.assess(_base_state(intencao="cobranca_info", contesta_cobranca=True))

    assert result["route"] == "escalate_human"
    assert result["motivo_humano"] == "contestacao_cobranca"


async def test_j3_pedido_cancelamento_flag_escalates_even_under_cobranca_info_intencao() -> None:
    dmn = FakeDmnTransport()
    dmn.register("lucas_escalation_routing", [{"roteamento": "CONTRATOS_HUMANO"}])
    graph = _graph(dmn=dmn)

    result = await graph.assess(_base_state(intencao="cobranca_info", pedido_cancelamento=True))

    assert result["route"] == "escalate_human"
    assert result["motivo_humano"] == "pedido_cancelamento"


async def test_j3_full_turn_starts_process_and_sends_ack_never_the_adverse_text() -> None:
    dmn = FakeDmnTransport()
    dmn.register("lucas_escalation_routing", [{"roteamento": "COBRANCA_HUMANO"}])
    cibseven = FakeCibSevenTransport()
    sender = _FakeWhatsAppSender()
    inference = _FakeInference(["Resumo factual do caso.", "Um atendente humano vai continuar."])
    compiled = (
        _graph(inference=inference, dmn=dmn, cibseven=cibseven, whatsapp=sender).compile_graph().compile()
    )

    result = await compiled.ainvoke(_base_state(intencao="inadimplencia"))

    assert result["route"] == "escalate_human"
    assert result["process_started"] is True
    assert result["business_key"] == "ESC-amh-wa:amh:deadbeef"
    assert sender.sent == [
        ("deadbeef", "Um atendente humano vai continuar.", "ESC-amh-wa:amh:deadbeef:send_escalation_ack")
    ]
    for _to_hash, text, _idempotency_key in sender.sent:
        assert "suspens" not in text.lower()
        assert "cancelad" not in text.lower()
        assert "negad" not in text.lower()


# ---------------------------------------------------------------------------
# DMN fail-safe (never fail-open)
# ---------------------------------------------------------------------------


async def test_assess_billing_dmn_unavailable_escalates_dmn_indisponivel() -> None:
    dmn = FakeDmnTransport()  # `lucas_billing_admissibility` deliberately NOT registered
    dmn.register("lucas_escalation_routing", [{"roteamento": "ATENDIMENTO_HUMANO"}])
    graph = _graph(dmn=dmn)

    result = await graph.assess(_base_state())

    assert result["route"] == "escalate_human"
    assert result["motivo_humano"] == "dmn_indisponivel"
    assert "lucas_billing_admissibility" in result["dmn_error"]


async def test_assess_escalation_routing_dmn_unavailable_still_escalates_case_never_lost() -> None:
    """The case is NEVER dropped even when the SUGGESTION table itself is unavailable — the
    conservative catch-all group is used and `route` stays `escalate_human`."""
    dmn = FakeDmnTransport()
    dmn.register("lucas_billing_admissibility", [{"roteamento": "ESCALAR_HUMANO"}])
    # `lucas_escalation_routing` deliberately NOT registered -> raises inside `_assess_escalation`.
    graph = _graph(dmn=dmn)

    result = await graph.assess(_base_state())

    assert result["route"] == "escalate_human"
    assert result["grupo_humano"] == "atendimento-humano"
    assert "roteamento_escalacao" not in result


async def test_assess_ambiguous_dmn_roteamento_value_never_leaked_into_engine_variables() -> None:
    """Helena-class probe #2 (adapted): a tainted/untrusted value returned by the
    `lucas_billing_admissibility` DMN table (simulating a compromised or misconfigured
    per-tenant deployment, ADR-0004/ADR-0028) — outside the allowlist — must escalate via a
    CLASS TOKEN ONLY (`ambiguidade`). No fragment of the offending value may reach the
    ENGINE-BOUND SP-OP-ESCALATION-001 process variables (mirrors Helena's T1.11 R1 cycle-2
    regression test `test_cpf_bearing_field_value_never_reaches_engine_variables`)."""
    leaked_value = "CPF 123.456.789-00 dor"
    dmn = FakeDmnTransport()
    dmn.register("lucas_billing_admissibility", [{"roteamento": leaked_value}])
    dmn.register("lucas_escalation_routing", [{"roteamento": "ATENDIMENTO_HUMANO"}])
    cibseven = FakeCibSevenTransport()
    recording = _record_start(cibseven)
    inference = _FakeInference(["resumo factual", "um humano vai continuar"])
    compiled = _graph(inference=inference, dmn=dmn, cibseven=cibseven).compile_graph().compile()

    result = await compiled.ainvoke(_base_state())

    assert result["route"] == "escalate_human"
    assert result["motivo_humano"] == "ambiguidade"

    assert recording, "the escalation must have been started"
    serialized = json.dumps(recording[0], ensure_ascii=False, default=str)
    for fragment in ("123.456.789-00", "123.456.789", "456.789", "CPF"):
        assert fragment not in serialized, (
            f"engine-bound variables leaked a fragment of the offending DMN-returned value "
            f"({fragment!r}): {serialized}"
        )
    assert recording[0]["motivo_encaminhamento"] == "ambiguidade"


async def test_llm_failure_never_blocks_routing_or_escalation() -> None:
    """Fail-closed classification companion: an LLM (drafting) failure NEVER changes `route` —
    routing is 100% DMN-derived (ADR-0012) before any LLM call runs, so a drafting failure can
    only degrade prose quality, never fail open into an unrouted/adverse state."""
    dmn = FakeDmnTransport()
    dmn.register("lucas_escalation_routing", [{"roteamento": "COBRANCA_HUMANO"}])
    cibseven = FakeCibSevenTransport()
    sender = _FakeWhatsAppSender()
    compiled = (
        _graph(inference=_RaisingInference(), dmn=dmn, cibseven=cibseven, whatsapp=sender)
        .compile_graph()
        .compile()
    )

    result = await compiled.ainvoke(_base_state(intencao="inadimplencia"))

    assert result["route"] == "escalate_human"
    assert result["process_started"] is True
    assert sender.sent  # fail-safe canned ack still delivered


# ---------------------------------------------------------------------------
# Idempotent start_process
# ---------------------------------------------------------------------------


async def test_escalate_is_idempotent_on_active_instance() -> None:
    cibseven = FakeCibSevenTransport()
    business_key = "ESC-amh-wa:amh:deadbeef"
    cibseven.seed_instance(
        ProcessInstance(
            instance_id="existing-1",
            process_key="SP-OP-ESCALATION-001",
            business_key=business_key,
            state="ACTIVE",
            already_existed=True,
        )
    )
    graph = _graph(cibseven=cibseven)

    result = await graph.start_process(
        _base_state(route="escalate_human", motivo_humano="inadimplencia_detectada", motivo_categoria="outro")
    )

    assert result["process_ref"]["instance_id"] == "existing-1"
    assert result["process_ref"]["already_existed"] is True


async def test_start_process_records_error_on_cibseven_failure_but_never_loses_route() -> None:
    class _FailingCibSeven(FakeCibSevenTransport):
        async def start_process_instance(
            self, process_key: str, business_key: str, variables: dict[str, Any]
        ) -> ProcessInstance:
            raise CibSevenError("engine unreachable")

    graph = _graph(cibseven=_FailingCibSeven())
    result = await graph.start_process(
        _base_state(route="escalate_human", motivo_humano="falha_tecnica", motivo_categoria="falha_tecnica")
    )

    assert result["process_started"] is False
    assert "start_process indisponivel" in result["error"]


# ---------------------------------------------------------------------------
# L0 structural guards
# ---------------------------------------------------------------------------


def test_route_type_admits_no_adverse_variant() -> None:
    """L0 hard, CI-enforced (mirrors `spec/processes/dmn/lucas_billing_admissibility.dmn`'s own
    `<description>` and the v1 donor's `test_route_type_admits_no_adverse_variant`): `Route`
    admits ONLY `respond_member`/`escalate_human` — no cancel/suspend/deny/rescind variant."""
    assert set(Route.__args__) == {"respond_member", "escalate_human"}  # type: ignore[attr-defined]


def test_admissibilidade_cobranca_type_admits_no_adverse_variant() -> None:
    assert set(AdmissibilidadeCobranca.__args__) == {"RESPONDER", "LEMBRETE", "ESCALAR_HUMANO"}  # type: ignore[attr-defined]


def test_roteamento_escalacao_type_admits_only_human_groups() -> None:
    """Every destination is a HUMAN GROUP — none is an adverse outcome."""
    assert set(RoteamentoEscalacao.__args__) == {  # type: ignore[attr-defined]
        "ATENDIMENTO_HUMANO",
        "COBRANCA_HUMANO",
        "CONTRATOS_HUMANO",
    }


def test_motivo_categoria_never_clinical() -> None:
    """L0 hard: Lucas is billing/collection, never clinical — `motivo_categoria` never admits
    the clinical categories from the shared SP-OP-ESCALATION-001 domain
    (`red_flag_clinico`/`risco_psicossocial`/`intencao_clinica`)."""
    allowed = set(MotivoCategoria.__args__)  # type: ignore[attr-defined]
    assert allowed == {"outro", "solicitacao_humano", "falha_tecnica"}
    assert allowed.isdisjoint({"red_flag_clinico", "risco_psicossocial", "intencao_clinica"})


async def test_conciliation_facts_passed_through_unchanged_never_recomputed() -> None:
    """L0 guard (mirrors `tests/unit/sec/test_dentro_teto_source.py`'s spirit for a different
    worker-pre-resolved fact): `status_conciliado`/`ciclos_sem_conciliacao` are consumed
    VERBATIM in the DMN call — Lucas never reconciles/derives one from the other."""
    dmn = FakeDmnTransport()
    dmn.register("lucas_billing_admissibility", [{"roteamento": "RESPONDER"}])
    graph = _graph(dmn=dmn)

    # A deliberately "contradictory-looking" combination (conciliado=True but ciclos>0) — Lucas
    # must NOT infer/flip either value; both must reach the DMN exactly as given in state.
    await graph.assess(
        _base_state(
            intencao="confirmacao_pagamento",
            tipo_solicitacao="status_pagamento",
            status_conciliado=True,
            ciclos_sem_conciliacao=3,
        )
    )

    admis_call = next(call for call in dmn.calls if call[0] == "lucas_billing_admissibility")
    assert admis_call[1]["status_conciliado"] is True
    assert admis_call[1]["ciclos_sem_conciliacao"] == 3


async def test_dossier_and_message_structural_guardrails_always_none() -> None:
    """Structural guardrails: a value here would be a detectable bug (mirrors Rafael's
    `decisao_cobertura` guardrail)."""
    graph = _graph(dmn=FakeDmnTransport())
    mensagem = await graph._build_message(_base_state(admissibilidade="RESPONDER"))
    assert mensagem["comunicacao_suspensao"] is None
    assert mensagem["comunicacao_cancelamento"] is None

    dossier = await graph._build_dossier(_base_state(motivo_humano="inadimplencia_detectada"))
    assert dossier["decisao_cancelamento"] is None


async def test_no_adverse_desfecho_ever_originates_from_lucas_across_all_journeys() -> None:
    """Cross-journey L0 guard: no `desfecho` string emitted by any node contains an adverse verb
    stem — Lucas responds/reminds or escalates, never cancels/suspends/denies."""
    forbidden = ("cancel", "suspend", "suspens", "negad", "rescind", "denied", "denies")
    dmn = FakeDmnTransport()
    dmn.register("lucas_billing_admissibility", [{"roteamento": "RESPONDER"}])
    dmn.register("lucas_escalation_routing", [{"roteamento": "ATENDIMENTO_HUMANO"}])
    sender = _FakeWhatsAppSender()

    for intencao, extra in (
        ("cobranca_info", {}),
        ("confirmacao_pagamento", {"status_conciliado": True}),
        ("inadimplencia", {}),
        ("cancelamento", {}),
    ):
        compiled = (
            _graph(dmn=dmn, cibseven=FakeCibSevenTransport(), whatsapp=sender, inference=_FakeInference())
            .compile_graph()
            .compile()
        )
        result = await compiled.ainvoke(_base_state(intencao=intencao, **extra))
        desfecho = str(result.get("desfecho", "")).lower()
        for stem in forbidden:
            assert stem not in desfecho, f"intencao={intencao} desfecho={desfecho!r} contains {stem!r}"


# ---------------------------------------------------------------------------
# R1 cycle-1 regressions — F1 (fail-OPEN case loss) + F2 (output-field passthrough)
# ---------------------------------------------------------------------------


async def test_planted_error_j3_full_turn_still_starts_real_escalation() -> None:
    """R1 cycle-1 F1 regression (the verifier's exact fail-OPEN case): a J3 turn arriving with a
    CALLER-planted `error` short-circuited gather/assess (no `route` stamp); `_route`'s
    conservative default still ran `escalate_human` (dossier built, beneficiary ACK SENT) but the
    old `start_process` gate (`route != "escalate_human"` -> skip) then silently dropped the
    start — beneficiary promised a human, ZERO engine instances (silent case loss). Post-fix the
    case ALWAYS reaches a REAL recorded start: `receive` resets the planted `error` (F2),
    `escalate_human` stamps `route` authoritatively (F1a), and `start_process` fails CLOSED on
    any non-respond route (F1b) — three independent layers, each sufficient alone."""
    dmn = FakeDmnTransport()
    dmn.register("lucas_escalation_routing", [{"roteamento": "COBRANCA_HUMANO"}])
    cibseven = FakeCibSevenTransport()
    recording = _record_start(cibseven)
    sender = _FakeWhatsAppSender()
    compiled = _graph(dmn=dmn, cibseven=cibseven, whatsapp=sender).compile_graph().compile()

    result = await compiled.ainvoke(_base_state(intencao="inadimplencia", error="caller planted error"))

    assert result["route"] == "escalate_human"
    assert result["process_started"] is True
    assert recording, "a REAL start must be recorded on the transport — never a silent skip"
    assert recording[0]["motivo_encaminhamento"] == "inadimplencia_detectada"
    assert sender.sent, "the 'a human will continue' promise must be backed by an actual process"


async def test_escalate_human_stamps_route_authoritatively() -> None:
    """R1 cycle-1 F1a: the node that performs the human handoff is the authority on the fact
    that a handoff is happening — its output ALWAYS carries route="escalate_human", regardless
    of what upstream did (or failed to) stamp."""
    graph = _graph()
    out = await graph.escalate_human(_base_state(motivo_humano="inadimplencia_detectada"))
    assert out["route"] == "escalate_human"

    # Even with NO motivo in state (anomalous arrival), the stamp holds and the motivo defaults
    # to the falha_tecnica class token — never empty engine-bound fields.
    out_anomalous = await graph.escalate_human(_base_state())
    assert out_anomalous["route"] == "escalate_human"
    assert out_anomalous["motivo_humano"] == "falha_tecnica"
    assert out_anomalous["motivo_categoria"] == "falha_tecnica"


async def test_start_process_fail_closed_on_unset_route_starts_escalation() -> None:
    """R1 cycle-1 F1b: an unset/unknown `route` at start_process is treated as escalation-bound
    (mirrors `_route`'s own conservative default) — the start happens; NEVER a silent
    process_started=False skip."""
    cibseven = FakeCibSevenTransport()
    recording = _record_start(cibseven)
    graph = _graph(cibseven=cibseven)

    result = await graph.start_process(_base_state())  # no `route` key at all

    assert result["process_started"] is True
    assert recording, "unset route must fail CLOSED into a real start, never a silent skip"


async def test_start_process_never_fabricates_outro_or_moderada_when_unresolved() -> None:
    """LUCAS-MOTIVO-SEVERIDADE-DEFAULTS: mirrors GAP-ESC-SEVERITY-GROUP's own principle one layer
    up. `_base_state()` never sets `motivo_categoria`/`severidade` (as if `assess`/
    `escalate_human`/`_escalate_min` never ran — a state-machine invariant violation every real
    path already prevents). Both are Literal-typed contract fields; an unresolved value must ride
    through verbatim (`""`, since `receive`'s own reset never runs on a hand-built dict either),
    never be laundered into the well-formed-looking `"outro"`/`"moderada"` tokens the ALREADY-
    fixed worker boundary (`escalation.py`) would otherwise trust as real."""
    cibseven = FakeCibSevenTransport()
    recording = _record_start(cibseven)
    graph = _graph(cibseven=cibseven)

    result = await graph.start_process(_base_state())  # no `route`/`motivo_categoria`/`severidade`

    assert result["process_started"] is True
    assert recording, "unresolved contract fields must still fail CLOSED into a real start"
    assert recording[0]["motivo_categoria"] == ""
    assert recording[0]["severidade"] == ""


async def test_start_process_skips_only_on_explicit_respond_member_route() -> None:
    """The informational path is the ONLY one that never opens a process — and it must be
    EXPLICIT (`route == "respond_member"`), never inferred from absence."""
    cibseven = FakeCibSevenTransport()
    recording = _record_start(cibseven)
    graph = _graph(cibseven=cibseven)

    result = await graph.start_process(_base_state(route="respond_member"))

    assert result["process_started"] is False
    assert not recording


def test_escalate_min_clears_dmn_refs() -> None:
    """R1 cycle-1 F2 (verifier's exact probe surface): the pre-DMN shortcut explicitly empties
    `dmn_refs` — nothing may pose as DMN provenance on a path where no DMN ran."""
    out = LucasGraph._escalate_min("ambiguidade", error="x")
    assert out["dmn_refs"] == {}


async def test_caller_planted_output_fields_never_reach_engine_variables() -> None:
    """R1 cycle-1 F2 regression (the verifier's exact class, incl. its live probe: a
    'SUSPENDER CPF=...' value planted in `dmn_refs` reached `dmn_decision_refs` via the natural
    `ambiguidade` path): one unique sentinel per OUTPUT-ONLY state field, exercised across all
    journeys AND both skip-assess shortcuts — no sentinel fragment may reach the engine-bound
    SP-OP-ESCALATION-001 variables. `receive`'s `_OUTPUT_FIELDS_RESET` is the fix under test."""
    planted: dict[str, Any] = {
        "gathered": True,
        "billing_facts": {"x": "SENT_billing_facts"},
        "gather_notes": ["SENT_gather_notes"],
        "admissibilidade": "SENT_admissibilidade",
        "roteamento_escalacao": "SENT_roteamento_escalacao",
        "dmn_refs": {"planted": "SENT_dmn_refs SUSPENDER CPF 123.456.789-00"},
        "dmn_error": "SENT_dmn_error",
        "route": "SENT_route",
        "motivo_humano": "SENT_motivo_humano",
        "motivo_categoria": "SENT_motivo_categoria",
        "severidade": "SENT_severidade",
        "grupo_humano": "SENT_grupo_humano",
        "mensagem": {"texto": "SENT_mensagem"},
        "mensagem_enviada": True,
        "dossier": {"narrativa": "SENT_dossier"},
        "process_started": True,
        "business_key": "SENT_business_key",
        "process_ref": {"instance_id": "SENT_process_ref"},
        "desfecho": "SENT_desfecho",
        "error": "SENT_error",
    }

    dmn = FakeDmnTransport()
    dmn.register("lucas_billing_admissibility", [{"roteamento": "RESPONDER"}])
    dmn.register("lucas_escalation_routing", [{"roteamento": "ATENDIMENTO_HUMANO"}])

    scenarios: list[tuple[str, dict[str, Any]]] = [
        ("j1_respond", {**_base_state(intencao="cobranca_info"), **planted}),
        (
            "j2_conciliado",
            {**_base_state(intencao="confirmacao_pagamento", status_conciliado=True), **planted},
        ),
        ("j3_inadimplencia", {**_base_state(intencao="inadimplencia"), **planted}),
        # Skip-assess shortcut 1: unknown intencao -> `ambiguidade` (_escalate_min, no DMN ran —
        # the verifier's live probe path for the planted dmn_refs).
        ("shortcut_ambiguidade", {**_base_state(), **planted, "intencao": "SENT_intencao"}),
        # Skip-assess shortcut 2: missing runtime context -> `falha_tecnica` (_escalate_min).
        (
            "shortcut_missing_context",
            {**{k: v for k, v in _base_state().items() if k != "tenant_id"}, **planted},
        ),
    ]

    for label, state in scenarios:
        cibseven = FakeCibSevenTransport()
        recording = _record_start(cibseven)
        compiled = (
            _graph(dmn=dmn, cibseven=cibseven, whatsapp=_FakeWhatsAppSender(), inference=_FakeInference())
            .compile_graph()
            .compile()
        )
        await compiled.ainvoke(state)  # type: ignore[arg-type]

        for variables in recording:
            serialized = json.dumps(variables, ensure_ascii=False, default=str)
            for fragment in ("SENT_", "SUSPENDER", "123.456.789-00", "CPF"):
                assert fragment not in serialized, (
                    f"[{label}] engine-bound variables leaked a caller-planted output-field "
                    f"fragment ({fragment!r}): {serialized}"
                )
        if label == "shortcut_ambiguidade":
            # The verifier's exact probe: on the no-DMN shortcut, NO dmn provenance key may even
            # exist (dmn_refs was planted; the reset + _escalate_min clear must strip it).
            assert recording, "ambiguidade shortcut must still escalate (start recorded)"
            assert "dmn_decision_refs" not in recording[0]
            assert "dmn_decision_ref" not in recording[0]


# ---------------------------------------------------------------------------
# CC-06 — the dossier narrative Lucas copies into `resumo_contexto` is scrubbed at the chokepoint
# ---------------------------------------------------------------------------


async def test_dossier_narrativa_identifiers_never_reach_the_engine_variables() -> None:
    """CC-06 (INFO-A's Lucas twin): `_escalation_variables` copies `dossier["narrativa"]` — free
    LLM text — into BOTH the contractual `resumo_contexto` and the `dossie_lucas` annotation, and
    `start_process_idempotent` shipped them to the engine VERBATIM. An identifier the model copies
    out of the case must be gone from every engine-bound variable, while the summary itself (the
    thing the human attendant reads) survives. Synthetic identifiers only."""
    cpf = "123.456.789-09"
    email = "beneficiario.teste@exemplo.com.br"
    cibseven = FakeCibSevenTransport()
    recording = _record_start(cibseven)
    graph = _graph(cibseven=cibseven)

    result = await graph.start_process(
        _base_state(
            route="escalate_human",
            motivo_humano="inadimplencia_detectada",
            motivo_categoria="outro",
            dossier={
                "prompt_version": "dossier@v1",
                "tipo": "dossie_escalacao",
                "narrativa": f"Beneficiario contestou a cobranca; informou CPF {cpf} e e-mail {email}.",
                "fatos": {"competencia": "2026-08", "numero_boleto": "34191790010104351004791020"},
                "decisao_cancelamento": None,
            },
        )
    )

    assert result["process_started"] is True
    assert recording, "the escalation must have been started"
    serialized = json.dumps(recording[0], ensure_ascii=False, default=str)
    for fragment in (cpf, email, "123.456.789", "456.789-09"):
        assert fragment not in serialized, (
            f"engine-bound variables leaked {fragment!r} from the dossier narrative: {serialized}"
        )
    # Both copies of the narrative are covered — the contractual variable AND the annotation.
    assert "Beneficiario contestou a cobranca" in recording[0]["resumo_contexto"]
    assert "[REDACTED_DIGITS]" in recording[0]["resumo_contexto"]
    assert "[REDACTED_EMAIL]" in recording[0]["dossie_lucas"]["narrativa"]
    # Structured facts survive untouched (the boleto number is a long digit run BY CONSTRUCTION).
    assert recording[0]["dossie_lucas"]["fatos"]["numero_boleto"] == "34191790010104351004791020"
    assert recording[0]["dossie_lucas"]["decisao_cancelamento"] is None


# ---------------------------------------------------------------------------
# LUC-08 — outbound idempotency key (gap `IDEMPOTENCY-KEY-MISSING`)
# ---------------------------------------------------------------------------


class _RecordingWhatsAppWithKey:
    """Dedicated fake for the LUC-08 tests below — kept separate from `_FakeWhatsAppSender`
    (whose signature/assertions are updated together with the `graph.py` fix itself) so these
    tests pin ONLY the new `idempotency_key` behaviour, never anything about the send path this
    task did not touch."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    async def send(self, to_hash: str, text: str, *, idempotency_key: str) -> dict[str, Any]:
        self.sent.append((to_hash, text, idempotency_key))
        return {"ok": True}


async def test_replayed_respond_member_turn_uses_the_same_idempotency_key_each_time() -> None:
    """LUC-08: an engine re-delivery of the SAME turn must claim the SAME key on the durable
    store, never a fresh one — a fresh key per attempt would never dedupe anything. The key is
    derived ONLY from `business_key` (stable — `tenant_id`/`conversation_id`) and the node name,
    never from anything that changes across a replay (no timestamp, no random id)."""
    dmn = FakeDmnTransport()
    dmn.register("lucas_billing_admissibility", [{"roteamento": "RESPONDER"}])
    sender = _RecordingWhatsAppWithKey()
    state = _base_state(tipo_solicitacao="2a_via")

    for _ in range(2):
        compiled = (
            _graph(dmn=dmn, cibseven=FakeCibSevenTransport(), whatsapp=sender, inference=_FakeInference())
            .compile_graph()
            .compile()
        )
        await compiled.ainvoke(state)

    assert len(sender.sent) == 2, "both replays must have actually sent through the fake"
    first_key = sender.sent[0][2]
    second_key = sender.sent[1][2]
    assert first_key == second_key == "ESC-amh-wa:amh:deadbeef:respond_member"


async def test_replayed_escalation_ack_turn_uses_the_same_idempotency_key_each_time() -> None:
    """Same property as above, for `send_escalation_ack` — the OTHER outbound call site LUC-08
    closes. Node name differs (`send_escalation_ack`, not `respond_member`), so a beneficiary
    with BOTH an informational reminder and an escalation in flight never has one send's key
    collide with — and wrongly suppress — the other."""
    dmn = FakeDmnTransport()
    dmn.register("lucas_escalation_routing", [{"roteamento": "COBRANCA_HUMANO"}])
    sender = _RecordingWhatsAppWithKey()
    state = _base_state(intencao="inadimplencia")

    for _ in range(2):
        compiled = (
            _graph(dmn=dmn, cibseven=FakeCibSevenTransport(), whatsapp=sender, inference=_FakeInference())
            .compile_graph()
            .compile()
        )
        await compiled.ainvoke(state)

    assert len(sender.sent) == 2, "both replays must have actually sent the ack through the fake"
    first_key = sender.sent[0][2]
    second_key = sender.sent[1][2]
    assert first_key == second_key == "ESC-amh-wa:amh:deadbeef:send_escalation_ack"
