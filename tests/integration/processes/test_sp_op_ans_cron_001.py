"""SP-OP-ANS-CRON-001 — suite de integracao executavel (engine CIB Seven REAL).

Test spec: `docs/processes/test-specs/SP-OP-ANS-CRON-001.md`. Contrato:
`docs/processes/contracts/SP-OP-ANS-CRON-001.md` (secao "Calendario automatico desligado").

R-007 (F-002, F-121; decisao do dono DEC-03, issue #1081): o calendario AUTOMATICO esta desligado.
A definition do SIP saiu do BPMN (RN 639/2025 revoga a RN 551/2022, vigente 2026-03-02; DL-0029) e
as 4 definitions restantes (`-RN209`, `-RN388`, `-RN424TISS`, `-DIOPS`) estao
`isExecutable="false"` porque nenhuma tem norma primaria verificada. Esta suite prova, contra o
engine REAL, o efeito disso: deployar o arquivo NAO cria process definition nenhuma do agendador e,
portanto, nenhum job de timer-start. Os testes de tick (TimerStartEvent -> ST_ResolverCompetencia ->
fato `ans.cron_due`) sairam junto, porque o engine nao tem mais o que executar; a logica do resolver
e do publicador segue coberta no job unitario obrigatorio (`tests/unit/tools/workers/
test_ans_cron.py`, `tests/unit/tools/workers/test_events_ans_timer_identity.py` — resolver e
publicador REAIS sobre os inputs canonicos do BPMN) e volta para ca quando um gatilho for religado
por parecer COUNSEL (R-006).

FINDING #2 (fora do escopo): a `NotificationBridge` TEM a regra `ans.cron_due` ->
SP-OP-ANS-SUBMIT-001, mas nenhum consumidor vivo le `operadora.notifications.internal`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from maezo.platform.notification_bridge import NotificationBridge

from .engine_rest import EngineRest

pytestmark = pytest.mark.integration

_REPO = Path(__file__).resolve().parents[3]
_BPMN_CRON = _REPO / "spec/processes/bpmn/SP-OP-ANS-CRON-001_Agendador_Envios_ANS.bpmn"
_BPMN_SUBMIT = _REPO / "spec/processes/bpmn/SP-OP-ANS-SUBMIT-001_Envios_Periodicos_ANS.bpmn"
_DMN_CALENDAR = _REPO / "spec/processes/dmn/ans_calendar.dmn"
_DMN_SLA = _REPO / "spec/processes/dmn/ans_sla.dmn"
_DMN_ADMISS = _REPO / "spec/processes/dmn/ans_submission_admissibility.dmn"
_DMN_RETRY = _REPO / "spec/processes/dmn/ans_retry_policy.dmn"

_PROCESS_KEY_ANS_SUBMIT = "SP-OP-ANS-SUBMIT-001"

#: Todas as chaves que o agendador ja teve — inclusive a do SIP, removida por R-007.
_CRON_KEYS = {
    "SP-OP-ANS-CRON-001-RN124SIP",
    "SP-OP-ANS-CRON-001-RN209",
    "SP-OP-ANS-CRON-001-RN388",
    "SP-OP-ANS-CRON-001-RN424TISS",
    "SP-OP-ANS-CRON-001-DIOPS",
}


@pytest_asyncio.fixture
async def deploy_artifacts(engine: EngineRest) -> str:
    """Deploya o agendador CRON (1 arquivo BPMN, ZERO DMN) no engine real."""
    return await engine.deploy(_BPMN_CRON, name="SP-OP-ANS-CRON-001-qa")


@pytest_asyncio.fixture
async def deploy_cron_and_submit_artifacts(engine: EngineRest) -> str:
    """Deploya o agendador CRON + o processo de envio (SUBMIT) + suas 4 DMNs num unico deployment."""
    return await engine.deploy(
        _BPMN_CRON,
        _BPMN_SUBMIT,
        _DMN_CALENDAR,
        _DMN_SLA,
        _DMN_ADMISS,
        _DMN_RETRY,
        name="SP-OP-ANS-CRON-001-seam-qa",
    )


# ===========================================================================
# Fence estatico do BRIDGE (sem engine)
#
# Os fences estaticos BPMN<->worker vivem em `tests/unit/spec/test_ans_cron_timers_taxonomy.py`
# e `tests/unit/spec/test_ans_calendar_r007_fences.py` (job unitario obrigatorio).
# ===========================================================================


def test_notification_bridge_ans_cron_rule_now_wired() -> None:
    """FINDING #2 UPDATED (T2.6-7, `docs/design/T2.6-ans-submission-rescope.md` §1.5/§5):
    NotificationBridge tem exatamente uma regra para `event_type=ans.cron_due`, alvo
    SP-OP-ANS-SUBMIT-001.

    Este teste intencionalmente NAO prova o seam ao vivo ponta a ponta: nenhum consumidor rodando
    neste codebase le `operadora.notifications.internal` e chama `NotificationBridge.on_event(...)`
    — e por isso que o antigo `test_cron_dispara_fato_e_nao_inicia_submit_automaticamente` (contra o engine
    real) observava zero instancias de SP-OP-ANS-SUBMIT-001 auto-iniciadas (hoje, R-007, nem ha
    tick: o calendario automatico esta desligado). O
    predicado/business-key/mapeamento da propria regra e unit-testado em
    `tests/unit/platform/test_notification_bridge.py`.
    """
    bridge = NotificationBridge()
    rules = bridge.get_handoff("ans.cron_due")
    assert len(rules) == 1, (
        f"NotificationBridge deveria ter EXATAMENTE 1 regra para ans.cron_due pos-T2.6-7; encontrado: {rules}"
    )
    target_process, predicate = rules[0]
    assert target_process == _PROCESS_KEY_ANS_SUBMIT
    assert callable(predicate)
    # Anchor (t2-notify-integrity item 3): a regra exige tenant_id ALEM de report_type (todas as
    # regras do bridge — hoje 11 — exigem non_blank(tenant_id) via `_anchored`, simetrico com os
    # workers in-flow). Na
    # producao o tenant chega no fato via o stamp de deployment do publicador generico
    # (`register_events_workers` -> `make_publish_event_handler`), entao a regra ARMA ao vivo.
    assert predicate({"tenant_id": "amh", "report_type": "DIOPS_TRIMESTRAL"}) is True
    # Fail-closed: fato sem tenant_id (ou sem report_type) NAO dispara — nenhuma business key sensata.
    assert predicate({"report_type": "DIOPS_TRIMESTRAL"}) is False
    assert predicate({}) is False

    targets = {h["target_process"] for h in bridge.list_handoffs()}
    assert _PROCESS_KEY_ANS_SUBMIT in targets
    # Regressao: as 5 regras pre-T2.6-7 continuam intactas + as 2 novas (NIP + cron) + as 4 de
    # alerta de SLA -> SP-OP-ESCALATION-001 (R-104/WP-ALERTA-SLA-CANAL; lgpd, recurso, programa e
    # auth — `auth.notify_sla_risk` veio com o BT_AlertaSla do SP-OP-AUTH-001) = 11. A fonte da
    # verdade e `_SLA_ALERT_SPECS` + `_register_default_handoffs`: qualquer nova regra DEVE passar
    # por aqui e re-derivar este numero (drift V11: a 4a regra de SLA aterrissou sem bump).
    assert bridge.count_handoffs() == 11


# ===========================================================================
# Calendario automatico desligado (R-007) — engine REAL
# ===========================================================================


async def test_calendario_automatico_desligado_engine_nao_cria_definition_nem_timer(
    engine: EngineRest, deploy_artifacts: str
) -> None:
    """R-007 / DEC-03: o arquivo do agendador e aceito pelo engine (deploy nao falha, o recurso
    esta no deployment), mas NENHUMA process definition nasce dele — a do SIP porque saiu do BPMN,
    as outras 4 porque estao `isExecutable="false"`. Sem definition nao ha job de timer-start: o
    engine nao agenda nenhum tick do calendario."""
    recursos = await engine.deployment_resource_names(deploy_artifacts)
    assert _BPMN_CRON.name in recursos, f"o BPMN do agendador deveria estar no deployment: {recursos}"

    criadas = await engine.process_definition_keys_of_deployment(deploy_artifacts)
    assert criadas == set(), f"calendario automatico deveria estar desligado; definitions criadas: {criadas}"


async def test_deploy_conjunto_com_submit_so_cria_o_processo_de_envio(
    engine: EngineRest, deploy_cron_and_submit_artifacts: str
) -> None:
    """No deployment conjunto (CRON + SUBMIT + 4 DMNs), so SP-OP-ANS-SUBMIT-001 vira definition:
    o envio manual (backlog do SIP ate o 4o tri/2025 e os demais tipos) continua deployavel, e
    nenhuma chave do agendador aparece."""
    criadas = await engine.process_definition_keys_of_deployment(deploy_cron_and_submit_artifacts)
    assert criadas == {_PROCESS_KEY_ANS_SUBMIT}, criadas
    assert not criadas & _CRON_KEYS
