"""`urgenciaAposVinteQuatroHorasCobertura` no montador (dicionario K1 1.4.0, K1 2.3.0; DL-0183).

A formula publicada (rco d9b8adfa, `docs/dicionario/regras-gerais.yaml`) decide primeiro pelas DATAS: dias
corridos entre a data de referencia do atendimento e o inicio da cobertura; 2 ou mais -> verdadeiro; 0 ou
menos -> falso; 1 -> pelas horas so com as DUAS horas e o parametro, senao nulo; nulo sem uma das datas. A
data de referencia vem do tipo da guia (nunca deduzido); nenhuma hora e inferida; o parametro nao tem padrao
no codigo. Os cenarios C047-C051 do proprio rco estao em
`tests/unit/platform/test_rco_k1_2_3_conformidade.py`.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest

from maezo.platform.rco_entradas import DicionarioEntradasK1, RegimeFormulaK1, ler_dicionario_entradas
from maezo.platform.rco_manifesto import ManifestoK1InvalidoError
from maezo.tools.workers import rco_montador as mt
from tests.unit.platform import rco_k1_2_3_recorte as rec

URGENCIA = "urgenciaAposVinteQuatroHorasCobertura"
INICIO = "2026-11-02"


@pytest.fixture(scope="module")
def dic() -> DicionarioEntradasK1:
    return rec.dicionario()


def _montar(
    dic: DicionarioEntradasK1,
    *,
    tipo: str | None = mt.TIPO_GUIA_CONTA_SP_SADT,
    inicio: object = INICIO,
    hora_inicio: object = "10:00",
    tiss: dict[str, object] | None = None,
    horas: object = 24,
    sem_produtor: frozenset[str] = frozenset(),
    guia: dict[str, object] | None = None,
) -> tuple[object, str | None]:
    cadastro: dict[str, object] = {}
    if inicio is not None:
        cadastro["data_inicio_cobertura"] = inicio
    if hora_inicio is not None:
        cadastro["hora_inicio_cobertura"] = hora_inicio
    fatos = mt.FatosDoCaso(
        guia=guia if guia is not None else {"data_atendimento": "2026-11-03"},
        versao_k3_pinada=(1, 15),
        cadastro=cadastro,
        tiss=tiss if tiss is not None else {"data_execucao": "2026-11-03", "hora_inicial": "10:00:00"},
        tipo_guia=tipo,
        parametros={} if horas is None else {"horas_minimas_urgencia": horas},
        termos_sem_produtor=sem_produtor,
    )
    montadas = mt.montar_entradas(rec.manifesto().decisoes["rco_geral_carencia"], dic, fatos)
    assert montadas.fontes[URGENCIA] == mt.Fonte.FORMULA.value
    return montadas.variaveis[URGENCIA], montadas.nulos.get(URGENCIA)


@pytest.mark.parametrize(
    ("data_referencia", "esperado"),
    [("2026-11-04", True), ("2026-12-31", True), ("2026-11-02", False), ("2026-10-30", False)],
)
def test_decide_pelas_datas_sem_hora_nem_parametro(
    dic: DicionarioEntradasK1, data_referencia: str, esperado: bool
) -> None:
    # 2 dias ou mais: verdadeiro; 0 ou menos (inclusive atendimento antes da cobertura): falso. Nenhuma hora
    # nem o parametro sao lidos para decidir.
    valor, motivo = _montar(
        dic,
        tipo=mt.TIPO_GUIA_SOLICITACAO_SP_SADT,
        hora_inicio=None,
        tiss={"data_solicitacao": data_referencia},
        horas=None,
    )
    assert (valor, motivo) == (esperado, None)


@pytest.mark.parametrize(
    ("hora_inicio", "hora_inicial", "esperado"),
    [
        ("10:00", "10:00:00", True),  # exatamente 24 horas: >= vale
        ("10:00", "09:59:59", False),
        ("10:00:01", "10:00:00", False),
        ("00:00", "23:59", True),
        ("23:59:59", "23:59:58", False),
    ],
)
def test_um_dia_decide_pelas_horas(
    dic: DicionarioEntradasK1, hora_inicio: str, hora_inicial: str, esperado: bool
) -> None:
    valor, motivo = _montar(
        dic, hora_inicio=hora_inicio, tiss={"data_execucao": "2026-11-03", "hora_inicial": hora_inicial}
    )
    assert (valor, motivo) == (esperado, None)


def test_um_dia_usa_o_parametro_publicado_nao_um_numero_do_codigo(dic: DicionarioEntradasK1) -> None:
    # 23 horas: falso com 24, verdadeiro com 23 (o numero vem de quem chama, de configuracao).
    tiss = {"data_execucao": "2026-11-03", "hora_inicial": "09:00:00"}
    assert _montar(dic, tiss=tiss, horas=24) == (False, None)
    assert _montar(dic, tiss=tiss, horas=23) == (True, None)
    assert _montar(dic, tiss=tiss, horas=23.5) == (False, None)


@pytest.mark.parametrize(
    ("kwargs", "motivo"),
    [
        ({"hora_inicio": None}, "termo_da_formula_ausente"),
        ({"tiss": {"data_execucao": "2026-11-03"}}, "termo_da_formula_ausente"),
        ({"hora_inicio": "10h"}, "termo_hora_invalida"),
        ({"hora_inicio": "24:00"}, "termo_hora_invalida"),
        ({"hora_inicio": "10:60"}, "termo_hora_invalida"),
        ({"hora_inicio": "10:00:00.5"}, "termo_hora_invalida"),
        ({"tiss": {"data_execucao": "2026-11-03", "hora_inicial": "10:00:00Z"}}, "termo_hora_invalida"),
        ({"tiss": {"data_execucao": "2026-11-03", "hora_inicial": "10:00:00-03:00"}}, "termo_hora_invalida"),
        ({"hora_inicio": 10}, "termo_hora_invalida"),
        ({"horas": None}, "termo_da_formula_ausente"),
        ({"horas": True}, "termo_parametro_invalido"),
        ({"horas": "24"}, "termo_parametro_invalido"),
        ({"horas": -1}, "termo_parametro_invalido"),
        ({"horas": float("nan")}, "termo_parametro_invalido"),
        ({"sem_produtor": frozenset({"horasMinimasUrgencia"})}, "termo_sem_produtor_no_maezo"),
        ({"sem_produtor": frozenset({"horaInicioCobertura"})}, "termo_sem_produtor_no_maezo"),
    ],
)
def test_um_dia_sem_as_duas_horas_ou_o_parametro_e_nulo(
    dic: DicionarioEntradasK1, kwargs: dict[str, Any], motivo: str
) -> None:
    assert _montar(dic, **kwargs) == (None, motivo)


@pytest.mark.parametrize(
    ("kwargs", "motivo"),
    [
        ({"inicio": None}, "termo_da_formula_ausente"),
        ({"tiss": {"hora_inicial": "10:00:00"}}, "termo_da_formula_ausente"),
        ({"inicio": "2026-11-02T00:00:00"}, "termo_data_invalida"),
        ({"inicio": "02/11/2026"}, "termo_data_invalida"),
        ({"tiss": {"data_execucao": "2026-11-03T10:00:00-03:00"}}, "termo_data_invalida"),
        ({"sem_produtor": frozenset({"dataInicioCobertura"})}, "termo_sem_produtor_no_maezo"),
        ({"sem_produtor": frozenset({"dataReferenciaAtendimento"})}, "termo_sem_produtor_no_maezo"),
    ],
)
def test_sem_uma_das_datas_e_nulo(dic: DicionarioEntradasK1, kwargs: dict[str, Any], motivo: str) -> None:
    assert _montar(dic, **kwargs) == (None, motivo)


@pytest.mark.parametrize(
    "tipo", [None, "honorarios", "odontologia", "prorrogacao_internacao", "CONTA_SP_SADT"]
)
def test_tipo_de_guia_sem_referencia_e_nulo(dic: DicionarioEntradasK1, tipo: str | None) -> None:
    # Nunca se escolhe a data pela que estiver presente: sem tipo com referencia, nulo, mesmo com todas.
    tiss = {"data_execucao": "2026-12-31", "data_solicitacao": "2026-12-31", "data_atendimento": "2026-12-31"}
    assert _montar(dic, tipo=tipo, tiss=tiss) == (None, "tipo_guia_sem_data_de_referencia")


@pytest.mark.parametrize(
    ("tipo", "chave"),
    [
        (mt.TIPO_GUIA_SOLICITACAO_SP_SADT, "data_solicitacao"),
        (mt.TIPO_GUIA_SOLICITACAO_INTERNACAO, "data_solicitacao"),
        (mt.TIPO_GUIA_CONSULTA, "data_atendimento"),
        (mt.TIPO_GUIA_CONTA_SP_SADT, "data_execucao"),
        (mt.TIPO_GUIA_RESUMO_INTERNACAO, "data_execucao"),
    ],
)
def test_data_de_referencia_pelo_tipo_da_guia(dic: DicionarioEntradasK1, tipo: str, chave: str) -> None:
    # So o elemento do tipo da guia vale: as outras datas presentes (2 dias depois) nao decidem.
    tiss: dict[str, object] = {
        c: "2026-11-30" for c in ("data_solicitacao", "data_atendimento", "data_execucao")
    }
    tiss[chave] = INICIO  # 0 dia pelo elemento certo
    assert _montar(dic, tipo=tipo, tiss=tiss, hora_inicio=None, horas=None) == (False, None)


def test_solicitacao_e_consulta_nao_tem_hora_e_um_dia_fica_nulo(dic: DicionarioEntradasK1) -> None:
    # A solicitacao nao tem hora no XSD: `hora_inicial` presente na guia NAO e usada fora da conta.
    for tipo, chave in (
        (mt.TIPO_GUIA_SOLICITACAO_INTERNACAO, "data_solicitacao"),
        (mt.TIPO_GUIA_CONSULTA, "data_atendimento"),
    ):
        tiss = {chave: "2026-11-03", "hora_inicial": "23:00:00"}
        assert _montar(dic, tipo=tipo, tiss=tiss) == (None, "termo_da_formula_ausente")


def test_variavel_de_start_nao_e_a_data_de_referencia(dic: DicionarioEntradasK1) -> None:
    # `data_atendimento` do contrato de start (semente) nao entra: so `FatosDoCaso.tiss` pelo tipo da guia.
    assert _montar(dic, tiss={}, guia={"data_atendimento": "2026-12-31"}) == (
        None,
        "termo_da_formula_ausente",
    )


def test_formula_divergente_no_dicionario_recusa(dic: DicionarioEntradasK1) -> None:
    entrada = dic.entradas[URGENCIA]
    assert entrada.formula is not None
    outra = dataclasses.replace(entrada.formula, formula=entrada.formula.formula.replace(">= 2", ">= 3"))
    mutado = dataclasses.replace(
        dic, entradas={**dic.entradas, URGENCIA: dataclasses.replace(entrada, formula=outra)}
    )
    decisao = rec.manifesto().decisoes["rco_geral_carencia"]
    assert any(
        "diverge da implementada" in p
        for p in mt.conferir_mapeamento(mutado, decisao.dominio, decisao.entradas)
    )


def test_regimes_divergentes_no_dicionario_recusam(dic: DicionarioEntradasK1) -> None:
    entrada = dic.entradas["diasAteApresentacao"]
    assert entrada.formula is not None and entrada.formula.regimes
    trocado = tuple(
        dataclasses.replace(r, termo_data="dataExecucao") if r.regime == "internacao" else r
        for r in entrada.formula.regimes
    )
    sem_regime_com_termo = (
        *entrada.formula.regimes[:2],
        RegimeFormulaK1("consulta", ("guiaConsulta",), None, "x", "l"),
    )
    decisao = rec.manifesto().decisoes["rco_geral_prazos_conta"]
    for regimes in (trocado, sem_regime_com_termo):
        formula = dataclasses.replace(entrada.formula, regimes=regimes)
        mutado = dataclasses.replace(
            dic,
            entradas={**dic.entradas, "diasAteApresentacao": dataclasses.replace(entrada, formula=formula)},
        )
        problemas = mt.conferir_mapeamento(mutado, decisao.dominio, decisao.entradas)
        assert any("regimes do dicionario divergem" in p for p in problemas), problemas
    assert mt.conferir_mapeamento(dic, decisao.dominio, decisao.entradas) == []


@pytest.mark.parametrize(
    "nome", ["motivoIsencaoCarencia", "cidIgualDlpDeclarada", "regime", "cidSecundarioInformado"]
)
def test_formulas_novas_nao_implementadas_ficam_nulas(dic: DicionarioEntradasK1, nome: str) -> None:
    assert nome in mt.FORMULAS_PENDENTES and nome not in mt.FORMULAS
    decisao = next(
        d for d in rec.manifesto().decisoes.values() if d.dominio == "regras-gerais" and nome in d.entradas
    )
    fatos = mt.FatosDoCaso(guia={"data_atendimento": "2026-11-03"}, versao_k3_pinada=(1, 15))
    montadas = mt.montar_entradas(decisao, dic, fatos)
    assert (montadas.variaveis[nome], montadas.nulos[nome]) == (None, "formula_publicada_nao_implementada")


# --- decisao da coordenacao (DL-0183): parametro ausente e transcricao PROVISORIA -----------------


def test_um_dia_sem_parametro_e_nulo_mesmo_com_as_duas_horas(dic: DicionarioEntradasK1) -> None:
    # O pacote nao publica o valor de `horasMinimasUrgencia`: nenhum numero no codigo, 1 dia vai nulo
    # (-> regulacao) mesmo com a hora do cadastro e a da guia presentes.
    tiss = {"data_execucao": "2026-11-03", "hora_inicial": "23:00:00"}
    assert _montar(dic, hora_inicio="01:00", tiss=tiss, horas=None) == (None, "termo_da_formula_ausente")
    # Com 2 dias ou mais (ou 0) as datas bastam e o parametro nao e lido.
    assert _montar(dic, tiss={"data_execucao": "2026-11-04"}, hora_inicio=None, horas=None) == (True, None)


def test_urgencia_sem_bloco_estruturado_no_pacote() -> None:
    """Falha DE PROPOSITO quando o pacote passar a publicar a referencia por tipo de guia (bloco estruturado
    na `formula` da urgencia em `entradas.json`): entao troque `TIPOS_GUIA_REFERENCIA_PROVISORIO` pela
    leitura do pacote (dicionario 1.5.0) e atualize este teste."""
    bruto = json.loads((rec.RECORTE / "entradas-recorte.json").read_text(encoding="utf-8"))
    (entrada,) = [e for e in bruto["entradas"] if e["nome_dmn"] == URGENCIA]
    assert set(entrada["formula"]) == {"formula", "termos"}, (
        "o pacote publicou bloco estruturado: troque a transcricao"
    )
    assert all(set(t) == {"nome", "origem", "fonte"} for t in entrada["formula"]["termos"])
    assert set(mt.TIPOS_GUIA_REFERENCIA_PROVISORIO) == {
        mt.TIPO_GUIA_SOLICITACAO_SP_SADT,
        mt.TIPO_GUIA_SOLICITACAO_INTERNACAO,
        mt.TIPO_GUIA_CONSULTA,
        mt.TIPO_GUIA_CONTA_SP_SADT,
        mt.TIPO_GUIA_RESUMO_INTERNACAO,
    }


@pytest.mark.parametrize("termo_data", ["dataReferenciaAtendimento", "dataInicioCobertura"])
def test_bloco_estruturado_publicado_para_a_urgencia_e_recusado(
    dic: DicionarioEntradasK1, termo_data: str
) -> None:
    # Qualquer bloco `regimes` publicado para a urgencia (igual ou nao a transcricao) faz o montador recusar
    # ate a troca ser feita: nunca se mistura a transcricao com o que o pacote diz.
    entrada = dic.entradas[URGENCIA]
    assert entrada.formula is not None
    regimes = (RegimeFormulaK1("conta_sp_sadt", ("guiaSP-SADT",), termo_data, "xsd"),)
    formula = dataclasses.replace(entrada.formula, regimes=regimes)
    mutado = dataclasses.replace(
        dic, entradas={**dic.entradas, URGENCIA: dataclasses.replace(entrada, formula=formula)}
    )
    decisao = rec.manifesto().decisoes["rco_geral_carencia"]
    assert any(
        "regimes do dicionario divergem" in p
        for p in mt.conferir_mapeamento(mutado, decisao.dominio, decisao.entradas)
    )
    fatos = mt.FatosDoCaso(guia={"data_atendimento": "2026-11-03"}, versao_k3_pinada=(1, 15))
    with pytest.raises(mt.MontadorEntradasError, match="regimes"):
        mt.montar_entradas(decisao, mutado, fatos)


def test_campo_estruturado_desconhecido_na_formula_e_recusado_pelo_leitor() -> None:
    bruto = json.loads((rec.RECORTE / "entradas-recorte.json").read_text(encoding="utf-8"))
    for e in bruto["entradas"]:
        if e["nome_dmn"] == URGENCIA:
            e["formula"]["referencia"] = [
                {"tipo_guia": "guiaConsulta", "termo_data": "dataReferenciaAtendimento"}
            ]
    with pytest.raises(ManifestoK1InvalidoError, match="desconhecidos \\['referencia'\\]"):
        ler_dicionario_entradas(json.dumps(bruto).encode("utf-8"))
