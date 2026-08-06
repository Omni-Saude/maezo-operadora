"""Unit tests for maezo.tools.workers.adequacao (SP-OP-ADEQUACAO-001).

TDD London School: tests verify gap measurement and the human-gated fallback commitment.
"""

import pytest

from maezo.a2a import DelegationResult, RejectionReason
from maezo.tools.workers.adequacao import (
    ERR_FALLBACK_COMMITMENT_NOT_HUMAN,
    AdequacaoError,
    execute_remediation,
    make_prepare_remediation_dossier_handler,
    make_update_monitoring_plan_handler,
    measure_gap,
    notify_coordenacao,
    notify_sla_risk,
    register_adequacao_workers,
    register_fallback_commitment,
    route_remediation,
    update_monitoring_plan,
)
from maezo.tools.workers.dmn_transport import DmnEvaluationError, FakeDmnTransport
from maezo.tools.workers.harness import ExternalTask, FakeKafkaPublisher, WorkerHarness


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
# measure_gap — FACT PRESERVATION (mirrors credenciamento.validate_cred's fix, the identical
# defect class: an already-resolved fact on `variables` must be ECHOED, never clobbered by the
# placeholder/re-derivation before BRT_AdequacaoGap evaluates it).
# ---------------------------------------------------------------


def test_measure_gap_preserves_resolved_tempo_acesso() -> None:
    """An explicit, correctly-typed (int, non-bool) `tempo_acesso_apurado_min` is echoed
    through unchanged -- NOT clobbered by the hardcoded 45min placeholder."""
    result = measure_gap(
        {"regiao_saude": "R-001", "especialidade": "cardiologia", "tempo_acesso_apurado_min": 12}
    )
    assert result["tempo_acesso_apurado_min"] == 12


def test_measure_gap_tempo_acesso_absent_falls_back_to_placeholder() -> None:
    result = measure_gap({"regiao_saude": "R-001", "especialidade": "cardiologia"})
    assert result["tempo_acesso_apurado_min"] == 45


@pytest.mark.parametrize("wrong_type", [True, False, "12", 12.0, None, [12], {"v": 12}])
def test_measure_gap_tempo_acesso_wrong_type_falls_back_to_placeholder(wrong_type: object) -> None:
    """A wrong-typed `tempo_acesso_apurado_min` (incl. `bool` -- a subclass of `int` in Python,
    but NEVER a valid numeric measurement here) is NOT treated as a resolved fact -- falls back
    to the placeholder, exactly like absent."""
    result = measure_gap(
        {"regiao_saude": "R-001", "especialidade": "cardiologia", "tempo_acesso_apurado_min": wrong_type}
    )
    assert result["tempo_acesso_apurado_min"] == 45


def test_measure_gap_preserves_resolved_distancia_apurada() -> None:
    """An explicit, correctly-typed (float) `distancia_apurada_km` is echoed through unchanged
    -- NOT clobbered by the hardcoded 15.5km placeholder."""
    result = measure_gap(
        {"regiao_saude": "R-001", "especialidade": "cardiologia", "distancia_apurada_km": 3.2}
    )
    assert result["distancia_apurada_km"] == 3.2


def test_measure_gap_distancia_apurada_absent_falls_back_to_placeholder() -> None:
    result = measure_gap({"regiao_saude": "R-001", "especialidade": "cardiologia"})
    assert result["distancia_apurada_km"] == 15.5


@pytest.mark.parametrize("wrong_type", [True, False, "3.2", 3, None, [3.2], {"v": 3.2}])
def test_measure_gap_distancia_apurada_wrong_type_falls_back_to_placeholder(wrong_type: object) -> None:
    """A wrong-typed `distancia_apurada_km` (incl. `bool`, and incl. a plain `int` -- the
    contract types this field `double`, never `number`/`integer`) falls back to the placeholder,
    exactly like absent."""
    result = measure_gap(
        {"regiao_saude": "R-001", "especialidade": "cardiologia", "distancia_apurada_km": wrong_type}
    )
    assert result["distancia_apurada_km"] == 15.5


def test_measure_gap_preserves_resolved_cobertura_geo_suficiente_true() -> None:
    """An already-resolved `cobertura_geo_suficiente=True` is respected even when the
    prestadores-derived heuristic would say otherwise (prestadores<2 -> heuristic False)."""
    result = measure_gap(
        {
            "regiao_saude": "R-001",
            "especialidade": "cardiologia",
            "prestadores_disponiveis": 0,
            "cobertura_geo_suficiente": True,
        }
    )
    assert result["cobertura_geo_suficiente"] is True


