"""Fences ESTATICOS de R-066 (F-071, F-137, F-138) — desfechos terminais do SP-OP-ANS-SUBMIT-001.

Contrato: `docs/processes/contracts/SP-OP-ANS-SUBMIT-001.md` (secoes "Topicos", "Variaveis de
saida" e "SLAs"). O que estes fences provam (leitura pura do XML + do contrato, sem engine):

  1. F-071: todo fim de negocio do processo publica `agents.events.anssubmit.completed` antes
     (ADR-0007, auditoria dupla engine + Kafka) e o conjunto de desfechos publicados e EXATAMENTE
     o enumerado no contrato — inclusive `falha_retransmissao`, que antes nunca saia.
  2. F-137: o timeout de `ICE_AguardarAck` vai a `UT_TratarNack` (humano), com a duracao
     declarada no contrato; a User Task humana tem alerta nao-interruptivo dirigido pela DMN
     `ans_sla` (`sla.sla_alerta`) — nenhuma duracao literal inventada.
  3. F-138: o ENGINE (nao so o formulario do portal) valida `justificativa_adiamento` antes do
     desfecho `adiado_humano` e consome `decisao_nack` (`retransmitir_manual` | `registrar_falha`,
     os mesmos literais do formulario `ans_nack`). Decisao ausente/invalida nunca vira terminal:
     volta a uma User Task humana.
  4. Nenhum gateway faz fork e join ao mesmo tempo (bpmnlint `no-gateway-join-fork`).

As metades que exigem engine (o JUEL de cada condicao avaliado de verdade, o evento publicado no
fim) vivem em `tests/integration/processes/test_sp_op_ans_submit_001.py`.
"""

from __future__ import annotations

import re
import typing
from collections import defaultdict
from pathlib import Path
from xml.etree.ElementTree import Element

from maezo.platform.xml_safe import parse_xml_without_dtd
from maezo.portal.contracts.models import AnsNackInputs

_REPO = Path(__file__).resolve().parents[3]
_BPMN = _REPO / "spec/processes/bpmn/SP-OP-ANS-SUBMIT-001_Envios_Periodicos_ANS.bpmn"
_CONTRATO = _REPO / "docs/processes/contracts/SP-OP-ANS-SUBMIT-001.md"

_B = "{http://www.omg.org/spec/BPMN/20100524/MODEL}"
_C = "{http://camunda.org/schema/1.0/bpmn}"
_COMPLETED = "agents.events.anssubmit.completed"

# Fins que NAO sao desfecho de negocio da instancia: o ramo nao-interruptivo do alerta de prazo
# (a revisao segue aberta) e os dois fins internos do subprocess de retry (o token volta ao fluxo
# principal pelo fim normal ou pelo boundary de erro).
_FINS_NAO_TERMINAIS = {"End_DeadlineRiskNotificado", "End_RetryOk", "End_RetryEsgotado"}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


class _Modelo:
    def __init__(self) -> None:
        root = parse_xml_without_dtd(_BPMN.read_bytes())
        processo = root.find(f"{_B}process")
        assert processo is not None
        self.nos: dict[str, Element] = {}
        self.fluxos: dict[str, Element] = {}
        for el in processo.iter():
            if not isinstance(el.tag, str) or el.get("id") is None:
                continue
            if _local(el.tag) == "sequenceFlow":
                self.fluxos[el.get("id", "")] = el
            elif _local(el.tag) not in {"process", "documentation"}:
                self.nos[el.get("id", "")] = el
        self.entrada: dict[str, list[str]] = defaultdict(list)
        self.saida: dict[str, list[str]] = defaultdict(list)
        for fid, fl in self.fluxos.items():
            self.saida[fl.get("sourceRef", "")].append(fid)
            self.entrada[fl.get("targetRef", "")].append(fid)

    def tipo(self, nid: str) -> str:
        return _local(self.nos[nid].tag)

    def origem(self, fid: str) -> str:
        return self.fluxos[fid].get("sourceRef", "")

    def destino(self, fid: str) -> str:
        return self.fluxos[fid].get("targetRef", "")

    def condicao(self, fid: str) -> str:
        return (self.fluxos[fid].findtext(f"{_B}conditionExpression") or "").strip()

    def parametros(self, nid: str) -> dict[str, str]:
        return {p.get("name", ""): (p.text or "").strip() for p in self.nos[nid].iter(f"{_C}inputParameter")}

    def timer(self, nid: str) -> str:
        return (self.nos[nid].findtext(f"{_B}timerEventDefinition/{_B}timeDuration") or "").strip()


