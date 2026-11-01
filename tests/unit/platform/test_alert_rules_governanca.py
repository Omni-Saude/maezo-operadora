"""R-085 / F-090: os alertas de governanca prometidos por ADR-0010 e pelo runbook devops-stack.md
existem, leem metricas com emissor VIVO, usam o limiar da FONTE citada e nao carregam PHI em rotulo.

Cada prova comportamental abaixo falha se o incremento correspondente for removido do codigo de
producao (`gateway.seams._base.gate`, `a2a.dispatcher._finish_handler`)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Final

import pytest
import yaml

from maezo.a2a import Budget, DelegationEnvelope
from tests.unit.a2a.fakes import FakeAgentHandler, build_test_dispatcher, make_card

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_ALERT_RULES: Final[Path] = _REPO_ROOT / "deploy" / "observability" / "alert-rules.yml"
_GROUP: Final[str] = "maezo_governanca_agentes"

#: Rotulos que denunciariam PHI/identificador de negocio num rotulo Prometheus (ADR-0006).
_FORBIDDEN_LABEL_FRAGMENTS: Final[tuple[str, ...]] = (
    "cpf",
    "cns",
    "beneficiario",
    "paciente",
    "patient",
    "phone",
    "telefone",
    "conversation",
    "thread",
    "business_key",
    "task_id",
    "payload",
    "operation",
    "nome",
)


#: Rotulos anexados na RASPAGEM (deploy/observability/otel-collector-ecs-dev.yaml), nao declarados
#: no contador — os unicos aceitos num `by (...)`/`on (...)` deste grupo alem dos do contador.
_SCRAPE_LABELS: Final[frozenset[str]] = frozenset({"environment"})


def _rules() -> dict[str, dict[str, Any]]:
    document: Any = yaml.safe_load(_ALERT_RULES.read_text(encoding="utf-8"))
    (group,) = [g for g in document["groups"] if g["name"] == _GROUP]
    return {rule["alert"]: rule for rule in group["rules"]}


def _threshold(expr: str) -> float:
    match = re.search(r">\s*([0-9.]+)\s*$", expr.strip())
    assert match, expr
    return float(match.group(1))


def _labelnames(metric: str) -> tuple[str, ...]:
    from maezo.runtime.metrics import MetricsCollector

    collector = MetricsCollector()
    for attr in ("gateway_pep_decision", "a2a_guard_refusal", "agent_desfecho_total"):
        counter = getattr(collector, attr)
        if counter._name == metric.removesuffix("_total"):
            return tuple(counter._labelnames)
    raise AssertionError(metric)


def _sample(name: str, **labels: str) -> float:
    from maezo.platform.observability import get_metrics_collector

    value = get_metrics_collector().registry.get_sample_value(name, labels or None)
    return float(value or 0.0)


# --- regras ---------------------------------------------------------------------------------


def test_alert_rules_governanca_group_ships_exactly_the_three_sourced_alerts() -> None:
    assert set(_rules()) == {"MaezoPEPDenySpike", "MaezoA2ABudgetExhausted", "MaezoEscalationRateHigh"}


def test_alert_rules_pep_deny_spike_uses_runbook_threshold_and_only_enforced_denies() -> None:
    rule = _rules()["MaezoPEPDenySpike"]
    expr = rule["expr"]
    assert "maezo_gateway_pep_decision_total" in expr
    assert 'decision="deny"' in expr
    # Recusa-sombra (WOULD_DENY) nunca pagina: com o manifesto DRAFT toda classe nega em sombra.
    assert 'enforced="true"' in expr
    assert _threshold(expr) == 1.0  # devops-stack.md §6 "PEP deny spike": > 1 req/s
    assert rule["annotations"]["runbook_url"] == "docs/runbooks/alerts/MaezoPEPDenySpike.md"


def test_alert_rules_a2a_budget_exhausted_fires_on_the_event_itself() -> None:
    rule = _rules()["MaezoA2ABudgetExhausted"]
    expr = rule["expr"]
    assert "maezo_a2a_guard_refusal_total" in expr
    assert 'guard="budget_exhausted"' in expr
    assert _threshold(expr) == 0.0  # ADR-0010 nomeia o evento, nao uma taxa
    assert rule["annotations"]["runbook_url"] == "docs/runbooks/alerts/MaezoA2ABudgetExhausted.md"


def test_alert_rules_escalation_rate_uses_runbook_threshold_and_window() -> None:
    rule = _rules()["MaezoEscalationRateHigh"]
    expr = rule["expr"]
    assert 'maezo_agent_desfecho_total{desfecho="escalado_humano"}[1h]' in expr
    assert "maezo_agent_desfecho_total[1h]" in expr
    # R-085 (dono 2026-10-31): "volume minimo + 2 janelas, agregado por agente e ambiente".
    assert "sum by (agent_id)" not in expr
    assert expr.count("sum by (agent_id, environment)") == 5
    assert 'maezo_agent_desfecho_total{desfecho="escalado_humano"}[5m]' in expr
    assert "maezo_agent_desfecho_total[5m]" in expr
    # O MESMO 30% sourced nas duas janelas (devops-stack.md §6 "Escalation rate": > 30% / 1h).
    assert [float(t) for t in re.findall(r"\)\s*>\s*([0-9.]+)", expr)] == [0.30, 0.30]
    # Volume minimo na janela longa (parametro TODO-OWNER P6 do arquivo).
    assert re.search(r"increase\(maezo_agent_desfecho_total\[1h\]\)\)\s*>=\s*[0-9]+", expr), expr
    assert "for" not in rule
    assert rule["annotations"]["runbook_url"] == "docs/runbooks/alerts/MaezoEscalationRateHigh.md"


@pytest.mark.parametrize(
    "metric",
    ["maezo_gateway_pep_decision_total", "maezo_a2a_guard_refusal_total", "maezo_agent_desfecho_total"],
)
def test_alert_rules_governanca_metrics_carry_no_phi_label(metric: str) -> None:
    labels = _labelnames(metric)
    assert labels, metric
    for label in labels:
        assert not any(fragment in label.lower() for fragment in _FORBIDDEN_LABEL_FRAGMENTS), (metric, label)


def test_alert_rules_governanca_labels_used_in_expr_exist_on_the_metric() -> None:
    for rule in _rules().values():
        expr = rule["expr"]
        for metric in re.findall(r"(maezo_[a-z0-9_]+_total)", expr):
            labels = set(_labelnames(metric))
            for matcher in re.findall(re.escape(metric) + r"\{([^}]*)\}", expr):
                used = {part.split("=")[0].strip() for part in matcher.split(",") if part.strip()}
                assert used <= labels, (rule["alert"], used - labels)
        metrics_in_expr = re.findall(r"(maezo_[a-z0-9_]+_total)", expr)
        for grouped in re.findall(r"(?:by|on) \(([^)]*)\)", expr):
            for label in (x.strip() for x in grouped.split(",")):
                # `environment` e rotulo de RASPAGEM (coletor ECS), nao do contador (R-085).
                if label in _SCRAPE_LABELS:
                    continue
                assert any(label in _labelnames(m) for m in metrics_in_expr), label


# --- emissores vivos (comportamento) -----------------------------------------------------------


@pytest.mark.asyncio
async def test_alert_rules_gate_counts_one_pep_decision_with_bounded_labels() -> None:
    from maezo.gateway.seams._base import SeamContext, gate

    seam = SeamContext(tenant="amh", principal="helena")
    labels = {
        "tenant": "amh",
        "principal": "helena",
        "decision": "deny",
        "layer": "L0_CATALOGO",
        "reason": "OPERACAO_DESCONHECIDA",
        "enforced": "false",
    }
    before = _sample("maezo_gateway_pep_decision_total", **labels)
    decision = await gate(seam, "operacao.que.nao.existe")
    after = _sample("maezo_gateway_pep_decision_total", **labels)

    assert decision.allow is False and decision.enforced is False
    assert after == before + 1.0, (before, after)


def test_alert_rules_pep_metric_normalises_an_unknown_reason_and_layer() -> None:
    from maezo.gateway.effect_pep import EffectDecision
    from maezo.gateway.seams._base import SeamContext, _count_pep_decision

    seam = SeamContext(tenant="amh", principal="helena")
    labels = {
        "tenant": "amh",
        "principal": "helena",
        "decision": "deny",
        "layer": "outro",
        "reason": "outro",
        "enforced": "true",
    }
    before = _sample("maezo_gateway_pep_decision_total", **labels)
    _count_pep_decision(
        seam, EffectDecision(allow=False, enforced=True, reason="TOKEN_NOVO_CRU", layer="CAMADA_NOVA")
    )
    assert _sample("maezo_gateway_pep_decision_total", **labels) == before + 1.0


def _root(*, budget: Budget) -> DelegationEnvelope:
    return DelegationEnvelope.root(
        task_id="r085-t1",
        task_type="authorization.analyze",
        origin="helena",
        target="rafael",
        tenant="amh",
        budget=budget,
        payload_ref="fhir://Patient/abc",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("subdelegate_to", "budget", "guard"),
    [
        ("carolina", Budget(tokens=1, time_ms=1), "budget_exhausted"),
        ("helena", Budget(tokens=100, time_ms=100), "cyclic"),
    ],
)
async def test_alert_rules_dispatcher_counts_the_guard_that_refused(
    subdelegate_to: str, budget: Budget, guard: str
) -> None:
    handler = FakeAgentHandler(subdelegate_to=subdelegate_to)
    dispatcher, _sink, _producer = build_test_dispatcher(
        cards=[make_card("rafael")], handlers={"rafael": handler}
    )
    labels = {"target": "rafael", "guard": guard}
    before = _sample("maezo_a2a_guard_refusal_total", **labels)
    result = await dispatcher.delegate(_root(budget=budget))
    after = _sample("maezo_a2a_guard_refusal_total", **labels)

    assert not result.success
    assert after == before + 1.0, (before, after)


@pytest.mark.asyncio
async def test_alert_rules_successful_delegation_counts_no_guard_refusal() -> None:
    handler = FakeAgentHandler()
    dispatcher, _sink, _producer = build_test_dispatcher(
        cards=[make_card("rafael")], handlers={"rafael": handler}
    )
    guards = ("budget_exhausted", "cyclic", "max_hops", "outro")
    before = {g: _sample("maezo_a2a_guard_refusal_total", target="rafael", guard=g) for g in guards}
    result = await dispatcher.delegate(_root(budget=Budget(tokens=100, time_ms=100)))
    after = {g: _sample("maezo_a2a_guard_refusal_total", target="rafael", guard=g) for g in before}

    assert result.success
    assert after == before


def test_alert_rules_guard_recorder_never_labels_a_raw_value() -> None:
    from maezo.platform.observability import record_a2a_guard_refusal

    before = _sample("maezo_a2a_guard_refusal_total", target="rafael", guard="outro")
    record_a2a_guard_refusal(target="rafael", guard="valor_cru_inesperado")
    assert _sample("maezo_a2a_guard_refusal_total", target="rafael", guard="outro") == before + 1.0
    assert _sample("maezo_a2a_guard_refusal_total", target="rafael", guard="valor_cru_inesperado") == 0.0
