"""`regime` e `cidSecundarioInformado` pelo texto do dicionario K1 1.5.1 (rco 1c5a2f2d, PR #187, PROG-RCO-38),
sem mudanca na 1.6.0 (rco b8444f52, PR #189, PROG-RCO-39).

Os recortes reais dos dois dicionarios estao em `tests/fixtures/rco/entradas-1.6.0-recorte/`. Cada texto e
conferido byte a byte (`_FormulaImplementada.vigente`) e cada clausula do texto novo tem um caso abaixo:

- `regime`: "o regime vem da guia do item: regimeInternacao (Tab 41) nas guias de solicitacao e de resumo de
  internacao; regimeAtendimento (Tab 76) nas guias SP/SADT e de consulta" + as clausulas por codigo da 1.4.0 +
  "se a entrada for montada com mais de uma guia e os regimes derivados divergirem, nulo". O que muda no
  calculo: um termo com regime e o outro num dos demais codigos (ou desconhecido) passa de regime a NULO (o
  texto nao diz se um regime nulo diverge; a duvida nunca vira valor). Na 1.5.0 segue o calculo antigo;
- `cidSecundarioInformado`: o texto da 1.5.1 publica o nulo sem nenhum diagnostico que o montador ja aplicava
  (DL-0184); o calculo e o mesmo nas duas versoes.

Valores so sinteticos.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from maezo.platform.rco_entradas import DicionarioEntradasK1, ler_dicionario_entradas
from maezo.tools.workers import rco_montador as mt
from tests.unit.platform import rco_k1_2_3_recorte as rec

DECISAO = "rco_geral_itens_inclusos"
PASTA = Path(__file__).resolve().parents[3] / "fixtures" / "rco" / "entradas-1.6.0-recorte"
VERSOES_NOVAS = ("1.5.1", "1.6.0")
MUDAM = ("regime", "cidSecundarioInformado")


def _dicionario(versao: str) -> DicionarioEntradasK1:
    return ler_dicionario_entradas((PASTA / f"entradas-{versao}-recorte.json").read_bytes())


@pytest.fixture(scope="module", params=VERSOES_NOVAS)
def dic(request: pytest.FixtureRequest) -> DicionarioEntradasK1:
    versao: str = request.param
    return _dicionario(versao)


def _montar(
    dic: DicionarioEntradasK1,
    nome: str,
    tiss: dict[str, object],
    *,
    sem_produtor: frozenset[str] = frozenset(),
) -> tuple[Any, str | None]:
    fatos = mt.FatosDoCaso(
        guia={"data_atendimento": "2026-11-03"},
        versao_k3_pinada=(1, 15),
        tiss=tiss,
        termos_sem_produtor=sem_produtor,
    )
    montadas = mt.montar_entradas(rec.manifesto().decisoes[DECISAO], dic, fatos)
    assert montadas.versao_dicionario == dic.versao
    assert montadas.fontes[nome] == mt.Fonte.FORMULA.value
    return montadas.variaveis[nome], montadas.nulos.get(nome)


# --- o texto conferido e o de cada versao -----------------------------------------------------------------
@pytest.mark.parametrize("nome", MUDAM)
def test_texto_vigente_e_o_publicado_byte_a_byte(dic: DicionarioEntradasK1, nome: str) -> None:
    publicada = dic.entradas[nome].formula
    assert publicada is not None
    impl = mt.FORMULAS[nome]
    texto, _ = impl.vigente(mt.versao_dicionario(dic))
    assert texto.encode("utf-8") == publicada.formula.encode("utf-8")
    assert impl.termos == tuple((t.nome, t.origem) for t in publicada.termos)
    decisao = rec.manifesto().decisoes[DECISAO]
    assert mt.conferir_mapeamento(dic, decisao.dominio, decisao.entradas) == []


@pytest.mark.parametrize("nome", MUDAM)
def test_versoes_anteriores_seguem_com_o_texto_antigo(nome: str) -> None:
    impl = mt.FORMULAS[nome]
    publicada_1_5_0 = rec.dicionario().entradas[nome].formula
    assert publicada_1_5_0 is not None
    for versao in ((1, 4, 0), (1, 5, 0)):
        assert impl.vigente(versao) == (impl.texto, impl.calcular)
    assert impl.texto == publicada_1_5_0.formula
    assert impl.vigente((1, 5, 1)) == impl.vigente((1, 6, 0))


def test_revisoes_em_ordem_e_depois_do_texto_original() -> None:
    for nome, impl in mt.FORMULAS.items():
        desdes = [r.desde for r in impl.revisoes]
        assert desdes == sorted(set(desdes)), nome
        assert all(d > impl.desde for d in desdes), nome
        textos = [impl.texto, *(r.texto for r in impl.revisoes)]
        assert len(set(textos)) == len(textos), nome
    assert {n for n, f in mt.FORMULAS.items() if f.revisoes} == set(MUDAM)


@pytest.mark.parametrize("nome", MUDAM)
def test_texto_antigo_num_dicionario_novo_e_recusado(dic: DicionarioEntradasK1, nome: str) -> None:
    entrada = dic.entradas[nome]
    assert entrada.formula is not None
    antiga = mt.FORMULAS[nome].texto
    for texto in (antiga, entrada.formula.formula + " "):
        formula = dataclasses.replace(entrada.formula, formula=texto)
        mutado = dataclasses.replace(
            dic, entradas={**dic.entradas, nome: dataclasses.replace(entrada, formula=formula)}
        )
        decisao = rec.manifesto().decisoes[DECISAO]
        problemas = mt.conferir_mapeamento(mutado, decisao.dominio, decisao.entradas)
        assert f"{nome}: formula do dicionario diverge da implementada; reveja o montador" in problemas
        with pytest.raises(mt.MontadorEntradasError):
            mt.montar_entradas(decisao, mutado, mt.FatosDoCaso(guia={}, versao_k3_pinada=(1, 15)))


@pytest.mark.parametrize("nome", MUDAM)
def test_texto_novo_num_dicionario_1_5_0_e_recusado(nome: str) -> None:
    dic = rec.dicionario()
    entrada = dic.entradas[nome]
    assert entrada.formula is not None
    novo = mt.FORMULAS[nome].vigente((1, 5, 1))[0]
    formula = dataclasses.replace(entrada.formula, formula=novo)
    mutado = dataclasses.replace(
        dic, entradas={**dic.entradas, nome: dataclasses.replace(entrada, formula=formula)}
    )
    decisao = rec.manifesto().decisoes[DECISAO]
    problemas = mt.conferir_mapeamento(mutado, decisao.dominio, decisao.entradas)
    assert any("diverge da implementada" in p for p in problemas)


# --- regime (1.5.1) ---------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("tiss", "esperado"),
    [
        # "regimeInternacao (Tab 41) nas guias de solicitacao e de resumo de internacao": so esse termo
        ({"regime_internacao": "1"}, ("internacao", None)),
        ({"regime_internacao": "2"}, ("hospital_dia", None)),
        # "regimeAtendimento (Tab 76) nas guias SP/SADT e de consulta": so esse termo
        ({"regime_atendimento": "03"}, ("internacao", None)),
        ({"regime_atendimento": "01"}, ("ambulatorial", None)),
        ({"regime_atendimento": "04"}, ("pronto_socorro", None)),
        # "nulo nos demais codigos (regimeAtendimento 02 e 05, regimeInternacao 3)"
        ({"regime_internacao": "3"}, (None, "codigo_sem_valor_na_formula")),
        ({"regime_atendimento": "02"}, (None, "codigo_sem_valor_na_formula")),
        ({"regime_atendimento": "05"}, (None, "codigo_sem_valor_na_formula")),
        # codigo desconhecido, de outra tabela ou com outra grafia: nulo, nunca corrigido
        ({"regime_internacao": "9"}, (None, "codigo_sem_valor_na_formula")),
        ({"regime_internacao": "01"}, (None, "codigo_sem_valor_na_formula")),
        ({"regime_atendimento": "1"}, (None, "codigo_sem_valor_na_formula")),
        ({"regime_atendimento": "99"}, (None, "codigo_sem_valor_na_formula")),
        ({"regime_atendimento": " 01"}, (None, "codigo_sem_valor_na_formula")),
        ({"regime_atendimento": "ambulatorial"}, (None, "codigo_sem_valor_na_formula")),
        # codigo que nao e texto: nulo
        ({"regime_internacao": 1}, (None, "termo_fora_da_forma_do_elemento")),
        ({"regime_atendimento": ["01"]}, (None, "termo_fora_da_forma_do_elemento")),
        ({"regime_internacao": "1", "regime_atendimento": 3}, (None, "termo_fora_da_forma_do_elemento")),
        # duas guias com o mesmo regime derivado: nao divergem, vale o regime
        ({"regime_internacao": "1", "regime_atendimento": "03"}, ("internacao", None)),
        # "mais de uma guia e os regimes derivados divergirem, nulo"
        ({"regime_internacao": "1", "regime_atendimento": "01"}, (None, "termos_da_formula_em_conflito")),
        ({"regime_internacao": "2", "regime_atendimento": "03"}, (None, "termos_da_formula_em_conflito")),
        ({"regime_internacao": "2", "regime_atendimento": "04"}, (None, "termos_da_formula_em_conflito")),
        # uma guia com regime e a outra num dos demais codigos ou desconhecido: nulo (na 1.5.0 dava o regime)
        ({"regime_internacao": "1", "regime_atendimento": "02"}, (None, "termos_da_formula_em_conflito")),
        ({"regime_internacao": "1", "regime_atendimento": "05"}, (None, "termos_da_formula_em_conflito")),
        ({"regime_internacao": "3", "regime_atendimento": "03"}, (None, "termos_da_formula_em_conflito")),
        ({"regime_internacao": "3", "regime_atendimento": "01"}, (None, "termos_da_formula_em_conflito")),
        ({"regime_internacao": "2", "regime_atendimento": "02"}, (None, "termos_da_formula_em_conflito")),
        ({"regime_internacao": "9", "regime_atendimento": "04"}, (None, "termos_da_formula_em_conflito")),
        # as duas guias sem regime derivado: nulo
        ({"regime_internacao": "3", "regime_atendimento": "02"}, (None, "codigo_sem_valor_na_formula")),
        # termo ausente (None ou vazio) nao e guia: vale a outra
        ({"regime_internacao": None, "regime_atendimento": "04"}, ("pronto_socorro", None)),
        ({"regime_internacao": "", "regime_atendimento": "01"}, ("ambulatorial", None)),
        ({"regime_internacao": "2", "regime_atendimento": "  "}, ("hospital_dia", None)),
        # sem termo: nulo
        ({}, (None, "termo_da_formula_ausente")),
        ({"regime_internacao": None, "regime_atendimento": ""}, (None, "termo_da_formula_ausente")),
    ],
)
def test_regime_pelo_texto_da_1_5_1(
    dic: DicionarioEntradasK1, tiss: dict[str, object], esperado: Any
) -> None:
    assert _montar(dic, "regime", tiss) == esperado


@pytest.mark.parametrize(
    ("tiss", "antes", "depois"),
    [
        ({"regime_internacao": "1", "regime_atendimento": "02"}, "internacao", None),
        ({"regime_internacao": "3", "regime_atendimento": "01"}, "ambulatorial", None),
        ({"regime_internacao": "1", "regime_atendimento": "03"}, "internacao", "internacao"),
        ({"regime_internacao": "2"}, "hospital_dia", "hospital_dia"),
    ],
)
def test_regime_so_muda_para_nulo_da_1_5_0_para_a_1_5_1(
    tiss: dict[str, object], antes: str, depois: str | None
) -> None:
    assert _montar(rec.dicionario(), "regime", tiss)[0] == antes
    for versao in VERSOES_NOVAS:
        assert _montar(_dicionario(versao), "regime", tiss)[0] == depois


def test_regime_termo_sem_produtor(dic: DicionarioEntradasK1) -> None:
    sem = frozenset({"regimeInternacao", "regimeAtendimento"})
    tiss: dict[str, object] = {"regime_internacao": "1", "regime_atendimento": "03"}
    assert _montar(dic, "regime", tiss, sem_produtor=sem) == (None, "termo_sem_produtor_no_maezo")
    assert _montar(dic, "regime", {"regime_atendimento": "01"}, sem_produtor=sem - {"regimeAtendimento"}) == (
        "ambulatorial",
        None,
    )


def test_regime_nunca_vem_do_tipo_da_guia(dic: DicionarioEntradasK1) -> None:
    fatos = mt.FatosDoCaso(
        guia={"data_atendimento": "2026-11-03", "regime_atendimento": "internacao"},
        versao_k3_pinada=(1, 15),
        cadastro={"regime_atendimento": "internacao"},
        regime_guia=mt.REGIME_INTERNACAO,
        tipo_guia=mt.TIPO_GUIA_CONSULTA,
    )
    montadas = mt.montar_entradas(rec.manifesto().decisoes[DECISAO], dic, fatos)
    assert (montadas.variaveis["regime"], montadas.nulos["regime"]) == (None, "termo_da_formula_ausente")


# --- cidSecundarioInformado (1.5.1) -----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("diagnosticos", "esperado"),
    [
        # "nulo se a guia de resumo de internacao nao foi extraida, se dadosSaidaInternacao esta ausente"
        (None, (None, "termo_da_formula_ausente")),
        # "ou se nao ha nenhum elemento diagnostico (sem o CID principal)"
        ([], (None, "grupo_de_saida_sem_diagnostico")),
        ((), (None, "grupo_de_saida_sem_diagnostico")),
        # "false se ha so o primeiro"
        (["A000"], (False, None)),
        # "true se ha ao menos um diagnostico alem do primeiro (ocorrencias 2 a 4)"
        (["A000", "B000"], (True, None)),
        (("A000", "B000", "C000"), (True, None)),
        (["A000", "B000", "C000", "D000"], (True, None)),
        # fora da forma do XSD (0 a 4 ocorrencias de st_texto4): nulo, nunca contado
        (["A000", "B000", "C000", "D000", "E000"], (None, "termo_fora_da_forma_do_elemento")),
        (["A000", "B0000"], (None, "termo_fora_da_forma_do_elemento")),
        (["A000", ""], (None, "termo_fora_da_forma_do_elemento")),
        (["  ", "B000"], (None, "termo_fora_da_forma_do_elemento")),
        (["A000", None], (None, "termo_fora_da_forma_do_elemento")),
        (["A000", 7], (None, "termo_fora_da_forma_do_elemento")),
        ("A000", (None, "termo_fora_da_forma_do_elemento")),
        (2, (None, "termo_fora_da_forma_do_elemento")),
    ],
)
def test_cid_secundario_pelo_texto_da_1_5_1(
    dic: DicionarioEntradasK1, diagnosticos: object, esperado: Any
) -> None:
    assert _montar(dic, "cidSecundarioInformado", {"diagnosticos_saida_internacao": diagnosticos}) == esperado


def test_cid_secundario_sem_a_chave_e_sem_produtor(dic: DicionarioEntradasK1) -> None:
    assert _montar(dic, "cidSecundarioInformado", {}) == (None, "termo_da_formula_ausente")
    tiss: dict[str, object] = {"diagnosticos_saida_internacao": ["A000", "B000"]}
    assert _montar(dic, "cidSecundarioInformado", tiss, sem_produtor=frozenset({"diagnostico"})) == (
        None,
        "termo_sem_produtor_no_maezo",
    )


def test_cid_secundario_tem_o_mesmo_calculo_nas_duas_versoes() -> None:
    impl = mt.FORMULAS["cidSecundarioInformado"]
    assert impl.vigente((1, 5, 1))[1] is impl.calcular


def test_prazo_de_comprovacao_da_1_6_0_nao_entra_no_montador() -> None:
    """O texto novo de `prazoComprovacaoUsoVencido` (1.6.0) nao toca o montador: fora de `FORMULAS` e de
    `FORMULAS_PENDENTES`, e nenhum dominio mapeado o declara."""
    dic = _dicionario("1.6.0")
    assert "prazoComprovacaoUsoVencido" in dic.entradas
    assert "prazoComprovacaoUsoVencido" not in mt.FORMULAS
    assert "prazoComprovacaoUsoVencido" not in mt.FORMULAS_PENDENTES
    assert all("prazoComprovacaoUsoVencido" not in mapa for mapa in mt.MAPEAMENTOS.values())