def _desfechos_do_contrato() -> set[str]:
    texto = _CONTRATO.read_text(encoding="utf-8")
    linha = next(ln for ln in texto.splitlines() if f"`{_COMPLETED}`" in ln and "desfecho" in ln)
    trecho = linha.split("desfecho", 1)[1]
    return set(re.findall(r"`([a-z_]+)`", trecho))


def _duracao_ack_do_contrato() -> str:
    texto = _CONTRATO.read_text(encoding="utf-8")
    linha = next(ln for ln in texto.splitlines() if "`ICE_AguardarAck`" in ln and "Aguardo de ACK" in ln)
    m = re.search(r"ref `(P[0-9A-Z]+)`", linha)
    assert m, linha
    return m.group(1)


def _fins_terminais(m: _Modelo) -> list[str]:
    fins = [n for n in m.nos if m.tipo(n) == "endEvent" and n not in _FINS_NAO_TERMINAIS]
    assert fins, "nenhum endEvent encontrado — fence vacuo"
    return fins


def test_todo_fim_terminal_publica_completed_com_desfecho_do_contrato() -> None:
    """F-071: cada endEvent terminal e alcancado SO por uma service task que publica
    `anssubmit.completed` com `event_desfecho`; e o conjunto publicado e o do contrato."""
    m = _Modelo()
    publicados: set[str] = set()
    for fim in _fins_terminais(m):
        assert m.entrada[fim], fim
        for fid in m.entrada[fim]:
            pub = m.origem(fid)
            params = m.parametros(pub)
            assert m.tipo(pub) == "serviceTask", f"{fim} alcancado direto de {pub} sem publicar"
            assert params.get("event_topic") == _COMPLETED, f"{pub} -> {fim} nao publica completed"
            assert params.get("event_desfecho"), f"{pub} publica completed sem desfecho"
            publicados.add(params["event_desfecho"])
    assert publicados == _desfechos_do_contrato(), (publicados, _desfechos_do_contrato())


def test_falha_retransmissao_e_publicado_antes_do_fim() -> None:
    """F-071 (o caso do achado): `End_FalhaRetransmissao` so e alcancado depois do completed com
    `desfecho=falha_retransmissao`; `UT_TratarNack` nao liga mais direto ao fim."""
    m = _Modelo()
    origens = {m.origem(f) for f in m.entrada["End_FalhaRetransmissao"]}
    assert "UT_TratarNack" not in origens
    assert {m.parametros(o).get("event_desfecho") for o in origens} == {"falha_retransmissao"}


def test_timeout_do_ack_vai_a_humano_com_duracao_do_contrato() -> None:
    """F-137: `ICE_AguardarAck` expirado -> `UT_TratarNack` (contrato §SLAs), nunca a um
    terminal automatico; a duracao e a declarada no contrato."""
    m = _Modelo()
    destinos = {m.destino(f) for f in m.saida["ICE_AguardarAck"]}
    assert destinos == {"UT_TratarNack"}, destinos
    assert m.timer("ICE_AguardarAck") == _duracao_ack_do_contrato()


def test_tratar_nack_tem_alerta_de_prazo_da_dmn_ans_sla() -> None:
    """A espera humana em `UT_TratarNack` nao fica sem prazo: alerta nao-interruptivo com a
    duracao da DMN `ans_sla` (`sla.sla_alerta`, resolvida por `BRT_AnsSla` em todo caminho que
    chega ao envio), convergindo na notificacao de risco de prazo ja existente."""
    m = _Modelo()
    timers = [
        n
        for n in m.nos
        if m.tipo(n) == "boundaryEvent" and m.nos[n].get("attachedToRef") == "UT_TratarNack" and m.timer(n)
    ]
    assert len(timers) == 1, timers
    (bt,) = timers
    assert m.nos[bt].get("cancelActivity") == "false"
    assert m.timer(bt) == "${sla.sla_alerta}"
    assert {m.destino(f) for f in m.saida[bt]} == {"ST_NotificarDeadlineRisk"}


