"""TB-E-17-a / EP-REG-19a: rescisao por arrependimento com declaracao destacada e desfecho humano."""

import re
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from lxml import etree

from maezo.gateway.action_execution import ActionExecutionGateway, load_action_approvals
from maezo.gateway.pep import HARD_ACTIONS, Decision, build_pep
from maezo.platform.saida_a_pedido.arrependimento import (
    ACAO_AUTONOMIA_DA_EFETIVACAO,
    CODIGOS_DE_RECUSA_DO_FORMULARIO,
    EfetivacaoAutonomaRecusada,
    PedidoRecusado,
    pedido_de_payload,
    protocolo_de_arrependimento,
    receber_pedido,
    recusar_efetivacao_por_agente,
    validar_declaracao,
)
from maezo.platform.saida_a_pedido.janela import AncoraDeFonte
from maezo.platform.saida_a_pedido.politica import PoliticaInvalida, carregar_politica_janela

RAIZ = Path(__file__).resolve().parents[3]
AGORA = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
NS = {"bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL"}


def _payload(**declaracao: Any) -> dict[str, Any]:
    base_declaracao: dict[str, Any] = {
        "tipo_do_ato": "declaracao_art8_par1",
        "ato_ref": "marcacao-1",
        "estado_inicial_renderizado": "desmarcada",
        "marcada_em": "2026-11-16T09:00:00-03:00",
        "texto_digest": "digest-nao-ratificado",
    }
    base_declaracao.update(declaracao)
    return {
        "pedido_ref": "pedido-1",
        "contrato_ref": "contrato-opaco-1",
        "abertura_ref": "clique-abertura",
        "envio_ref": "clique-envio",
        "assinatura_ref": "assinatura-1",
        "declaracao": base_declaracao,
    }


def test_withdrawal_requires_separate_declaration() -> None:
    for fundido in ("clique-abertura", "clique-envio"):
        with pytest.raises(PedidoRecusado) as exc:
            validar_declaracao(pedido_de_payload(_payload(ato_ref=fundido)))
        assert exc.value.code == "DECLARATION_FUSED_WITH_CLICK"


def test_withdrawal_form_requires_explicit_declaration() -> None:
    sem = _payload()
    del sem["declaracao"]
    with pytest.raises(PedidoRecusado) as exc:
        validar_declaracao(pedido_de_payload(sem))
    assert exc.value.code == "declaration_required"
    with pytest.raises(PedidoRecusado) as exc2:
        validar_declaracao(pedido_de_payload(_payload(estado_inicial_renderizado="marcada")))
    assert exc2.value.code == "declaration_required"


def test_cancel_yes_not_accepted_as_withdrawal() -> None:
    pedido = pedido_de_payload(_payload(tipo_do_ato="confirmacao_cancelamento", ato_ref="resposta-sim"))
    with pytest.raises(PedidoRecusado) as exc:
        validar_declaracao(pedido)
    assert exc.value.code == "CANCEL_CONFIRMATION_NOT_WITHDRAWAL_DECLARATION"


def test_withdrawal_never_autodenied() -> None:
    ancoras = [
        AncoraDeFonte(
            nome="vigencia_start",
            instante=datetime(2026, 10, 17, tzinfo=UTC),
            receipt_ref="rv",
            origem="fonte",
        ),
        AncoraDeFonte(
            nome="signed_at", instante=datetime(2026, 10, 7, tzinfo=UTC), receipt_ref="ra", origem="fonte"
        ),
    ]
    pedido = pedido_de_payload(_payload())
    for lista in (ancoras, []):
        recebimento = receber_pedido(pedido, ancoras=lista, recebido_em=AGORA)
        assert recebimento.encaminhamento == "route_to_human"
        assert re.match(r"^ARR-[0-9A-F]{4}-[0-9A-F]{4}$", recebimento.protocolo)
        assert recebimento.desfecho is None
        assert recebimento.situacao_janela in ("FORA", "INDETERMINADA")
    assert not any(
        re.search(r"(?i)prazo|janela|window|deadline|intempest", codigo)
        for codigo in CODIGOS_DE_RECUSA_DO_FORMULARIO
    )


def test_protocol_issued_on_submit() -> None:
    pedido = pedido_de_payload(_payload())
    primeiro = receber_pedido(pedido, ancoras=[], recebido_em=AGORA)
    segundo = receber_pedido(pedido, ancoras=[], recebido_em=AGORA)
    assert primeiro.protocolo == segundo.protocolo == protocolo_de_arrependimento("pedido-1")
    with pytest.raises(ValueError, match="pedido_ref"):
        protocolo_de_arrependimento("")
    with pytest.raises(ValueError):
        protocolo_de_arrependimento("   ")


def _fio_do_bpmn() -> tuple[etree._Element, dict[str, etree._Element]]:
    caminho = RAIZ / "spec/processes/bpmn/SP-OP-CANCEL-001_Cancelamento_Contrato.bpmn"
    raiz = etree.parse(str(caminho)).getroot()
    por_id = {e.get("id", ""): e for e in raiz.iter() if isinstance(e.tag, str) and e.get("id")}
    return raiz, por_id