def test_measure_gap_preserves_resolved_cobertura_geo_suficiente_false() -> None:
    """An already-resolved `cobertura_geo_suficiente=False` is respected even when the
    prestadores-derived heuristic would say otherwise (prestadores>=2 -> heuristic True) --
    the FIX must be strictly MORE conservative than the pre-fix always-recompute behavior,
    never less."""
    result = measure_gap(
        {
            "regiao_saude": "R-001",
            "especialidade": "cardiologia",
            "prestadores_disponiveis": 5,
            "cobertura_geo_suficiente": False,
        }
    )
    assert result["cobertura_geo_suficiente"] is False


@pytest.mark.parametrize("wrong_type", ["true", 1, 0, None, [], {}])
def test_measure_gap_cobertura_geo_suficiente_wrong_type_falls_back_to_derivation(wrong_type: object) -> None:
    result = measure_gap(
        {
            "regiao_saude": "R-001",
            "especialidade": "cardiologia",
            "prestadores_disponiveis": 5,
            "cobertura_geo_suficiente": wrong_type,
        }
    )
    assert result["cobertura_geo_suficiente"] is True  # prestadores=5 >= 2 -> derived True


def test_measure_gap_preserves_resolved_dados_geo_completos_true() -> None:
    """An already-resolved `dados_geo_completos=True` is respected even when regiao/especialidade
    are blank (the shape-derivation heuristic would say False)."""
    result = measure_gap({"regiao_saude": "", "especialidade": "", "dados_geo_completos": True})
    assert result["dados_geo_completos"] is True


def test_measure_gap_preserves_resolved_dados_geo_completos_false() -> None:
    """An already-resolved `dados_geo_completos=False` is respected even when regiao/especialidade
    are both present (the shape-derivation heuristic would say True)."""
    result = measure_gap(
        {"regiao_saude": "R-001", "especialidade": "cardiologia", "dados_geo_completos": False}
    )
    assert result["dados_geo_completos"] is False


@pytest.mark.parametrize("wrong_type", ["true", 1, 0, None, [], {}])
def test_measure_gap_dados_geo_completos_wrong_type_falls_back_to_derivation(wrong_type: object) -> None:
    result = measure_gap(
        {"regiao_saude": "R-001", "especialidade": "cardiologia", "dados_geo_completos": wrong_type}
    )
    assert result["dados_geo_completos"] is True  # regiao and especialidade both present -> True


