"""Unit tests for maezo.tools.workers.adequacao (SP-OP-ADEQUACAO-001).

TDD London School: tests verify gap measurement and the human-gated fallback commitment.
"""

import pytest

from maezo.tools.workers.adequacao import (
    ERR_FALLBACK_COMMITMENT_NOT_HUMAN,
    AdequacaoError,
    execute_remediation,
    measure_gap,
    notify_coordenacao,
    notify_sla_risk,
    register_adequacao_workers,
    register_fallback_commitment,
    route_remediation,
    update_monitoring_plan,
)
from maezo.tools.workers.dmn_transport import DmnEvaluationError, FakeDmnTransport
from maezo.tools.workers.harness import WorkerHarness


def _adequacao_fake(*, gap_adequacao: str, roteamento_remediacao: str, motivo: str = "") -> FakeDmnTransport:
    """Rows verified live against the compose engine (T1.5 parity run)."""
    fake = FakeDmnTransport()
    fake.register("adequacao_gap", [{"gap_adequacao": gap_adequacao, "motivo": motivo}])
    fake.register(
        "adequacao_remediation_routing",
        [{"roteamento_remediacao": roteamento_remediacao, "motivo": motivo}],
    )
    return fake


# ---------------------------------------------------------------
# measure_gap
# ---------------------------------------------------------------


def test_measure_gap_returns_facts() -> None:
    result = measure_gap(
        {
            "regiao_saude": "R-001",
            "especialidade": "cardiologia",
            "tipo_carater": "eletivo",
            "prestadores_disponiveis": 3,
        }
    )
    assert result["tempo_acesso_apurado_min"] == 45
    assert result["distancia_apurada_km"] == 15.5
    assert result["prestadores_disponiveis"] == 3
    assert result["cobertura_geo_suficiente"] is True
    assert result["dados_geo_completos"] is True


def test_measure_gap_insufficient_data() -> None:
    result = measure_gap(
        {
            "regiao_saude": "",
            "especialidade": "",
        }
    )
    assert result["dados_geo_completos"] is False


# ---------------------------------------------------------------
# route_remediation
# ---------------------------------------------------------------


def test_adequacao_gap_conforme_now_leve() -> None:
    """golden-parity finding (T1.5, live-verified): the deployed adequacao_gap table's
    GAP_LEVE row (tipo_carater="eletivo", tempo<=60, distancia<=50.0, prestadores>0,
    cobertura=true) precedes CONFORME's in FIRST-hit-policy order and is LESS restrictive than
    the old Python's CONFORME gate (tempo<=30, distancia<=20.0) — this exact input (which the
    old Python called CONFORME) is actually GAP_LEVE on the real engine. DMN wins (documented,
    not patched); zero functional impact — CONFORME and GAP_LEVE route to the SAME MONITORAR
    remediation (live-verified)."""
    fake = _adequacao_fake(gap_adequacao="GAP_LEVE", roteamento_remediacao="MONITORAR")
    result = route_remediation(
        {
            "tipo_carater": "eletivo",
            "tempo_acesso_apurado_min": 15,
            "distancia_apurada_km": 5.0,
            "prestadores_disponiveis": 5,
            "cobertura_geo_suficiente": True,
            "dados_geo_completos": True,
        },
        dmn=fake,
    )
    assert result["gap_adequacao"] == "GAP_LEVE"
    assert result["roteamento_remediacao"] == "MONITORAR"


def test_adequacao_gap_conforme_reachable_beyond_leve_gate() -> None:
    """CONFORME is still reachable — live-verified: when tempo exceeds GAP_LEVE's own gate
    (tempo<=60) but prestadores>0 and cobertura=true, the DMN's gateless CONFORME row (wildcard
    on tempo/distancia) matches. Also documents a DMN-content quirk (not patched, per
    constraint 5): CONFORME's row does not itself gate on tempo/distancia, so this specific
    case (tempo=70min) reads as MORE severe than the tempo=45min GAP_LEVE case yet is labeled
    CONFORME — flagged for spec-side review, not fixed here."""
    fake = _adequacao_fake(gap_adequacao="CONFORME", roteamento_remediacao="MONITORAR")
    result = route_remediation(
        {
            "tipo_carater": "eletivo",
            "tempo_acesso_apurado_min": 70,
            "distancia_apurada_km": 10.0,
            "prestadores_disponiveis": 2,
            "cobertura_geo_suficiente": True,
            "dados_geo_completos": True,
        },
        dmn=fake,
    )
    assert result["gap_adequacao"] == "CONFORME"
    assert result["roteamento_remediacao"] == "MONITORAR"


