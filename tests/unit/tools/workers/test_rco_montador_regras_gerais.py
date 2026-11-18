"""`regime` e `cidSecundarioInformado` no montador (dicionario K1 1.5.0, rco aed187d4; DL-0184).

As duas formulas sao calculadas EXATAMENTE pelo texto publicado (`docs/dicionario/comum/regime.entrada.yaml` e
`docs/dicionario/regras-gerais.yaml` do rco), so de elementos da guia TISS entregues em `FatosDoCaso.tiss`:

- `regime`: internacao se `regimeInternacao = 1` (Tab 41) OU `regimeAtendimento = 03` (Tab 76); hospital_dia
  se `regimeInternacao = 2`; ambulatorial se `regimeAtendimento = 01`; pronto_socorro se `regimeAtendimento =
  04`; nulo nos demais codigos. Codigo exato (texto); os dois termos com regimes diferentes: nulo (a
  formula nao diz qual vale);
- `cidSecundarioInformado`: mais de um `diagnostico` em `dadosSaidaInternacao`; sem a lista (resumo de
  internacao nao extraido), nulo.

`cidIgualDlpDeclarada` e `motivoIsencaoCarencia` seguem nulas: `cidDeclaradoCpt` e `isencaoCadastro` nao tem
fonte no K3 (`test_rco_montador_urgencia.py::test_formulas_novas_nao_implementadas_ficam_nulas`). Os casos do
rco de `geral_itens_inclusos` trazem so a derivada pronta (`regime`, `cidSecundarioInformado`) e a decisao nao
esta no recorte: nao ha caso do rco a montar dos fatos para estas duas.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from maezo.platform.rco_entradas import DicionarioEntradasK1
from maezo.tools.workers import rco_montador as mt
from tests.unit.platform import rco_k1_2_3_recorte as rec

DECISAO = "rco_geral_itens_inclusos"


@pytest.fixture(scope="module")
def dic() -> DicionarioEntradasK1:
    return rec.dicionario()


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
    assert montadas.fontes[nome] == mt.Fonte.FORMULA.value
    return montadas.variaveis[nome], montadas.nulos.get(nome)


@pytest.mark.parametrize("nome", ["regime", "cidSecundarioInformado"])
def test_formula_implementada_e_a_publicada(dic: DicionarioEntradasK1, nome: str) -> None:
    publicada = dic.entradas[nome].formula
    impl = mt.FORMULAS[nome]
    assert publicada is not None and nome not in mt.FORMULAS_PENDENTES
    assert impl.texto == publicada.formula
    assert impl.termos == tuple((t.nome, t.origem) for t in publicada.termos)
    assert impl.desde == (1, 4, 0)
    decisao = rec.manifesto().decisoes[DECISAO]
    assert mt.conferir_mapeamento(dic, decisao.dominio, decisao.entradas) == []


@pytest.mark.parametrize("nome", ["regime", "cidSecundarioInformado"])
def test_formula_mudada_no_pacote_faz_o_montador_recusar(dic: DicionarioEntradasK1, nome: str) -> None:
    entrada = dic.entradas[nome]
    assert entrada.formula is not None
    for formula in (
        dataclasses.replace(entrada.formula, formula=entrada.formula.formula + " "),
        dataclasses.replace(entrada.formula, termos=entrada.formula.termos[:1] + entrada.formula.termos[:1]),
    ):
        mutado = dataclasses.replace(
            dic, entradas={**dic.entradas, nome: dataclasses.replace(entrada, formula=formula)}
        )
        decisao = rec.manifesto().decisoes[DECISAO]
        problemas = mt.conferir_mapeamento(mutado, decisao.dominio, decisao.entradas)
        assert any("diverge da implementada" in p for p in problemas), problemas
        with pytest.raises(mt.MontadorEntradasError):
            mt.montar_entradas(decisao, mutado, mt.FatosDoCaso(guia={}, versao_k3_pinada=(1, 15)))


# --- regime ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("tiss", "esperado"),
    [
        # cada codigo que a formula lista, so com o seu termo
        ({"regime_internacao": "1"}, ("internacao", None)),
        ({"regime_internacao": "2"}, ("hospital_dia", None)),
        ({"regime_atendimento": "03"}, ("internacao", None)),
        ({"regime_atendimento": "01"}, ("ambulatorial", None)),
        ({"regime_atendimento": "04"}, ("pronto_socorro", None)),
        # os "demais codigos" que a formula nomeia: nulo
        ({"regime_internacao": "3"}, (None, "codigo_sem_valor_na_formula")),
        ({"regime_atendimento": "02"}, (None, "codigo_sem_valor_na_formula")),
        ({"regime_atendimento": "05"}, (None, "codigo_sem_valor_na_formula")),
        # codigo exato: outra grafia, outra tabela ou codigo desconhecido e nulo, nunca corrigido
        ({"regime_atendimento": "1"}, (None, "codigo_sem_valor_na_formula")),
        ({"regime_atendimento": "3"}, (None, "codigo_sem_valor_na_formula")),
        ({"regime_internacao": "01"}, (None, "codigo_sem_valor_na_formula")),
        ({"regime_internacao": "03"}, (None, "codigo_sem_valor_na_formula")),
        ({"regime_atendimento": " 01"}, (None, "codigo_sem_valor_na_formula")),
        ({"regime_atendimento": "internacao"}, (None, "codigo_sem_valor_na_formula")),
        ({"regime_atendimento": "99"}, (None, "codigo_sem_valor_na_formula")),
        # codigo que nao e texto: nulo
        ({"regime_internacao": 1}, (None, "termo_fora_da_forma_do_elemento")),
        ({"regime_atendimento": True}, (None, "termo_fora_da_forma_do_elemento")),
        # os dois termos: cada clausula da formula avaliada; uma so satisfeita vale (o OU publicado)
        ({"regime_internacao": "1", "regime_atendimento": "03"}, ("internacao", None)),
        ({"regime_internacao": "1", "regime_atendimento": "02"}, ("internacao", None)),
        ({"regime_internacao": "1", "regime_atendimento": "05"}, ("internacao", None)),
        ({"regime_internacao": "3", "regime_atendimento": "03"}, ("internacao", None)),
        ({"regime_internacao": "3", "regime_atendimento": "01"}, ("ambulatorial", None)),
        ({"regime_internacao": "2", "regime_atendimento": "02"}, ("hospital_dia", None)),
        # duas clausulas com regimes diferentes: so ai a formula e muda sobre qual prevalece -> nulo
        ({"regime_internacao": "1", "regime_atendimento": "01"}, (None, "termos_da_formula_em_conflito")),
        ({"regime_internacao": "2", "regime_atendimento": "03"}, (None, "termos_da_formula_em_conflito")),
        ({"regime_internacao": "2", "regime_atendimento": "04"}, (None, "termos_da_formula_em_conflito")),
        ({"regime_internacao": "3", "regime_atendimento": "02"}, (None, "codigo_sem_valor_na_formula")),
        # um termo ausente (None ou vazio) nao conta
        ({"regime_internacao": None, "regime_atendimento": "04"}, ("pronto_socorro", None)),
        ({"regime_internacao": "", "regime_atendimento": "01"}, ("ambulatorial", None)),
        # sem termo: nulo
        ({}, (None, "termo_da_formula_ausente")),
        ({"regime_internacao": None, "regime_atendimento": "  "}, (None, "termo_da_formula_ausente")),
    ],
)
def test_regime_pela_formula(dic: DicionarioEntradasK1, tiss: dict[str, object], esperado: Any) -> None:
    assert _montar(dic, "regime", tiss) == esperado


def test_regime_termo_sem_produtor(dic: DicionarioEntradasK1) -> None:
    sem = frozenset({"regimeInternacao", "regimeAtendimento"})
    tiss = {"regime_internacao": "1", "regime_atendimento": "03"}
    assert _montar(dic, "regime", tiss, sem_produtor=sem) == (None, "termo_sem_produtor_no_maezo")
    # um sem produtor e o outro presente: vale o presente
    assert _montar(dic, "regime", {"regime_atendimento": "01"}, sem_produtor=sem - {"regimeAtendimento"}) == (
        "ambulatorial",
        None,
    )


def test_regime_nunca_vem_do_tipo_da_guia_nem_do_regime_do_prazo(dic: DicionarioEntradasK1) -> None:
    fatos = mt.FatosDoCaso(
        guia={"data_atendimento": "2026-11-03", "regime_atendimento": "internacao"},
        versao_k3_pinada=(1, 15),
        cadastro={"regime_atendimento": "internacao"},
        regime_guia=mt.REGIME_INTERNACAO,
        tipo_guia=mt.TIPO_GUIA_RESUMO_INTERNACAO,
    )
    montadas = mt.montar_entradas(rec.manifesto().decisoes[DECISAO], dic, fatos)
    assert (montadas.variaveis["regime"], montadas.nulos["regime"]) == (None, "termo_da_formula_ausente")


# --- cidSecundarioInformado -----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("diagnosticos", "esperado"),
    [
        (["K409"], (False, None)),
        (["K409", "T814"], (True, None)),
        (("K409", "T814", "I10"), (True, None)),
        (["K409", "T814", "I10", "E119"], (True, None)),
        # o XSD admite 0 ocorrencias, mas sem nem o principal e dado ausente: nulo (falso levaria a glosa)
        ([], (None, "grupo_de_saida_sem_diagnostico")),
        ((), (None, "grupo_de_saida_sem_diagnostico")),
        # fora da forma do XSD (0 a 4 ocorrencias de st_texto4): nulo, nunca contado
        (["K409", "T814", "I10", "E119", "Z00"], (None, "termo_fora_da_forma_do_elemento")),
        (["K409", "T8140"], (None, "termo_fora_da_forma_do_elemento")),
        (["K409", ""], (None, "termo_fora_da_forma_do_elemento")),
        (["K409", "  "], (None, "termo_fora_da_forma_do_elemento")),
        (["K409", None], (None, "termo_fora_da_forma_do_elemento")),
        (["K409", 410], (None, "termo_fora_da_forma_do_elemento")),
        ("K409", (None, "termo_fora_da_forma_do_elemento")),
        (2, (None, "termo_fora_da_forma_do_elemento")),
        # guia de resumo de internacao nao extraida (ou sem o grupo de saida): nulo
        (None, (None, "termo_da_formula_ausente")),
    ],
)
def test_cid_secundario_pela_formula(dic: DicionarioEntradasK1, diagnosticos: object, esperado: Any) -> None:
    assert _montar(dic, "cidSecundarioInformado", {"diagnosticos_saida_internacao": diagnosticos}) == esperado


def test_cid_secundario_sem_a_chave_e_sem_produtor(dic: DicionarioEntradasK1) -> None:
    assert _montar(dic, "cidSecundarioInformado", {}) == (None, "termo_da_formula_ausente")
    tiss = {"diagnosticos_saida_internacao": ["K409", "T814"]}
    assert _montar(dic, "cidSecundarioInformado", tiss, sem_produtor=frozenset({"diagnostico"})) == (
        None,
        "termo_sem_produtor_no_maezo",
    )


def test_pendentes_tem_o_motivo_da_fonte_ausente() -> None:
    assert "cidDeclaradoCpt sem fonte no K3" in mt.FORMULAS_PENDENTES["cidIgualDlpDeclarada"]
    assert "isencaoCadastro sem fonte no K3" in mt.FORMULAS_PENDENTES["motivoIsencaoCarencia"]
    for nome in ("cidIgualDlpDeclarada", "motivoIsencaoCarencia"):
        assert mt.MAPEAMENTOS["regras-gerais"][nome].fonte is mt.Fonte.FORMULA_PENDENTE