def test_contract_termination_never_autonomous() -> None:
    # (1) PEP real: o item hard nega mesmo um agente L2
    pep = build_pep()
    contexto = {"agent_id": "lucas", "level": "L2"}
    assert pep.evaluate("contract_termination", contexto) is Decision.DENY
    # (2) a constante amarra a efetivacao ao item hard, no codigo e no arquivo congelado
    assert ACAO_AUTONOMIA_DA_EFETIVACAO == "contract_termination"
    assert ACAO_AUTONOMIA_DA_EFETIVACAO in HARD_ACTIONS
    congelado = yaml.safe_load(
        (RAIZ / "spec/policies/autonomy/_hard_frozen.yaml").read_text(encoding="utf-8")
    )
    assert ACAO_AUTONOMIA_DA_EFETIVACAO in {item["action"] for item in congelado["hard_items"]}
    # (3) a funcao de recusa SEMPRE levanta, com o codigo do item hard
    with pytest.raises(EfetivacaoAutonomaRecusada) as exc:
        recusar_efetivacao_por_agente(pep, agent_id="lucas", nivel="L2")
    assert exc.value.code == "CONTRACT_TERMINATION_L0_HARD"
    # (4) manifesto real: o topico de efetivacao e' vinculo_contratual_mudanca e nao e' liberado
    gateway = ActionExecutionGateway(load_action_approvals())
    topico = "operadora.cancel.effectuate_member_request"
    assert gateway.classify(topico) == "vinculo_contratual_mudanca"
    assert gateway.evaluate(gateway.classify(topico)).allow is False
    # (5) estrutura do BPMN real: a efetivacao so' e' alcancada por conclusao de User Task humana
    _, por_id = _fio_do_bpmn()
    fluxos = [e for e in por_id.values() if e.tag == f"{{{NS['bpmn']}}}sequenceFlow"]
    entradas = [f for f in fluxos if f.get("targetRef") == "ST_EffectuateMemberRequest"]
    assert [f.get("id") for f in entradas] == ["Flow_Execucao_Executado"]
    assert entradas[0].get("sourceRef") == "GW_ExecucaoCadastro"
    condicao = entradas[0].find("bpmn:conditionExpression", NS)
    assert condicao is not None
    assert condicao.text is not None
    assert "== 'EXECUTADO'" in condicao.text
    origens = [f.get("sourceRef", "") for f in fluxos if f.get("targetRef") == "GW_ExecucaoCadastro"]
    assert origens
    for origem in origens:
        assert por_id[origem].tag == f"{{{NS['bpmn']}}}userTask", origem


def test_withdrawal_rejects_health_and_usage_inputs() -> None:
    for extra in ({"uso_do_plano": True}, {"procedimentos_utilizados": ["x"]}, {"motivo": "texto"}):
        with pytest.raises(PedidoRecusado) as exc:
            pedido_de_payload({**_payload(), **extra})
        assert exc.value.code == "unknown_field"
    with pytest.raises(PedidoRecusado) as exc2:
        pedido_de_payload(_payload(cid="X"))
    assert exc2.value.code == "unknown_field"


def test_recebimento_has_no_desfecho_field_filled() -> None:
    recebimento = receber_pedido(pedido_de_payload(_payload()), ancoras=[], recebido_em=AGORA)
    assert asdict(recebimento)["desfecho"] is None


def test_withdrawal_never_auto_refused_on_window(tmp_path: Path) -> None:
    # TB-E-17-a: pedido no dia 9 apos a assinatura e dia 3 apos a vigencia, com regra de recusa automatica.
    politica_yaml = RAIZ / "spec/policies/saida-a-pedido/janela-arrependimento.yaml"
    dados = yaml.safe_load(politica_yaml.read_text(encoding="utf-8"))
    dados["fora_da_janela"] = "refused"
    caminho = tmp_path / "recusa-automatica.yaml"
    caminho.write_text(yaml.safe_dump(dados, allow_unicode=True, sort_keys=False), encoding="utf-8")
    with pytest.raises(PoliticaInvalida) as exc:
        carregar_politica_janela(caminho)
    assert exc.value.code == "AUTO_REFUSAL_FORBIDDEN_PENDING_Q3"
    assinatura = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    ancoras = [
        AncoraDeFonte(nome="signed_at", instante=assinatura, receipt_ref="ra", origem="fonte"),
        AncoraDeFonte(
            nome="vigencia_start", instante=assinatura + timedelta(days=6), receipt_ref="rv", origem="fonte"
        ),
    ]
    recebimento = receber_pedido(
        pedido_de_payload(_payload()), ancoras=ancoras, recebido_em=assinatura + timedelta(days=9)
    )
    assert recebimento.encaminhamento == "route_to_human"
    assert recebimento.desfecho is None
    assert recebimento.situacao_janela == "DENTRO"


def test_agent_cannot_terminate() -> None:
    # TB-E-17-a: agente executando termino -> recusa L0 `contract_termination`, em qualquer nivel.
    pep = build_pep()
    for nivel in ("L0", "L1", "L2", "L3"):
        with pytest.raises(EfetivacaoAutonomaRecusada) as exc:
            recusar_efetivacao_por_agente(pep, agent_id="lucas", nivel=nivel)
        assert exc.value.code == "CONTRACT_TERMINATION_L0_HARD"
