"""R-064 (F-017) — junta medica RN 424/2017 art. 3 I em SP-OP-AUTH-001, DARK.

Decisao do dono DEC-15 (issue #1083, registrada em R-034): junta SO em divergencia e NUNCA em
urgencia/emergencia. O que estes testes fixam:

* A regra (urgencia/emergencia -> junta vedada) mora na DMN `auth_junta_admissibility`, avaliada
  pelo ENGINE em `BRT_JuntaAdmissibilidade`. O worker `convene_junta` nao le o carater do
  atendimento: ele so publica, a partir da IMPLANTACAO (`MAEZO_AUTH_JUNTA_RN424_GUARD`, default
  OFF), se a guarda roteia (`junta_guarda_rn424_ativa`). Nenhuma regra de negocio em Python.
* Desligada (o padrao), a rota e' a de sempre: o caso vai a junta. O resultado da DMN fica em
  sombra.
* Ligada, junta vedada ou carater indeterminado volta a uma User Task HUMANA (coordenacao de
  auditoria medica). Nada aprova nem nega sozinho (itens hard authorization_denial e
  clinical_decision); a junta continua um corpo humano.
* Suspensao de prazo (art. 4 par. 2) NAO e' modelada: sem texto-base no repo nem no contrato.

Fixtures sinteticas; nenhum dado real.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

from maezo.platform.xml_safe import parse_xml_without_dtd
from maezo.tools.workers.auth import ConveneJuntaWorker, register_auth_workers
from maezo.tools.workers.base import ERR_DENIAL_NOT_HUMAN
from maezo.tools.workers.harness import FakeWorkerTransport, WorkerHarness

TOPIC = "operadora.auth.convene_junta"
FLAG_VAR = "junta_guarda_rn424_ativa"

_REPO = Path(__file__).resolve().parents[4]
_BPMN = _REPO / "spec/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn"
_DMN = _REPO / "spec/processes/dmn/auth_junta_admissibility.dmn"
_B = "{http://www.omg.org/spec/BPMN/20100524/MODEL}"
_C = "{http://camunda.org/schema/1.0/bpmn}"
_D = "{https://www.omg.org/spec/DMN/20191111/MODEL/}"


def _junta(**overrides: object) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "tenant_id": "tenant_sintetico",
        "numero_guia_tiss": "GUIA-SINTETICA-001",
        "decisao_auditor": "JUNTA_MEDICA",
        "carater_atendimento": "urgencia",
    }
    payload.update(overrides)
    return payload


# --- worker: dark by default, the deployment (never the payload) switches the guard ------------


def test_default_output_keeps_the_historical_convocation_and_says_guard_off() -> None:
    result = ConveneJuntaWorker().execute(_junta())
    assert result["status"] == "junta_convened"
    assert result["junta_group"] == "junta-medica"
    assert result[FLAG_VAR] is False
    assert "human_approved" not in result


def test_guard_on_marks_the_request_pending_admissibility_not_convened() -> None:
    result = ConveneJuntaWorker(rn424_guard=True).execute(_junta())
    assert result[FLAG_VAR] is True
    assert result["status"] == "junta_solicitada"
    assert result["junta_group"] == "junta-medica"
    assert "human_approved" not in result


@pytest.mark.parametrize("carater", ["urgencia", "emergencia", "eletivo", None, "", "U"])
def test_the_worker_never_applies_the_urgency_rule_itself(carater: object) -> None:
    """AGENTS.md regra 5: a vedacao e' DMN. O worker devolve o mesmo para qualquer carater."""
    on = ConveneJuntaWorker(rn424_guard=True)
    off = ConveneJuntaWorker()
    assert on.execute(_junta(carater_atendimento=carater)) == on.execute(
        _junta(carater_atendimento="eletivo")
    )
    assert off.execute(_junta(carater_atendimento=carater)) == off.execute(
        _junta(carater_atendimento="eletivo")
    )


@pytest.mark.parametrize("seeded", [True, False, "true", 1])
def test_a_seeded_process_variable_can_not_switch_the_guard(seeded: object) -> None:
    """COMPUTED OVERWRITES SEEDED: o valor vem so da implantacao e sobrescreve qualquer semente."""
    assert ConveneJuntaWorker().execute(_junta(**{FLAG_VAR: seeded}))[FLAG_VAR] is False
    assert ConveneJuntaWorker(rn424_guard=True).execute(_junta(**{FLAG_VAR: seeded}))[FLAG_VAR] is True


@pytest.mark.parametrize("enabled", [False, True])
def test_the_provenance_guard_is_unchanged_and_still_overwrites_the_seed(enabled: bool) -> None:
    result = ConveneJuntaWorker(rn424_guard=enabled).execute(
        _junta(decisao_auditor="APROVAR", **{FLAG_VAR: not enabled})
    )
    assert result["status"] == "blocked_by_guard"
    assert result["error_code"] == ERR_DENIAL_NOT_HUMAN
    assert result[FLAG_VAR] is enabled