def test_measure_gap_all_four_facts_preserved_simultaneously_prestadores_unaffected() -> None:
    """All four previously-clobbered facts respected at once; `prestadores_disponiveis`
    (already preserved pre-fix) and the unrelated identity fields stay unchanged -- pins that
    the fix touches ONLY the four clobbered keys."""
    result = measure_gap(
        {
            "regiao_saude": "R-002",
            "especialidade": "ortopedia",
            "tempo_acesso_apurado_min": 8,
            "distancia_apurada_km": 1.1,
            "cobertura_geo_suficiente": False,
            "dados_geo_completos": False,
            "prestadores_disponiveis": 7,
        }
    )
    assert result == {
        "tempo_acesso_apurado_min": 8,
        "distancia_apurada_km": 1.1,
        "prestadores_disponiveis": 7,
        "cobertura_geo_suficiente": False,
        "dados_geo_completos": False,
    }


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
    CONFORME — flagged for spec-side review, not fixed here.

    FAIL-SAFE (decisao do dono, 2026-08-06): o veredito da DMN continua CONFORME (nao reescrevemos
    a tabela), MAS o worker RECUSA agir sobre ele quando o acesso medido excede o teto que a
    propria tabela declara em `r_eletivo_leve` (60min/50km) — o roteamento vira ANALISE_HUMANA em
    vez de MONITORAR. Sem isto, este caso (70min) terminava em End_AdequacaoConforme SEM plano de
    monitoramento e SEM alerta a gestao-rede, pior que o comportamento anterior a preservacao de
    fatos."""
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
    # veredito da DMN preservado e auditavel...
    assert result["gap_adequacao"] == "CONFORME"
    # ...mas NAO se age sobre ele: 70min excede o teto eletivo da propria tabela (60min).
    assert result["roteamento_remediacao"] == "ANALISE_HUMANA"
    assert result["motivo"] == "CONFORME_RECUSADO_ACESSO_ACIMA_DO_TETO_LEVE"


def test_conforme_dentro_do_teto_leve_nao_e_recusado() -> None:
    """Contra-prova de nao-vacuidade: CONFORME DENTRO do teto (45min/10km) segue MONITORAR.

    Sem este teste o fail-safe poderia recusar tudo e ainda parecer correto."""
    fake = _adequacao_fake(gap_adequacao="CONFORME", roteamento_remediacao="MONITORAR")
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
    assert result["gap_adequacao"] == "CONFORME"
    assert result["roteamento_remediacao"] == "MONITORAR"


def test_conforme_recusado_tambem_por_distancia() -> None:
    """A recusa dispara pelo teto de DISTANCIA tambem, nao so pelo de tempo."""
    fake = _adequacao_fake(gap_adequacao="CONFORME", roteamento_remediacao="MONITORAR")
    result = route_remediation(
        {
            "tipo_carater": "eletivo",
            "tempo_acesso_apurado_min": 30,
            "distancia_apurada_km": 80.0,
            "prestadores_disponiveis": 2,
            "cobertura_geo_suficiente": True,
            "dados_geo_completos": True,
        },
        dmn=fake,
    )
    assert result["roteamento_remediacao"] == "ANALISE_HUMANA"


def test_gap_leve_acima_do_teto_nao_e_afetado_pelo_fail_safe() -> None:
    """O fail-safe so alcanca CONFORME — um GAP_LEVE segue seu roteamento normal."""
    fake = _adequacao_fake(gap_adequacao="GAP_LEVE", roteamento_remediacao="MONITORAR")
    result = route_remediation(
        {
            "tipo_carater": "eletivo",
            "tempo_acesso_apurado_min": 200,
            "distancia_apurada_km": 90.0,
            "prestadores_disponiveis": 2,
            "cobertura_geo_suficiente": True,
            "dados_geo_completos": True,
        },
        dmn=fake,
    )
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
# make_update_monitoring_plan_handler — item-9 notify-wiring fix: `register_adequacao_workers`
# used to `del kafka  # unused`; this raw handler publishes a `_NOTIFICATIONS_TOPIC` notification.
# ---------------------------------------------------------------


def _monitoring_plan_task(variables: dict) -> ExternalTask:
    return ExternalTask(
        task_id="et-mp-1",
        topic="operadora.adequacao.update_monitoring_plan",
        process_instance_id="pi-1",
        business_key="ADEQ-amh-R-001-cardiologia-2026-Q3",
        worker_id="w-1",
        variables=variables,
    )


async def test_make_update_monitoring_plan_handler_publishes_notification() -> None:
    kafka = FakeKafkaPublisher()
    handler = make_update_monitoring_plan_handler(kafka)
    task = _monitoring_plan_task(
        {
            "tenant_id": "amh",
            "regiao_saude": "R-001",
            "especialidade": "cardiologia",
            "gap_adequacao": "GAP_LEVE",
        }
    )

    result = await handler(task)

    assert result["plano_monitoramento_atualizado"] is True
    assert result["celula"] == "R-001:cardiologia"
    assert result["gap_adequacao"] == "GAP_LEVE"
    assert len(kafka.published) == 1
    topic, payload, key = kafka.published[0]
    assert topic == "operadora.notifications.internal"
    assert payload == {
        "type": "adequacao.update_monitoring_plan",
        "tenant_id": "amh",
        "regiao_saude": "R-001",
        "especialidade": "cardiologia",
        "gap_adequacao": "GAP_LEVE",
    }
    assert key == task.business_key
    assert kafka.best_effort_calls == [False]


async def test_make_update_monitoring_plan_handler_payload_is_non_phi() -> None:
    """Payload carries only cell identity (tenant/regiao/especialidade) + gap classification --
    no beneficiary identifier anywhere (this process carries none at all, ADR-0006)."""
    kafka = FakeKafkaPublisher()
    handler = make_update_monitoring_plan_handler(kafka)
    await handler(
        _monitoring_plan_task(
            {
                "tenant_id": "amh",
                "regiao_saude": "R-001",
                "especialidade": "cardiologia",
                "gap_adequacao": "GAP_CRITICO",
            }
        )
    )
    _, payload, _ = kafka.published[0]
    assert set(payload.keys()) == {"type", "tenant_id", "regiao_saude", "especialidade", "gap_adequacao"}


async def test_make_update_monitoring_plan_handler_no_producer_still_completes() -> None:
    """`kafka=None` -- no producer wired -- logs a warning and the task STILL completes; the L3
    monitoring-plan update itself is never blocked by a missing producer."""
    handler = make_update_monitoring_plan_handler(None)
    result = await handler(
        _monitoring_plan_task(
            {"regiao_saude": "R-001", "especialidade": "cardiologia", "gap_adequacao": "GAP_LEVE"}
        )
    )
    assert result["plano_monitoramento_atualizado"] is True
    assert result["celula"] == "R-001:cardiologia"


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
    tests/integration/processes/test_sp_op_adequacao_001.py (FINDING 1);
    `prepare_remediation_dossier` is now the REAL Andre A2A raw async handler (DL-0033 closed) —
    registered via `harness.register()` (NOT the WorkerRegistry), topic always served.
    `update_monitoring_plan` is ALSO now a raw handler (item-9 notify-wiring fix — the
    `del kafka # unused` gap) — registers with `kafka=None` too, so the topic is always served
    even with no producer wired."""
    harness = WorkerHarness(None, worker_id="unit-test-adequacao")  # type: ignore[arg-type]
    register_adequacao_workers(harness, None, dmn=FakeDmnTransport())
    topics = set(harness.registered_topics)
    assert "operadora.adequacao.update_monitoring_plan" in topics
    assert "operadora.adequacao.notify_sla_risk" in topics
    assert "operadora.adequacao.prepare_remediation_dossier" in topics
    assert harness.registry.get("operadora.adequacao.prepare_remediation_dossier") is None  # raw
    assert harness.registry.get("operadora.adequacao.update_monitoring_plan") is None  # raw (item-9)
    assert harness.registry.get("operadora.adequacao.notify_sla_risk") is not None  # still FunctionWorker
    adequacao_topics = {t for t in topics if t.startswith("operadora.adequacao.")}
    assert len(adequacao_topics) == 8  # all 8 BPMN-declared topics registered (dossier now REAL A2A)


