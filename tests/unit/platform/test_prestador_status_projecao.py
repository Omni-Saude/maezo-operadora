"""MPTT P3 — o contrato de projecao terminal -> delta amarrado aos BPMN reais.

* censo: TODO `endEvent` de SP-OP-CRED-001 e SP-OP-FRAUDE-001 esta classificado, e nada alem
  deles (um terminal novo no BPMN reprova ate alguem classifica-lo E subir a versao);
* os terminais ADVERSOS humanos (descredenciado, substituicao, fraude confirmada + referrals) sao
  os unicos que produzem DESCREDENCIADO/CONFIRMADA;
* nenhuma projecao grava estado de leitura (DESCONHECIDO/INDISPONIVEL) nem "em curso".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from maezo.platform.prestador_status import projecao as pj
from maezo.platform.xml_safe import parse_xml_without_dtd

_ROOT = Path(__file__).resolve().parents[3]
_BPMN = _ROOT / "spec" / "processes" / "bpmn"
_NS = "{http://www.omg.org/spec/BPMN/20100524/MODEL}"


def _end_events(nome: str, processo: str) -> set[str]:
    raiz = parse_xml_without_dtd((_BPMN / nome).read_bytes())
    processos = [p for p in raiz.iter(f"{_NS}process") if p.get("id") == processo]
    assert len(processos) == 1, f"{nome}: processo {processo} nao encontrado"
    return {e.get("id", "") for e in processos[0].iter(f"{_NS}endEvent")}


@pytest.mark.parametrize(
    ("arquivo", "processo", "tabela"),
    [
        ("SP-OP-CRED-001_Descredenciamento.bpmn", pj.PROCESSO_CRED, pj.PROJECAO_CRED),
        ("SP-OP-FRAUDE-001_Investigacao_Fraude.bpmn", pj.PROCESSO_FRAUDE, pj.PROJECAO_FRAUDE),
    ],
)
def test_censo_todo_end_event_do_bpmn_esta_classificado(arquivo: str, processo: str, tabela: object) -> None:
    eventos = _end_events(arquivo, processo)
    assert eventos, "nao-vacuidade: o BPMN tem eventos finais"
    assert set(tabela) == eventos  # type: ignore[call-overload]


def test_so_os_terminais_adversos_humanos_produzem_descredenciado() -> None:
    descred = {k for k, v in pj.PROJECAO_CRED.items() if v.valor == pj.ValorSituacao.DESCREDENCIADO}
    assert descred == {"End_PrestadorDescredenciado", "End_SubstituicaoRegistrada"}
    cred = {k for k, v in pj.PROJECAO_CRED.items() if v.valor == pj.ValorSituacao.CREDENCIADO}
    assert cred == {"End_PrestadorCredenciado"}


def test_so_a_acusacao_humana_e_seus_referrals_produzem_fraude_confirmada() -> None:
    confirmada = {k for k, v in pj.PROJECAO_FRAUDE.items() if v.valor == pj.ValorFraude.CONFIRMADA}
    assert confirmada == {
        "End_FraudeConfirmadaHumano",
        "End_EncaminhadoCredenciamento",
        "End_EncaminhadoJuridico",
        "End_EncaminhadoContratual",
    }
    assert pj.PROJECAO_FRAUDE["End_MonitorarSemAcao"].valor == pj.ValorFraude.MONITORAR
    assert pj.PROJECAO_FRAUDE["End_ArquivadoSemIndicio"].valor == pj.ValorFraude.ARQUIVADA
    assert pj.PROJECAO_FRAUDE["End_CustodiaNaoSelada"].valor == pj.ValorFraude.ENCERRADA_SEM_DECISAO


def test_terminais_de_guarda_e_mantido_nao_mudam_status() -> None:
    for terminal in (
        "End_VinculoMantido",
        "End_CredenciamentoNegado",
        "End_CredPrestadorInvalido",
        "End_DecredBloqueadoNaoHumano",
        "End_CredGuardBloqueadoNaoHumano",
    ):
        assert pj.PROJECAO_CRED[terminal].classe is pj.ClasseTerminal.SEM_DELTA, terminal


def test_alerta_de_sla_nao_e_terminal_nos_dois_processos() -> None:
    assert pj.PROJECAO_CRED["End_RiscoSlaNotificado"].classe is pj.ClasseTerminal.NAO_TERMINAL
    assert pj.PROJECAO_FRAUDE["End_RiscoSlaNotificado"].classe is pj.ClasseTerminal.NAO_TERMINAL


def test_nenhum_valor_de_leitura_e_gravavel() -> None:
    gravaveis = {v.value for v in pj.ValorSituacao} | {v.value for v in pj.ValorFraude}
    assert not gravaveis & {"DESCONHECIDO", "INDISPONIVEL", "DESCRED_EM_CURSO", "ABERTA", "SUSPENSO"}


def test_terminal_fora_do_contrato_nao_e_adivinhado() -> None:
    assert pj.projetar(pj.Origem.CRED, "End_QualquerCoisaNova") is None
    assert pj.projetar(pj.Origem.FRAUDE, "End_PrestadorCredenciado") is None  # nao e' terminal do FRAUDE


def test_projecao_exige_delta_coerente() -> None:
    with pytest.raises(ValueError):
        pj.Projecao(pj.ClasseTerminal.DELTA)
    with pytest.raises(ValueError):
        pj.Projecao(pj.ClasseTerminal.SEM_DELTA, pj.Dimensao.SITUACAO, "CREDENCIADO")


def test_versao_e_estavel() -> None:
    # Mudar a tabela exige subir a versao: esta linha muda junto, conscientemente.
    assert pj.VERSAO_PROJECAO == "mptt-p3-projecao/v1"
