"""`prazoComprovacaoUsoVencido` no dicionario K1 1.6.0 (rco b8444f52, PROG-RCO-39; PR-A): fora do montador.

Na 1.6.0 a derivada da OPME de urgencia passa a sair do termo de cadastro `prazoApresentacaoContratualDias`
(o prazo do contrato vigente do prestador) e do termo de cadastro `autorizacaoExtemporanea` (concedida =
falso; em analise ou ausente = nulo); qualquer termo ausente da nulo, e nulo fica pendente em
`exigir_documento`, nunca glosa (casos C101 e C102 de `validacao/casos/urgencia/urg_roteamento_opme.json` do
rco nesse commit).

As tres decisoes que a recebem (`urgencia`, `ortopedia-opme`, `otorrino-cabeca-pescoco`) estao em dominios
que o montador NAO mapeia: `montar_entradas` levanta `DominioNaoSuportadoError` (caso para pessoa) e nenhum
valor da derivada chega a tabela, com qualquer fato. Por isso ela nao entra em `FORMULAS_PENDENTES`, que
lista as formulas pendentes dos dominios MAPEADOS (`test_rco_k1_2_3_conformidade.py`: formulas publicadas do
recorte = `FORMULAS` | `FORMULAS_PENDENTES`). Mapear um desses dominios exige implementar a formula com
estes nulos.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from maezo.platform.rco_entradas import DicionarioEntradasK1, ler_dicionario_entradas
from maezo.platform.rco_manifesto import DecisaoK1
from maezo.tools.workers import rco_montador as mt

PRAZO = "prazoComprovacaoUsoVencido"
RECORTE_1_6_0 = (
    Path(__file__).resolve().parents[3]
    / "fixtures"
    / "rco"
    / "entradas-1.6.0-recorte"
    / "entradas-1.6.0-recorte.json"
)
DOMINIOS_COM_A_DERIVADA = {"urgencia", "ortopedia-opme", "otorrino-cabeca-pescoco"}


@pytest.fixture(scope="module")
def dic() -> DicionarioEntradasK1:
    return ler_dicionario_entradas(RECORTE_1_6_0.read_bytes())


def _tabelas(dic: DicionarioEntradasK1) -> list[tuple[str, str]]:
    """(dominio, decisao) de cada tabela que o dicionario 1.6.0 diz receber a derivada (`tabelas`, rco
    b8444f52: `dmn/<dominio>/<arquivo>.dmn#<decisao>`)."""
    out = []
    for tabela in dic.entradas[PRAZO].tabelas:
        caminho, _, decisao = tabela.partition("#")
        out.append((caminho.split("/")[1], decisao))
    return out


def test_a_derivada_so_aparece_em_dominios_nao_mapeados(dic: DicionarioEntradasK1) -> None:
    assert {dominio for dominio, _ in _tabelas(dic)} == DOMINIOS_COM_A_DERIVADA
    assert not DOMINIOS_COM_A_DERIVADA & set(mt.MAPEAMENTOS)
    assert PRAZO not in mt.FORMULAS and PRAZO not in mt.FORMULAS_PENDENTES
    assert all(PRAZO not in mapa for mapa in mt.MAPEAMENTOS.values())


def test_dominio_nao_mapeado_barra_a_decisao_antes_de_ler_fatos(dic: DicionarioEntradasK1) -> None:
    """Nulo -> nunca negativa, pela porta do dominio: `montar_entradas` recusa o dominio
    (`DominioNaoSuportadoError`, caso para pessoa) ANTES de ler qualquer fato, entao nenhum valor da derivada
    (nem `False`, que deixaria a OPME sem comprovacao sem pendencia, nem `True`, que a glosaria) chega a
    tabela, com quaisquer fatos. Os fatos aqui sao vazios de proposito: eles nao sao lidos."""
    fatos = mt.FatosDoCaso(guia={}, versao_k3_pinada=(1, 15))
    for dominio, decisao_id in _tabelas(dic):
        decisao = DecisaoK1(
            id=f"rco_{decisao_id}",
            arquivo=f"dmn/{decisao_id}.dmn",
            caminho_consumidor=f"spec/processes/dmn/rco/{decisao_id}.dmn",
            sha256="sha256:" + "0" * 64,
            saidas=("decisao",),
            regras={},
            dominio=dominio,
            entradas=("dataAtendimento", PRAZO),
        )
        with pytest.raises(mt.DominioNaoSuportadoError, match=dominio):
            mt.montar_entradas(decisao, dic, fatos)


def test_dominio_mapeado_que_declarar_a_derivada_e_recusado(dic: DicionarioEntradasK1) -> None:
    """Se uma tabela de dominio mapeado passar a declarar a derivada, o pin e recusado ate o montador ser
    revisto (incidente, sem valor), nunca calculado por formula que o montador nao tem."""
    problemas = mt.conferir_mapeamento(dic, "regras-gerais", ("dataAtendimento", PRAZO))
    assert problemas == [f"{PRAZO}: entrada sem mapeamento no dominio 'regras-gerais'"]


def test_a_formula_publicada_na_1_6_0_manda_nulo_sem_termo(dic: DicionarioEntradasK1) -> None:
    formula = dic.entradas[PRAZO].formula
    assert formula is not None
    assert formula.formula.startswith("nulo se autorizacaoExtemporanea = em_analise ou ausente; ")
    assert formula.formula.endswith("(nulo se faltar um termo)")
    origem = json.loads(RECORTE_1_6_0.read_text(encoding="utf-8"))
    de_onde = next(e for e in origem["entradas"] if e["nome_dmn"] == PRAZO)["de_onde"]
    assert "nunca há valor presumido e nulo nunca glosa" in de_onde