async def test_register_adequacao_workers_update_monitoring_plan_publishes_via_wired_kafka() -> None:
    """End-to-end registration-level pin: `register_adequacao_workers(harness, kafka)` wires the
    SAME kafka producer into `update_monitoring_plan`'s raw handler -- dispatching the registered
    handler for the topic actually publishes (not just a unit-level check on the factory)."""
    harness = WorkerHarness(None, worker_id="unit-test-adequacao")  # type: ignore[arg-type]
    kafka = FakeKafkaPublisher()
    register_adequacao_workers(harness, kafka, dmn=FakeDmnTransport())
    handler = harness._handlers["operadora.adequacao.update_monitoring_plan"]  # type: ignore[attr-defined]  # raw-handler registry, test introspection
    result = await handler(
        _monitoring_plan_task(
            {"regiao_saude": "R-001", "especialidade": "cardiologia", "gap_adequacao": "GAP_LEVE"}
        )
    )
    assert result["plano_monitoramento_atualizado"] is True
    assert len(kafka.published) == 1
    assert kafka.published[0][1]["type"] == "adequacao.update_monitoring_plan"


# ---------------------------------------------------------------
# prepare_remediation_dossier — REAL Andre A2A delegation (raw async handler; DL-0033 closed,
# DL-0037)
# ---------------------------------------------------------------


class _FakeDossierDispatcher:
    """Records the envelope; returns a programmed `DelegationResult` (or raises)."""

    def __init__(self, result: DelegationResult | None = None, exc: Exception | None = None) -> None:
        self.envelopes: list = []
        self._result = result
        self._exc = exc

    async def delegate(self, envelope) -> DelegationResult:  # noqa: ANN001 — duck-typed fake
        self.envelopes.append(envelope)
        if self._exc is not None:
            raise self._exc
        assert self._result is not None
        return self._result


def _dossier_task(variables: dict) -> ExternalTask:
    return ExternalTask(
        task_id="et-1",
        topic="operadora.adequacao.prepare_remediation_dossier",
        process_instance_id="pi-1",
        business_key="ADEQ-amh-SP-01-cardiologia",
        worker_id="w-1",
        variables=variables,
    )