def test_adiamento_exige_justificativa_no_engine() -> None:
    """F-138: `adiado_humano` so e publicado por um fluxo cuja condicao exige `ADIAR_ENVIO` E
    `justificativa_adiamento` nao vazia; o default do gateway de decisao (decisao ausente,
    desconhecida ou adiamento sem justificativa) vai a uma User Task humana, nunca ao terminal."""
    m = _Modelo()
    (entrada_adiado,) = m.entrada["ST_PublishAdiado"]
    gw = m.origem(entrada_adiado)
    assert m.tipo(gw) == "exclusiveGateway"
    cond = m.condicao(entrada_adiado)
    assert "'ADIAR_ENVIO'" in cond and "justificativa_adiamento" in cond and "trim()" in cond, cond
    default = m.nos[gw].get("default")
    assert default and default != entrada_adiado
    assert m.tipo(m.destino(default)) == "userTask", m.destino(default)
    for fid in m.saida[gw]:
        if fid != default:
            assert m.condicao(fid), f"{fid} sem condicao"


def test_decisao_nack_consumida_pelo_engine_com_os_literais_do_formulario() -> None:
    """F-138: `UT_TratarNack` sai por um gateway que consome `decisao_nack`; os valores
    consumidos sao exatamente os do formulario `ans_nack` do portal; `retransmitir_manual` volta a
    correcao (re-assinatura humana antes de qualquer novo envio, nunca direto ao submit) e o
    default (decisao ausente/invalida) reabre a mesma User Task."""
    m = _Modelo()
    (saida,) = m.saida["UT_TratarNack"]
    gw = m.destino(saida)
    assert m.tipo(gw) == "exclusiveGateway"
    literais_form = set(typing.get_args(AnsNackInputs.model_fields["decisao_nack"].annotation))
    consumidos = set()
    for fid in m.saida[gw]:
        consumidos.update(re.findall(r"decisao_nack'\) == '([a-z_]+)'", m.condicao(fid)))
    assert consumidos == literais_form, (consumidos, literais_form)
    retransmitir = [f for f in m.saida[gw] if "'retransmitir_manual'" in m.condicao(f)]
    assert [m.destino(f) for f in retransmitir] == ["UT_CorrigirPendenciaEnvio"]
    default = m.nos[gw].get("default")
    assert default and m.destino(default) == "UT_TratarNack"


def test_nenhum_gateway_faz_fork_e_join() -> None:
    """bpmnlint `no-gateway-join-fork`: merge e split em gateways distintos."""
    m = _Modelo()
    gateways = [n for n in m.nos if m.tipo(n).endswith("Gateway")]
    assert gateways
    ruins = [g for g in gateways if len(m.entrada[g]) > 1 and len(m.saida[g]) > 1]
    assert not ruins, ruins


def test_retransmitir_manual_zera_aprovacao_anterior() -> None:
    """Revisao B1 (ADR-0007, nao-repudio): ao tomar `retransmitir_manual`, o engine zera
    `decisao_envio`/`revisor_id` da 1a assinatura (senao uma conclusao vazia da revisao re-aprovava
    e o 2o envio saia assinado pelo 1o revisor) e `retry_attempt` (politica de retry completa)."""
    m = _Modelo()
    (fid,) = [f for f in m.saida["GW_DecisaoNack"] if "'retransmitir_manual'" in m.condicao(f)]
    listeners = [
        (el.get("event"), el.get("expression")) for el in m.fluxos[fid].iter(f"{_C}executionListener")
    ]
    assert listeners == [
        ("take", "${execution.setVariable('decisao_envio', '')}"),
        ("take", "${execution.setVariable('revisor_id', '')}"),
        ("take", "${execution.setVariable('retry_attempt', 0)}"),
    ], listeners
