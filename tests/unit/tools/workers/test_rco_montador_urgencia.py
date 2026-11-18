"""`urgenciaAposVinteQuatroHorasCobertura` no montador (dicionario K1 1.4.0 e 1.5.0, K1 2.3.0; DL-0183).

A formula publicada (rco d9b8adfa, `docs/dicionario/regras-gerais.yaml`) decide primeiro pelas DATAS: dias
corridos entre a data de referencia do atendimento e o inicio da cobertura; 2 ou mais -> verdadeiro; 0 ou
menos -> falso; 1 -> pelas horas so com as DUAS horas e o parametro, senao nulo; nulo sem uma das datas. A
data de referencia vem do tipo da guia (nunca deduzido) pela `referencia_por_tipo_guia` do pacote, e o
parametro `horasMinimasUrgencia` e o `valor` publicado no termo (dicionario 1.5.0, rco aed187d4); nenhuma
hora e inferida e nenhum numero vem do codigo. Dicionario sem esses blocos (1.4.0) deixa a derivada nula. Os
cenarios C047-C051 do proprio rco estao em `tests/unit/platform/test_rco_k1_2_3_conformidade.py`.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest

from maezo.platform.rco_entradas import (
    DicionarioEntradasK1,
    ReferenciaGuiaK1,
    RegimeFormulaK1,
    ler_dicionario_entradas,
)
from maezo.platform.rco_manifesto import ManifestoK1InvalidoError
from maezo.tools.workers import rco_montador as mt
from tests.unit.platform import rco_k1_2_3_recorte as rec

URGENCIA = "urgenciaAposVinteQuatroHorasCobertura"
INICIO = "2026-11-02"


@pytest.fixture(scope="module")
def dic() -> DicionarioEntradasK1:
    return rec.dicionario()


def _com_formula(dic: DicionarioEntradasK1, **mudancas: Any) -> DicionarioEntradasK1:
    """O dicionario com a `formula` da urgencia trocada (so para os testes de contrato do montador)."""
    entrada = dic.entradas[URGENCIA]
    assert entrada.formula is not None
    formula = dataclasses.replace(entrada.formula, **mudancas)
    return dataclasses.replace(
        dic, entradas={**dic.entradas, URGENCIA: dataclasses.replace(entrada, formula=formula)}
    )


def _com_parametro(dic: DicionarioEntradasK1, **campos: Any) -> DicionarioEntradasK1:
    """O dicionario com o termo `horasMinimasUrgencia` trocado (valor, unidade, referencia, lacuna...)."""
    formula = dic.entradas[URGENCIA].formula
    assert formula is not None
    vazio: dict[str, Any] = {
        "valor": None,
        "unidade": None,
        "referencia": (),
        "valor_por_contrato": None,
        "lacuna": None,
    }
    termos = tuple(
        dataclasses.replace(t, **{**vazio, **campos}) if t.nome == "horasMinimasUrgencia" else t
        for t in formula.termos
    )
    return _com_formula(dic, termos=termos)


def _dicionario_1_4_0() -> DicionarioEntradasK1:
    """O recorte como seria no dicionario 1.4.0: sem `referencia_por_tipo_guia` e sem o valor dos termos
    `parametro` (lido pelo leitor real, que recusa esses campos na 1.4.0)."""
    bruto = json.loads((rec.RECORTE / "entradas-recorte.json").read_text(encoding="utf-8"))
    bruto["versao"] = "1.4.0"
    for e in bruto["entradas"]:
        if e["formula"] is not None:
            e["formula"].pop("referencia_por_tipo_guia", None)
            for t in e["formula"]["termos"]:
                for campo in ("valor", "unidade", "referencia", "valor_por_contrato", "lacuna"):
                    t.pop(campo, None)
    return ler_dicionario_entradas(json.dumps(bruto).encode("utf-8"))


def _montar(
    dic: DicionarioEntradasK1,
    *,
    tipo: str | None = mt.TIPO_GUIA_CONTA_SP_SADT,
    inicio: object = INICIO,
    hora_inicio: object = "10:00",
    tiss: dict[str, object] | None = None,
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
    # nem o parametro sao lidos para decidir (aqui o pacote publica o parametro como lacuna).
    valor, motivo = _montar(
        _com_parametro(dic, lacuna="L-teste"),
        tipo=mt.TIPO_GUIA_SOLICITACAO_SP_SADT,
        hora_inicio=None,
        tiss={"data_solicitacao": data_referencia},
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


def test_o_pacote_publica_o_valor_do_parametro_em_horas(dic: DicionarioEntradasK1) -> None:
    formula = dic.entradas[URGENCIA].formula
    assert formula is not None
    (termo,) = [t for t in formula.termos if t.nome == "horasMinimasUrgencia"]
    # O numero e o do rco (OMNI-GERAL-CAR-0003 `efeito.prazos`), lido do pacote; o montador confere a unidade
    assert (termo.origem, termo.valor, termo.unidade) == ("parametro", 24, "horas")
    assert mt.FORMULAS[URGENCIA].parametros == {"horasMinimasUrgencia": "horas"}


def test_um_dia_usa_o_valor_publicado_no_pacote_nao_um_numero_do_codigo(dic: DicionarioEntradasK1) -> None:
    # 23 horas decorridas: falso com o valor publicado (24); verdadeiro se o pacote publicasse 23.
    tiss = {"data_execucao": "2026-11-03", "hora_inicial": "09:00:00"}
    assert _montar(dic, tiss=tiss) == (False, None)
    assert _montar(_com_parametro(dic, valor=23, unidade="horas"), tiss=tiss) == (True, None)
    assert _montar(_com_parametro(dic, valor=23.5, unidade="horas"), tiss=tiss) == (False, None)


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
        ({"sem_produtor": frozenset({"horasMinimasUrgencia"})}, "termo_sem_produtor_no_maezo"),
        ({"sem_produtor": frozenset({"horaInicioCobertura"})}, "termo_sem_produtor_no_maezo"),
    ],
)
def test_um_dia_sem_as_duas_horas_e_nulo(
    dic: DicionarioEntradasK1, kwargs: dict[str, Any], motivo: str
) -> None:
    assert _montar(dic, **kwargs) == (None, motivo)


@pytest.mark.parametrize(
    ("campos", "motivo"),
    [
        ({"lacuna": "L-teste"}, "termo_da_formula_ausente"),  # o pacote nao tem o valor: nulo
        ({"valor": -1, "unidade": "horas"}, "termo_parametro_invalido"),
        ({"valor": float("nan"), "unidade": "horas"}, "termo_parametro_invalido"),
    ],
)
def test_um_dia_sem_valor_valido_no_pacote_e_nulo_mesmo_com_as_duas_horas(
    dic: DicionarioEntradasK1, campos: dict[str, Any], motivo: str
) -> None:
    tiss = {"data_execucao": "2026-11-03", "hora_inicial": "23:00:00"}
    assert _montar(_com_parametro(dic, **campos), hora_inicio="01:00", tiss=tiss) == (None, motivo)


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
    # Honorarios, odontologia, prorrogacao e anexos sao o item do pacote com data nula (lacuna).
    tiss = {"data_execucao": "2026-12-31", "data_solicitacao": "2026-12-31", "data_atendimento": "2026-12-31"}
    assert _montar(dic, tipo=tipo, tiss=tiss) == (None, "tipo_guia_sem_data_de_referencia")


#: (tipo do Maezo, `guia` publicado, chave da data, chave da hora) conforme a referencia do pacote aed187d4.
_REFERENCIAS = [
    (mt.TIPO_GUIA_SOLICITACAO_SP_SADT, "solicitação de SP/SADT", "data_solicitacao", None),
    (mt.TIPO_GUIA_SOLICITACAO_INTERNACAO, "solicitação de internação", "data_solicitacao", None),
    (mt.TIPO_GUIA_CONSULTA, "consulta", "data_atendimento", None),
    (mt.TIPO_GUIA_CONTA_SP_SADT, "SP/SADT (conta)", "data_execucao", "hora_inicial"),
    (mt.TIPO_GUIA_RESUMO_INTERNACAO, "resumo de internação (conta)", "data_execucao", "hora_inicial"),
]


def test_o_pacote_publica_a_referencia_de_cada_tipo_de_guia(dic: DicionarioEntradasK1) -> None:
    formula = dic.entradas[URGENCIA].formula
    assert formula is not None
    publicadas = {r.guia: r for r in formula.referencia_por_tipo_guia}
    for tipo, guia, chave_data, chave_hora in _REFERENCIAS:
        item = publicadas.pop(guia)
        assert mt.TIPOS_GUIA[tipo] == guia
        assert item.data is not None and mt.ELEMENTOS_TISS[item.data] == chave_data
        assert (mt.ELEMENTOS_TISS[item.hora] if item.hora else None) == chave_hora
    # O que sobra e o item sem data (lacuna L-prog-rco-urg-01): nenhum tipo do Maezo o usa.
    assert [(r.data, r.hora, bool(r.lacuna)) for r in publicadas.values()] == [(None, None, True)]


@pytest.mark.parametrize(("tipo", "guia", "chave", "chave_hora"), _REFERENCIAS)
def test_data_de_referencia_pelo_tipo_da_guia(
    dic: DicionarioEntradasK1, tipo: str, guia: str, chave: str, chave_hora: str | None
) -> None:
    # So o elemento do tipo da guia vale: as outras datas presentes (2 dias depois) nao decidem.
    tiss: dict[str, object] = {
        c: "2026-11-30" for c in ("data_solicitacao", "data_atendimento", "data_execucao")
    }
    tiss[chave] = INICIO  # 0 dia pelo elemento certo
    assert _montar(dic, tipo=tipo, tiss=tiss, hora_inicio=None) == (False, None)
    # 1 dia: a hora so existe nas contas; nas solicitacoes e na consulta o caso de 1 dia e nulo.
    tiss = {chave: "2026-11-03", "hora_inicial": "10:00:00"}
    esperado = (True, None) if chave_hora else (None, "termo_da_formula_ausente")
    assert _montar(dic, tipo=tipo, tiss=tiss) == esperado


def test_a_referencia_vem_do_pacote_e_nao_do_codigo(dic: DicionarioEntradasK1) -> None:
    # Se o pacote apontasse a consulta para `dataSolicitacao`, o montador seguiria o pacote.
    formula = dic.entradas[URGENCIA].formula
    assert formula is not None
    elemento = "tissGuiasV4_03_00.xsd#ctm_sp-sadtSolicitacaoGuia/dataSolicitacao"
    referencias = tuple(
        dataclasses.replace(r, data=elemento) if r.guia == "consulta" else r
        for r in formula.referencia_por_tipo_guia
    )
    mutado = _com_formula(dic, referencia_por_tipo_guia=referencias)
    tiss = {"data_atendimento": "2026-11-30", "data_solicitacao": INICIO}
    assert _montar(mutado, tipo=mt.TIPO_GUIA_CONSULTA, tiss=tiss, hora_inicio=None) == (False, None)
    assert _montar(dic, tipo=mt.TIPO_GUIA_CONSULTA, tiss=tiss, hora_inicio=None) == (True, None)


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


# --- dicionario 1.4.0 (sem os blocos) e contrato da leitura do pacote (DL-0183) ---------------------


def test_dicionario_1_4_0_sem_os_blocos_deixa_a_urgencia_nula() -> None:
    # Na 1.4.0 o pacote nao publica o valor do parametro nem a referencia por tipo de guia: sem transcricao
    # no codigo, a derivada e nula (-> regulacao) em todo ramo, inclusive o de 1 dia com as duas horas.
    antigo = _dicionario_1_4_0()
    assert antigo.versao == "1.4.0"
    formula = antigo.entradas[URGENCIA].formula
    assert formula is not None and formula.referencia_por_tipo_guia == ()
    assert all(t.valor is None for t in formula.termos)
    decisao = rec.manifesto().decisoes["rco_geral_carencia"]
    assert mt.conferir_mapeamento(antigo, decisao.dominio, decisao.entradas) == []
    tiss = {"data_execucao": "2026-11-03", "hora_inicial": "23:00:00"}
    assert _montar(antigo, hora_inicio="01:00", tiss=tiss) == (None, "referencia_por_tipo_guia_nao_publicada")
    assert _montar(antigo, tiss={"data_execucao": "2026-11-04"}) == (
        None,
        "referencia_por_tipo_guia_nao_publicada",
    )


def test_dicionario_1_4_0_com_referencia_e_sem_valor_deixa_so_o_dia_um_nulo(
    dic: DicionarioEntradasK1,
) -> None:
    # Sem o valor estruturado (como na 1.4.0) e com a referencia: as datas decidem, o caso de 1 dia e nulo.
    sem_valor = _com_parametro(dic)
    tiss = {"data_execucao": "2026-11-03", "hora_inicial": "23:00:00"}
    assert _montar(sem_valor, hora_inicio="01:00", tiss=tiss) == (None, "termo_da_formula_ausente")
    assert _montar(sem_valor, tiss={"data_execucao": "2026-11-04"}, hora_inicio=None) == (True, None)


@pytest.mark.parametrize(
    ("campos", "trecho"),
    [
        ({"valor": 1, "unidade": "dias"}, "publicado em 'dias'"),
        ({"referencia": ("regras/x.yaml#horas",)}, "publicado sem `valor`"),
        ({"valor_por_contrato": "contrato do prestador"}, "publicado sem `valor`"),
    ],
)
def test_parametro_publicado_em_forma_que_o_montador_nao_le_recusa(
    dic: DicionarioEntradasK1, campos: dict[str, Any], trecho: str
) -> None:
    decisao = rec.manifesto().decisoes["rco_geral_carencia"]
    problemas = mt.conferir_mapeamento(_com_parametro(dic, **campos), decisao.dominio, decisao.entradas)
    assert any(trecho in p for p in problemas), problemas
    assert mt.conferir_mapeamento(_com_parametro(dic, lacuna="L-x"), decisao.dominio, decisao.entradas) == []


@pytest.mark.parametrize(
    "troca",
    [
        {"guia": "guia nova com data"},  # tipo de guia publicado que o montador nao conhece
        {"data": "tissGuiasV4_03_00.xsd#ctm_consultaAtendimento/outraData"},  # elemento desconhecido
        {"hora": "tissGuiasV4_03_00.xsd#ctm_consultaAtendimento/horaNova"},
    ],
)
def test_referencia_publicada_sem_correspondencia_no_montador_recusa(
    dic: DicionarioEntradasK1, troca: dict[str, str]
) -> None:
    formula = dic.entradas[URGENCIA].formula
    assert formula is not None
    referencias = tuple(
        dataclasses.replace(r, **troca) if r.guia == "consulta" else r
        for r in formula.referencia_por_tipo_guia
    )
    decisao = rec.manifesto().decisoes["rco_geral_carencia"]
    problemas = mt.conferir_mapeamento(
        _com_formula(dic, referencia_por_tipo_guia=referencias), decisao.dominio, decisao.entradas
    )
    assert any("referencia da guia" in p or "sem referencia publicada" in p for p in problemas), problemas
    fatos = mt.FatosDoCaso(guia={"data_atendimento": "2026-11-03"}, versao_k3_pinada=(1, 15))
    with pytest.raises(mt.MontadorEntradasError):
        mt.montar_entradas(decisao, _com_formula(dic, referencia_por_tipo_guia=referencias), fatos)


def test_referencia_publicada_para_formula_que_nao_a_le_recusa(dic: DicionarioEntradasK1) -> None:
    entrada = dic.entradas["diasAteRecurso"]
    assert entrada.formula is not None
    item = ReferenciaGuiaK1("consulta", "tissGuiasV4_03_00.xsd#ctm_consultaAtendimento/dataAtendimento", None)
    formula = dataclasses.replace(entrada.formula, referencia_por_tipo_guia=(item,))
    mutado = dataclasses.replace(
        dic, entradas={**dic.entradas, "diasAteRecurso": dataclasses.replace(entrada, formula=formula)}
    )
    decisao = next(d for d in rec.manifesto().decisoes.values() if "diasAteRecurso" in d.entradas)
    problemas = mt.conferir_mapeamento(mutado, decisao.dominio, decisao.entradas)
    assert any("formula que nao a le" in p for p in problemas), problemas


@pytest.mark.parametrize("termo_data", ["dataReferenciaAtendimento", "dataInicioCobertura"])
def test_bloco_estruturado_publicado_para_a_urgencia_e_recusado(
    dic: DicionarioEntradasK1, termo_data: str
) -> None:
    # Bloco `regimes` publicado para a urgencia (que le `referencia_por_tipo_guia`, nao regimes) faz o
    # montador recusar: a forma publicada muda e o montador tem de ser revisto.
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