def test_adequacao_gap_leve() -> None:
    fake = _adequacao_fake(gap_adequacao="GAP_LEVE", roteamento_remediacao="MONITORAR")
    result = route_remediation(
        {
            "tipo_carater": "eletivo",
            "tempo_acesso_apurado_min": 45,
            "distancia_apurada_km": 10.0,
            "prestadores_disponiveis": 2,
            "cobertura_geo_suficiente": True,
            "dados_geo_completos": True,
        },
        dmn=fake,
    )
    assert result["gap_adequacao"] == "GAP_LEVE"
    assert result["roteamento_remediacao"] == "MONITORAR"


def test_adequacao_gap_moderado() -> None:
    """golden-parity note: the DMN computes GAP_MODERADO differently — keyed off
    cobertura_geo_suficiente=false (wildcard on tempo/distancia/prestadores), not the old
    Python's prestadores>=1 and tempo<=90. Same live-verified inputs, same result."""
    fake = _adequacao_fake(gap_adequacao="GAP_MODERADO", roteamento_remediacao="ENCAMINHAR_CREDENCIAMENTO")
    result = route_remediation(
        {
            "tipo_carater": "eletivo",
            "tempo_acesso_apurado_min": 75,
            "distancia_apurada_km": 30.0,
            "prestadores_disponiveis": 1,
            "cobertura_geo_suficiente": False,
            "dados_geo_completos": True,
        },
        dmn=fake,
    )
    assert result["gap_adequacao"] == "GAP_MODERADO"
    assert result["roteamento_remediacao"] == "ENCAMINHAR_CREDENCIAMENTO"


def test_adequacao_gap_critico() -> None:
    fake = _adequacao_fake(gap_adequacao="GAP_CRITICO", roteamento_remediacao="ANALISE_HUMANA")
    result = route_remediation(
        {
            "tipo_carater": "eletivo",
            "tempo_acesso_apurado_min": 120,
            "distancia_apurada_km": 50.0,
            "prestadores_disponiveis": 0,
            "cobertura_geo_suficiente": False,
            "dados_geo_completos": True,
        },
        dmn=fake,
    )
    assert result["gap_adequacao"] == "GAP_CRITICO"
    assert result["roteamento_remediacao"] == "ANALISE_HUMANA"


def test_adequacao_gap_dados_incompletos() -> None:
    fake = _adequacao_fake(gap_adequacao="GAP_CRITICO", roteamento_remediacao="ANALISE_HUMANA")
    result = route_remediation(
        {
            "dados_geo_completos": False,
            "tempo_acesso_apurado_min": 0,
            "distancia_apurada_km": 0.0,
            "prestadores_disponiveis": 0,
            "cobertura_geo_suficiente": False,
        },
        dmn=fake,
    )
    assert result["gap_adequacao"] == "GAP_CRITICO"
    assert result["roteamento_remediacao"] == "ANALISE_HUMANA"


def test_route_remediation_dmn_unwired_raises_dmn_evaluation_error() -> None:
    with pytest.raises(DmnEvaluationError):
        route_remediation({"tipo_carater": "eletivo"}, dmn=None)


# ---------------------------------------------------------------
# notify_coordenacao
# ---------------------------------------------------------------


def test_notify_coordenacao() -> None:
    result = notify_coordenacao(
        {
            "regiao_saude": "R-001",
            "gap_adequacao": "GAP_CRITICO",
        }
    )
    assert result["notificacao_enviada"] is True


# ---------------------------------------------------------------
# execute_remediation
# ---------------------------------------------------------------


def test_execute_remediation() -> None:
    result = execute_remediation(
        {
            "regiao_saude": "R-001",
            "especialidade": "cardiologia",
        }
    )
    assert result["handoff_credenciamento"] is True
    assert result["processo_destino"] == "SP-OP-CRED-001"


# ---------------------------------------------------------------
# update_monitoring_plan — NEUTRAL, no adverse effect (t2.5-p2b-round2)
# ---------------------------------------------------------------


def test_update_monitoring_plan_neutral() -> None:
    result = update_monitoring_plan(
        {
            "regiao_saude": "R-001",
            "especialidade": "cardiologia",
            "gap_adequacao": "GAP_LEVE",
        }
    )
    assert result["plano_monitoramento_atualizado"] is True
    assert result["celula"] == "R-001:cardiologia"
    assert result["gap_adequacao"] == "GAP_LEVE"