@pytest.mark.parametrize("junk", ["true", 1, "1", object()])
def test_only_the_boolean_true_turns_the_guard_on(junk: object) -> None:
    assert ConveneJuntaWorker(rn424_guard=junk).execute(_junta())[FLAG_VAR] is False  # type: ignore[arg-type]


# --- composition: settings -> daemon bootstrap -> register_auth_workers -> the one worker -------


def _harness() -> WorkerHarness:
    return WorkerHarness(FakeWorkerTransport(), worker_id="r064-probe")


def test_settings_flag_is_dark_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from maezo.runtime.worker_runtime.settings import WorkerRuntimeSettings

    monkeypatch.delenv("MAEZO_AUTH_JUNTA_RN424_GUARD", raising=False)
    assert WorkerRuntimeSettings().auth_junta_rn424_guard is False
    monkeypatch.setenv("MAEZO_AUTH_JUNTA_RN424_GUARD", "true")
    assert WorkerRuntimeSettings().auth_junta_rn424_guard is True


@pytest.mark.parametrize("enabled", [False, True])
def test_register_auth_workers_threads_the_flag(enabled: bool) -> None:
    harness = _harness()
    register_auth_workers(harness, junta_rn424_guard=enabled)
    worker = harness.registry.get(TOPIC)
    assert type(worker) is ConveneJuntaWorker
    assert worker.execute(_junta())[FLAG_VAR] is enabled


@pytest.mark.parametrize("junk", ["true", "false", 1, "1"])
def test_a_junk_seam_value_does_not_switch_the_guard_on(junk: object) -> None:
    harness = _harness()
    register_auth_workers(harness, junta_rn424_guard=junk)
    assert harness.registry.get(TOPIC).execute(_junta())[FLAG_VAR] is False


@pytest.mark.parametrize("enabled", [False, True])
def test_the_daemon_bootstrap_threads_the_flag(enabled: bool) -> None:
    from maezo.runtime.worker_runtime.service import register_default_workers

    harness = _harness()
    register_default_workers(harness, junta_rn424_guard=enabled)
    assert harness.registry.get(TOPIC).execute(_junta())[FLAG_VAR] is enabled


# --- DMN: the rule (RN 424 art. 3 I), structurally ---------------------------------------------


def _dmn_rules() -> list[tuple[str, str, str]]:
    root = parse_xml_without_dtd(_DMN.read_bytes())
    table = root.find(f".//{_D}decisionTable")
    assert table is not None
    assert table.get("hitPolicy") == "FIRST"
    expr = table.find(f"{_D}input/{_D}inputExpression/{_D}text")
    assert expr is not None
    assert expr.text == "carater_atendimento"
    rules = []
    for rule in table.findall(f"{_D}rule"):
        entry = rule.find(f"{_D}inputEntry/{_D}text")
        outs = [o.findtext(f"{_D}text") or "" for o in rule.findall(f"{_D}outputEntry")]
        assert entry is not None
        rules.append(((entry.text or "").strip(), outs[0], outs[1]))
    return rules


def _first_match(carater: str | None) -> str:
    for entry, resultado, _motivo in _dmn_rules():
        if entry == "-" or (carater is not None and entry == f'"{carater}"'):
            return resultado.strip('"')
    raise AssertionError("FIRST sem linha casada")


@pytest.mark.parametrize("carater", ["urgencia", "emergencia"])
def test_dmn_forbids_the_junta_in_urgency_or_emergency(carater: str) -> None:
    assert _first_match(carater) == "VEDADA_URGENCIA_EMERGENCIA"


def test_dmn_admits_the_junta_only_for_an_elective_case() -> None:
    assert _first_match("eletivo") == "ADMITIDA"
    admitted = [entry for entry, resultado, _ in _dmn_rules() if resultado == '"ADMITIDA"']
    assert admitted == ['"eletivo"']


@pytest.mark.parametrize("carater", [None, "", "U", "Urgencia", "eletiva"])
def test_dmn_fails_closed_on_an_unknown_character(carater: str | None) -> None:
    assert _first_match(carater) == "CARATER_INDETERMINADO"


def test_dmn_has_no_approval_or_denial_output() -> None:
    """L0 hard: admissibilidade nunca vira merito."""
    for _entry, resultado, _motivo in _dmn_rules():
        assert resultado.strip('"') in {"ADMITIDA", "VEDADA_URGENCIA_EMERGENCIA", "CARATER_INDETERMINADO"}


# --- BPMN: the routing, structurally -------------------------------------------------------------