_ADEQ_DOSSIER_VARS = {
    "tenant_id": "amh",
    "regiao_saude": "SP-01",
    "especialidade": "cardiologia",
    "gap_adequacao": "GAP_CRITICO",
    "roteamento_remediacao": "ANALISE_HUMANA",
}


async def test_prepare_remediation_dossier_delegates_to_andre_and_returns_real_outputs() -> None:
    """Dispatcher present -> await delegate -> dossier-real outputs: `dossier_prepared=True`,
    the `output_ref` reference, and Andre's agent-produced compact summary AS-IS
    (`dossier_summary` — the SME/PO-pending UT-form seam). The envelope uses the SHARED
    `analytics.population` type with the `adequacao-worker` origin (his flow disambiguator) and
    the cell key as task_id (Guard 4)."""
    dispatcher = _FakeDossierDispatcher(
        result=DelegationResult.ok(
            "ADEQ-amh-SP-01-cardiologia",
            "process://ADEQ-amh-SP-01-cardiologia",
            meta={"route": "human_review", "grupo_destino": "gestao-rede"},
        )
    )
    handler = make_prepare_remediation_dossier_handler(dispatcher)  # type: ignore[arg-type]

    result = await handler(_dossier_task(_ADEQ_DOSSIER_VARS))

    assert result is not None
    assert result["dossier_prepared"] is True
    assert result["dossier_ref"] == "process://ADEQ-amh-SP-01-cardiologia"
    assert result["dossier_summary"] == {"route": "human_review", "grupo_destino": "gestao-rede"}
    (envelope,) = dispatcher.envelopes
    assert envelope.task_id == "ADEQ-amh-SP-01-cardiologia"
    assert envelope.task_type == "analytics.population"
    assert envelope.origin == "adequacao-worker"
    assert envelope.target == "andre"
    assert envelope.payload_meta["gap_adequacao"] == "GAP_CRITICO"


async def test_prepare_remediation_dossier_without_dispatcher_fail_neutrals_with_gap() -> None:
    """DL-0037 degradation posture: dispatcher absent (degraded runtime) -> the task still
    COMPLETES (UT_DecisaoFallback must open) with `dossier_prepared=False` + the bounded gap
    token — never a raise, never a fabricated dossier."""
    handler = make_prepare_remediation_dossier_handler(None)
    result = await handler(_dossier_task(_ADEQ_DOSSIER_VARS))
    assert result is not None
    assert result["dossier_prepared"] is False
    assert result["dossier_gap"] == "dispatcher_unavailable"


async def test_prepare_remediation_dossier_missing_cell_identity_gap_never_delegates() -> None:
    """No tenant/regiao/especialidade (incl. whitespace-only, `non_blank` discipline) -> no
    degenerate ADEQ task_id is ever delegated; gap marker, UT still opens."""
    dispatcher = _FakeDossierDispatcher(result=DelegationResult.ok("x", "process://x"))
    handler = make_prepare_remediation_dossier_handler(dispatcher)  # type: ignore[arg-type]
    result = await handler(_dossier_task({"tenant_id": "amh", "regiao_saude": "  "}))
    assert result is not None
    assert result["dossier_prepared"] is False
    assert result["dossier_gap"] == "missing_business_identifiers"
    assert dispatcher.envelopes == []


async def test_prepare_remediation_dossier_delegation_failure_fail_neutrals_never_raises() -> None:
    """ANY delegation exception -> gap marker + loud log; raw error text stays OUT of the engine
    variables (bounded class token only)."""
    handler = make_prepare_remediation_dossier_handler(
        _FakeDossierDispatcher(exc=RuntimeError("pg down: dsn=secret"))  # type: ignore[arg-type]
    )
    result = await handler(_dossier_task(_ADEQ_DOSSIER_VARS))
    assert result is not None
    assert result["dossier_prepared"] is False
    assert result["dossier_gap"] == "delegation_failed"
    assert "secret" not in str(result.values())


async def test_prepare_remediation_dossier_structured_rejection_gap_bounded_reason() -> None:
    handler = make_prepare_remediation_dossier_handler(
        _FakeDossierDispatcher(  # type: ignore[arg-type]
            result=DelegationResult.rejected(
                "ADEQ-amh-SP-01-cardiologia",
                RejectionReason.TASK_TYPE_NOT_ACCEPTED,
                detail="not accepted",
            )
        )
    )
    result = await handler(_dossier_task(_ADEQ_DOSSIER_VARS))
    assert result is not None
    assert result["dossier_prepared"] is False
    assert result["dossier_gap"] == "delegation_rejected:task_type_not_accepted"


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