def test_update_monitoring_plan_never_commits_fallback() -> None:
    """Monitoring/alert only -- NEVER commits cash-flow nor denies care (BPMN task doc,
    ST_UpdateMonitoringPlanL3: 'NAO compromete caixa nem nega atendimento')."""
    result = update_monitoring_plan({"regiao_saude": "R-001", "especialidade": "cardiologia"})
    forbidden = {"COMPROMISSO_FALLBACK", "NEGADO", "NEGAR"}
    for value in result.values():
        assert str(value).upper() not in forbidden
    assert "decisao_remediacao" not in result
    assert "compromisso_fallback_registrado" not in result


# ---------------------------------------------------------------
# notify_sla_risk — informational, never adverse (t2.5-p2b-round2)
# ---------------------------------------------------------------


def test_notify_sla_risk_informational() -> None:
    result = notify_sla_risk({"regiao_saude": "R-001", "especialidade": "cardiologia"})
    assert result["sla_risk_notified"] is True
    assert result["grupo_alertado"] == "coordenacao-rede"
    assert result["regiao_saude"] == "R-001"
    assert result["especialidade"] == "cardiologia"


def test_notify_sla_risk_no_adverse_outcome() -> None:
    """The non-interruptive timer alert never produces or propagates the fallback commitment
    decision -- UT_DecisaoFallback stays open, the commitment NEVER arises from a timer."""
    result = notify_sla_risk({"regiao_saude": "R-001", "decisao_remediacao": "COMPROMISSO_FALLBACK"})
    assert "decisao_remediacao" not in result
    forbidden = {"COMPROMISSO_FALLBACK"}
    for value in result.values():
        assert str(value).upper() not in forbidden


# ---------------------------------------------------------------
# register_adequacao_workers — registration + topic-registry drift guard
# ---------------------------------------------------------------


def test_register_adequacao_workers_registers_new_topics() -> None:
    """`update_monitoring_plan`/`notify_sla_risk` are registered on their exact BPMN-declared
    topics -- closes 2 of the 3 registry-drift gaps documented in
    tests/integration/processes/test_sp_op_adequacao_001.py (FINDING 1); only
    `prepare_remediation_dossier` (Andre A2A, gated) remains unregistered."""
    harness = WorkerHarness(None, worker_id="unit-test-adequacao")  # type: ignore[arg-type]
    register_adequacao_workers(harness, None, dmn=FakeDmnTransport())
    topics = set(harness.registered_topics)
    assert "operadora.adequacao.update_monitoring_plan" in topics
    assert "operadora.adequacao.notify_sla_risk" in topics
    assert "operadora.adequacao.prepare_remediation_dossier" not in topics
    adequacao_topics = {t for t in topics if t.startswith("operadora.adequacao.")}
    assert len(adequacao_topics) == 7  # 8 BPMN-declared topics minus the gated dossier worker


# ---------------------------------------------------------------
# register_fallback_commitment — GUARD
# ---------------------------------------------------------------


def test_fallback_commitment_happy_path() -> None:
    result = register_fallback_commitment(
        {
            "decisao_remediacao": "COMPROMISSO_FALLBACK",
            "tipo_fallback": "livre_escolha",
            "justificativa_fallback": "Sem prestador na regiao — RN 259",
            "referencia_regulatoria": "RN 259",
            "responsavel_id": "gestor-001",
            "estimativa_custo_cents": 500000,
        }
    )
    assert result["compromisso_fallback_registrado"] is True


def test_fallback_commitment_rejects_wrong_decisao() -> None:
    with pytest.raises(AdequacaoError) as excinfo:
        register_fallback_commitment(
            {
                "decisao_remediacao": "MONITORAR_OK",
                "tipo_fallback": "",
                "justificativa_fallback": "",
                "referencia_regulatoria": "",
                "responsavel_id": "",
            }
        )
    assert excinfo.value.code == ERR_FALLBACK_COMMITMENT_NOT_HUMAN
    assert "MONITORAR_OK" in excinfo.value.message


def test_fallback_commitment_rejects_missing_justificativa() -> None:
    with pytest.raises(AdequacaoError) as excinfo:
        register_fallback_commitment(
            {
                "decisao_remediacao": "COMPROMISSO_FALLBACK",
                "tipo_fallback": "reembolso_garantido",
                "justificativa_fallback": "",
                "referencia_regulatoria": "RN 259",
                "responsavel_id": "gestor-001",
            }
        )
    assert excinfo.value.code == ERR_FALLBACK_COMMITMENT_NOT_HUMAN