def _process() -> ET.Element:
    proc = parse_xml_without_dtd(_BPMN.read_bytes()).find(f"{_B}process")
    assert proc is not None
    return proc


def _by_id(proc: ET.Element) -> dict[str, ET.Element]:
    return {el.get("id", ""): el for el in proc.iter() if el.get("id")}


def _flows(proc: ET.Element) -> dict[str, ET.Element]:
    return {f.get("id", ""): f for f in proc.findall(f"{_B}sequenceFlow")}


def _outgoing(proc: ET.Element, source: str) -> list[ET.Element]:
    return [f for f in _flows(proc).values() if f.get("sourceRef") == source]


def _target(proc: ET.Element, source: str) -> str:
    (only,) = _outgoing(proc, source)
    return only.get("targetRef", "")


def test_bpmn_convocation_feeds_the_admissibility_decision_before_the_junta_task() -> None:
    proc = _process()
    nodes = _by_id(proc)
    assert _target(proc, "ST_ConvocarJunta") == "BRT_JuntaAdmissibilidade"
    brt = nodes["BRT_JuntaAdmissibilidade"]
    assert brt.tag == f"{_B}businessRuleTask"
    assert brt.get(f"{_C}decisionRef") == "auth_junta_admissibility"
    assert brt.get(f"{_C}resultVariable") == "junta_admissibilidade"
    assert brt.get(f"{_C}mapDecisionResult") == "singleResult"
    assert _target(proc, "BRT_JuntaAdmissibilidade") == "GW_JuntaAdmissivel"


def test_bpmn_only_the_admitted_or_dark_branch_reaches_the_junta() -> None:
    proc = _process()
    gw = _by_id(proc)["GW_JuntaAdmissivel"]
    assert gw.tag == f"{_B}exclusiveGateway"
    outs = {f.get("targetRef"): f for f in _outgoing(proc, "GW_JuntaAdmissivel")}
    assert set(outs) == {"UT_RegistrarParecerJunta", "GW_CoordenacaoMerge"}
    to_junta = outs["UT_RegistrarParecerJunta"]
    cond = to_junta.findtext(f"{_B}conditionExpression") or ""
    assert f"execution.getVariable('{FLAG_VAR}') != true" in cond
    assert "junta_admissibilidade.resultado == 'ADMITIDA'" in cond
    # fail-closed: o default (vedada, indeterminada, lixo) e' a decisao humana, nunca a junta
    assert gw.get("default") == outs["GW_CoordenacaoMerge"].get("id")
    assert outs["GW_CoordenacaoMerge"].find(f"{_B}conditionExpression") is None


def test_bpmn_a_forbidden_junta_returns_to_a_human_decision_never_to_an_automatic_end() -> None:
    proc = _process()
    nodes = _by_id(proc)
    assert _target(proc, "GW_CoordenacaoMerge") == "UT_CoordenacaoAssume"
    ut = nodes["UT_CoordenacaoAssume"]
    assert ut.tag == f"{_B}userTask"
    assert ut.get(f"{_C}candidateGroups") == "coordenacao-auditoria-medica"
    # a coordenacao continua decidindo pelo mesmo formulario (auth_decisao, portal intacto)
    assert _target(proc, "UT_CoordenacaoAssume") == "GW_DecisaoMerge"


def test_bpmn_the_decision_gateway_no_longer_joins_and_forks() -> None:
    proc = _process()
    flows = _flows(proc)
    into_decision = [f for f in flows.values() if f.get("targetRef") == "GW_DecisaoAuditor"]
    assert [f.get("sourceRef") for f in into_decision] == ["GW_DecisaoMerge"]
    into_merge = sorted(
        f.get("sourceRef", "") for f in flows.values() if f.get("targetRef") == "GW_DecisaoMerge"
    )
    assert into_merge == ["UT_AnaliseMedicoAuditor", "UT_CoordenacaoAssume", "UT_RegistrarParecerJunta"]


def test_bpmn_declared_incoming_and_outgoing_match_the_real_flows() -> None:
    proc = _process()
    flows = _flows(proc)
    for node_id, node in _by_id(proc).items():
        if node.tag in (f"{_B}sequenceFlow", f"{_B}process") or node_id not in {
            f.get("sourceRef") for f in flows.values()
        } | {f.get("targetRef") for f in flows.values()}:
            continue
        declared_in = sorted(e.text or "" for e in node.findall(f"{_B}incoming"))
        declared_out = sorted(e.text or "" for e in node.findall(f"{_B}outgoing"))
        real_in = sorted(fid for fid, f in flows.items() if f.get("targetRef") == node_id)
        real_out = sorted(fid for fid, f in flows.items() if f.get("sourceRef") == node_id)
        assert declared_in == real_in, node_id
        assert declared_out == real_out, node_id