# ---------------------------------------------------------------
# register_fallback_commitment — whitespace-bypass vectors (t3.1-guard-input-hardening,
# closing the #133-audit-flagged gap at adequacao.py:289-295: the guard's original bare
# `if not tipo_fallback` / `if not justificativa` / ... checks let WHITESPACE-ONLY decision +
# accountability fields through, the same class the c1377fa fix closed for
# pagto.register_payment_refusal. Every vector below MUST refuse with
# ERR_FALLBACK_COMMITMENT_NOT_HUMAN -- whitespace-only is the SAME as absent (ADR-0007/RN 259:
# a fallback commitment must carry an identifying human approver + a real justification).
# ---------------------------------------------------------------

_WHITESPACE_VARIANTS = [" ", "   ", "\t", "\n", "\t\n ", "\r\n"]
_NON_STRING_VARIANTS: list[object] = [123, True, 0.5, ["x"], {"k": "v"}]


def _fallback_baseline(**overrides: object) -> dict[str, object]:
    """Happy-path baseline for register_fallback_commitment -- so any guard failure observed
    in a test is attributable ONLY to the field under test."""
    base: dict[str, object] = {
        "decisao_remediacao": "COMPROMISSO_FALLBACK",
        "tipo_fallback": "livre_escolha",
        "justificativa_fallback": "Sem prestador na regiao — RN 259",
        "referencia_regulatoria": "RN 259",
        "responsavel_id": "gestor-001",
        "estimativa_custo_cents": 500000,
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize("whitespace", _WHITESPACE_VARIANTS)
def test_fallback_commitment_whitespace_only_tipo_fallback_refuses(whitespace: str) -> None:
    with pytest.raises(AdequacaoError) as excinfo:
        register_fallback_commitment(_fallback_baseline(tipo_fallback=whitespace))
    assert excinfo.value.code == ERR_FALLBACK_COMMITMENT_NOT_HUMAN
    assert "tipo_fallback" in excinfo.value.message


@pytest.mark.parametrize("non_string", _NON_STRING_VARIANTS)
def test_fallback_commitment_non_string_tipo_fallback_refuses(non_string: object) -> None:
    """A NON-string tipo_fallback normalizes to '' and refuses -- the pre-fix bare truthiness
    check (`if not tipo_fallback`) would have silently PASSED a truthy non-string."""
    with pytest.raises(AdequacaoError) as excinfo:
        register_fallback_commitment(_fallback_baseline(tipo_fallback=non_string))
    assert excinfo.value.code == ERR_FALLBACK_COMMITMENT_NOT_HUMAN
    assert "tipo_fallback" in excinfo.value.message


@pytest.mark.parametrize("whitespace", _WHITESPACE_VARIANTS)
def test_fallback_commitment_whitespace_only_justificativa_refuses(whitespace: str) -> None:
    with pytest.raises(AdequacaoError) as excinfo:
        register_fallback_commitment(_fallback_baseline(justificativa_fallback=whitespace))
    assert excinfo.value.code == ERR_FALLBACK_COMMITMENT_NOT_HUMAN
    assert "justificativa_fallback" in excinfo.value.message


@pytest.mark.parametrize("non_string", _NON_STRING_VARIANTS)
def test_fallback_commitment_non_string_justificativa_refuses(non_string: object) -> None:
    with pytest.raises(AdequacaoError) as excinfo:
        register_fallback_commitment(_fallback_baseline(justificativa_fallback=non_string))
    assert excinfo.value.code == ERR_FALLBACK_COMMITMENT_NOT_HUMAN
    assert "justificativa_fallback" in excinfo.value.message


@pytest.mark.parametrize("whitespace", _WHITESPACE_VARIANTS)
def test_fallback_commitment_whitespace_only_referencia_regulatoria_refuses(whitespace: str) -> None:
    with pytest.raises(AdequacaoError) as excinfo:
        register_fallback_commitment(_fallback_baseline(referencia_regulatoria=whitespace))
    assert excinfo.value.code == ERR_FALLBACK_COMMITMENT_NOT_HUMAN
    assert "referencia_regulatoria" in excinfo.value.message


@pytest.mark.parametrize("non_string", _NON_STRING_VARIANTS)
def test_fallback_commitment_non_string_referencia_regulatoria_refuses(non_string: object) -> None:
    with pytest.raises(AdequacaoError) as excinfo:
        register_fallback_commitment(_fallback_baseline(referencia_regulatoria=non_string))
    assert excinfo.value.code == ERR_FALLBACK_COMMITMENT_NOT_HUMAN
    assert "referencia_regulatoria" in excinfo.value.message


@pytest.mark.parametrize("whitespace", _WHITESPACE_VARIANTS)
def test_fallback_commitment_whitespace_only_responsavel_id_refuses(whitespace: str) -> None:
    with pytest.raises(AdequacaoError) as excinfo:
        register_fallback_commitment(_fallback_baseline(responsavel_id=whitespace))
    assert excinfo.value.code == ERR_FALLBACK_COMMITMENT_NOT_HUMAN
    assert "responsavel_id" in excinfo.value.message


@pytest.mark.parametrize("non_string", _NON_STRING_VARIANTS)
def test_fallback_commitment_non_string_responsavel_id_refuses(non_string: object) -> None:
    with pytest.raises(AdequacaoError) as excinfo:
        register_fallback_commitment(_fallback_baseline(responsavel_id=non_string))
    assert excinfo.value.code == ERR_FALLBACK_COMMITMENT_NOT_HUMAN
    assert "responsavel_id" in excinfo.value.message


@pytest.mark.parametrize("whitespace", _WHITESPACE_VARIANTS)
def test_fallback_commitment_both_responsavel_and_justificativa_whitespace_refuses(
    whitespace: str,
) -> None:
    """Both responsavel_id AND justificativa_fallback whitespace-only -- both missing fields
    named in the guard's error message."""
    with pytest.raises(AdequacaoError) as excinfo:
        register_fallback_commitment(
            _fallback_baseline(responsavel_id=whitespace, justificativa_fallback=whitespace)
        )
    assert excinfo.value.code == ERR_FALLBACK_COMMITMENT_NOT_HUMAN
    assert "responsavel_id" in excinfo.value.message
    assert "justificativa_fallback" in excinfo.value.message


@pytest.mark.parametrize("whitespace", [" ", "\t", "\n", "  \t\n"])
def test_fallback_commitment_whitespace_only_decisao_refuses(whitespace: str) -> None:
    """Whitespace-only decisao_remediacao normalizes to '' -> != COMPROMISSO_FALLBACK ->
    refuses (the engine's own gateway default routing is not itself a human decision)."""
    with pytest.raises(AdequacaoError) as excinfo:
        register_fallback_commitment(_fallback_baseline(decisao_remediacao=whitespace))
    assert excinfo.value.code == ERR_FALLBACK_COMMITMENT_NOT_HUMAN
    assert "decisao_remediacao" in excinfo.value.message


def test_fallback_commitment_padded_valid_literal_normalizes_and_registers() -> None:
    """Whitespace-PADDED but otherwise exact literal/fields normalize via `_norm_str` and
    still register (pins the normalization behavior -- this is NOT a bypass, it is the
    documented, intentional consequence of `.strip()`)."""
    result = register_fallback_commitment(
        _fallback_baseline(
            decisao_remediacao=" COMPROMISSO_FALLBACK ",
            tipo_fallback=" livre_escolha ",
            justificativa_fallback=" Sem prestador na regiao — RN 259 ",
            referencia_regulatoria=" RN 259 ",
            responsavel_id=" gestor-001 ",
        )
    )
    assert result["compromisso_fallback_registrado"] is True


@pytest.mark.parametrize(
    "decision",
    [
        "compromisso_fallback",
        "Compromisso_Fallback",
        "COMPROMISSO_FALLBACK_X",
        "XCOMPROMISSO_FALLBACK",
        "MONITORAR_OK ",
    ],
)
def test_fallback_commitment_non_exact_decisao_literal_still_refuses(decision: str) -> None:
    """Exact-match discipline survives normalization: case variants/substrings of the decision
    literal never satisfy the guard."""
    with pytest.raises(AdequacaoError) as excinfo:
        register_fallback_commitment(_fallback_baseline(decisao_remediacao=decision))
    assert excinfo.value.code == ERR_FALLBACK_COMMITMENT_NOT_HUMAN
