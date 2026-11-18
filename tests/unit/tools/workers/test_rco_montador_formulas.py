"""Formulas do dicionario de entradas K1 1.2.0 no montador (MZO-3; rco PR #169, f77f96ac).

`diasAteApresentacao` e `diasAteRecurso` sao calculadas EXATAMENTE pelo texto publicado
(`docs/dicionario/regras-gerais.yaml` do rco); as outras nove formulas publicadas ficam pendentes (nulas,
com o motivo). Prova:

- forma: o texto e os termos implementados sao os publicados; o mapeamento confere com a 1.2.0 e com a
  1.1.0 (na 1.1.0, anterior a formula, a entrada e nula por desenho);
- datas: internacao (alta) x ambulatorial (execucao), sem escolher pela data presente; nulos; intervalo
  negativo; so `AAAA-MM-DD` (instante com hora ou fuso entra nulo, nunca convertido nem truncado);
- o caminho inteiro ate a tolerancia (MZO-TOL): 30/31/60/61 dias com contrato de 30, so em
  `diasAlemDoPrazoContratual`;
- nunca a data de hoje: o montador nao le relogio, e termo ausente ou sem produtor e nulo.
"""

from __future__ import annotations

import ast
import dataclasses
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from maezo.platform.rco_entradas import DicionarioEntradasK1
from maezo.tools.workers import rco_montador as mt
from tests.unit.tools.workers import rco_recorte

PIN_SEM_K3 = (1, 15)
DIAS_ALEM = "diasAlemDoPrazoContratual"
ALTA = "2026-10-01"
EXECUCAO = "2026-10-05"


@pytest.fixture(scope="module")
def dic() -> DicionarioEntradasK1:
    return rco_recorte.dicionario_1_2_0()


@pytest.fixture(scope="module")
def dic_110() -> DicionarioEntradasK1:
    return rco_recorte.dicionario()


@pytest.fixture(scope="module")
def decisoes() -> dict[str, Any]:
    return rco_recorte.decisoes()


@pytest.fixture
def tolerancia_30(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configuracao de bordo (30) e DMN pinado sem tolerancia embutida (como em MZO-TOL)."""
    monkeypatch.setattr(mt, "tolerancia_conta_configurada", lambda: 30)
    monkeypatch.setattr(mt, "dmn_prazos_conta_bloqueia_tolerancia", lambda: None)


def _mais(dia: str, dias: int) -> str:
    return (date.fromisoformat(dia) + timedelta(days=dias)).isoformat()


def _fatos(
    *,
    regime: str | None = mt.REGIME_INTERNACAO,
    tiss: dict[str, object] | None = None,
    fora_tiss: dict[str, object] | None = None,
    prazo: object = 30,
    sem_produtor: frozenset[str] = frozenset(),
) -> mt.FatosDoCaso:
    cadastro: dict[str, object] = {} if prazo is None else {"prazo_apresentacao_contratual_dias": prazo}
    return mt.FatosDoCaso(
        guia={"data_atendimento": ALTA, "tipo_transacao": "ENVIO_LOTE_GUIAS"},
        versao_k3_pinada=PIN_SEM_K3,
        cadastro=cadastro,
        tiss=tiss or {},
        fora_tiss=fora_tiss or {},
        regime_guia=regime,
        termos_sem_produtor=sem_produtor,
    )


def _prazos(
    dic: DicionarioEntradasK1, decisoes: dict[str, Any], fatos: mt.FatosDoCaso
) -> mt.EntradasMontadas:
    return mt.montar_entradas(decisoes["rco_geral_prazos_conta"], dic, fatos)


# --- forma ------------------------------------------------------------------------------------------


def test_recorte_1_2_0_e_o_dicionario_publicado(dic: DicionarioEntradasK1) -> None:
    assert dic.versao == "1.2.0"
    assert set(dic.entradas) == set(rco_recorte.dicionario().entradas)
    assert dic.entradas["caraterAtendimento"].valores_maezo is not None
    assert dic.entradas["diasAteApresentacao"].valores_maezo is None


@pytest.mark.parametrize("nome", ["diasAteApresentacao", "diasAteRecurso", DIAS_ALEM])
def test_formula_implementada_e_a_publicada(dic: DicionarioEntradasK1, nome: str) -> None:
    publicada = dic.entradas[nome].formula
    impl = mt.FORMULAS[nome]
    assert publicada is not None
    assert impl.texto == publicada.formula
    assert impl.termos == tuple((t.nome, t.origem) for t in publicada.termos)


def test_toda_formula_publicada_esta_implementada_ou_pendente(dic: DicionarioEntradasK1) -> None:
    com_formula = {n for n, e in dic.entradas.items() if e.formula is not None}
    # 1.2.0: toda formula publicada e implementada ou pendente; as implementadas desde 1.2.0 estao nela. A
    # igualdade exata vale no dicionario 1.5.0 (test_rco_k1_2_3_conformidade.py, DL-0183).
    assert com_formula <= set(mt.FORMULAS) | set(mt.FORMULAS_PENDENTES)
    assert {n for n, f in mt.FORMULAS.items() if f.desde <= (1, 2, 0)} <= com_formula
    assert not set(mt.FORMULAS) & set(mt.FORMULAS_PENDENTES)
    for mapa in mt.MAPEAMENTOS.values():
        for nome, m in mapa.items():
            if nome in mt.FORMULAS_PENDENTES:
                assert m.fonte is mt.Fonte.FORMULA_PENDENTE, nome
            if nome in mt.FORMULAS:
                assert m.fonte is mt.Fonte.FORMULA, nome


def test_termos_nao_derivados_tem_chave_no_montador() -> None:
    chaves = {
        "cadastro": mt.CAMPOS_TERMO_CADASTRO,
        # termos `tiss` cujo elemento depende do tipo da guia (DL-0183) resolvem pela referencia do pacote
        "tiss": {**mt.CAMPOS_TERMO_TISS, **mt.TERMOS_POR_TIPO_GUIA},
        "fora_tiss": mt.CAMPOS_TERMO_FORA_TISS,
        # termos `parametro`: o `valor` publicado no pacote (dicionario 1.5.0), com a unidade declarada
        "parametro": {n for impl in mt.FORMULAS.values() for n in (impl.parametros or {})},
    }
    for impl in mt.FORMULAS.values():
        for nome, origem in impl.termos:
            if origem != "derivado":
                assert nome in chaves[origem], nome


@pytest.mark.parametrize("dicionario", ["1.1.0", "1.2.0"])
def test_mapeamento_confere_nas_duas_versoes(decisoes: dict[str, Any], dicionario: str) -> None:
    dic = rco_recorte.dicionario() if dicionario == "1.1.0" else rco_recorte.dicionario_1_2_0()
    for d in decisoes.values():
        assert mt.conferir_mapeamento(dic, d.dominio, d.entradas) == [], d.id


def test_formula_implementada_some_do_dicionario_novo_recusa(
    dic: DicionarioEntradasK1, decisoes: dict[str, Any]
) -> None:
    entradas = dict(dic.entradas)
    entradas["diasAteApresentacao"] = dataclasses.replace(entradas["diasAteApresentacao"], formula=None)
    sem = dataclasses.replace(dic, entradas=entradas)
    with pytest.raises(mt.MontadorEntradasError, match="nao publicada no dicionario 1.2.0"):
        _prazos(sem, decisoes, _fatos())


def test_dicionario_1_1_0_continua_nulo_por_desenho(
    dic_110: DicionarioEntradasK1, decisoes: dict[str, Any]
) -> None:
    """Pacote com o dicionario anterior a formula: nem as datas presentes fazem o montador calcular."""
    fatos = _fatos(
        tiss={"data_final_faturamento": ALTA}, fora_tiss={"data_recebimento_lote": _mais(ALTA, 45)}
    )
    montadas = _prazos(dic_110, decisoes, fatos)
    for nome in ("diasAteApresentacao", "diasAteRecurso"):
        assert montadas.variaveis[nome] is None
        assert montadas.nulos[nome] == "derivada_sem_formula"
        assert montadas.fontes[nome] == "nulo_por_desenho"


def test_formula_pendente_entra_nula(dic: DicionarioEntradasK1, decisoes: dict[str, Any]) -> None:
    fatos = mt.FatosDoCaso(guia={"data_atendimento": ALTA}, versao_k3_pinada=PIN_SEM_K3)
    montadas = mt.montar_entradas(decisoes["rco_diaria_cobertura"], dic, fatos)
    for nome in ("familiaDiariaTaxa", "internacaoObstetricaComAcompanhante"):
        assert montadas.variaveis[nome] is None
        assert montadas.nulos[nome] == "formula_publicada_nao_implementada"
        assert montadas.fontes[nome] == "formula_pendente"


# --- diasAteApresentacao: datas ---------------------------------------------------------------------


def test_internacao_conta_da_alta(dic: DicionarioEntradasK1, decisoes: dict[str, Any]) -> None:
    fatos = _fatos(
        tiss={"data_final_faturamento": ALTA, "data_execucao": EXECUCAO},
        fora_tiss={"data_recebimento_lote": "2026-11-15"},
    )
    montadas = _prazos(dic, decisoes, fatos)
    assert montadas.variaveis["diasAteApresentacao"] == 45  # 01/10 -> 15/11, nao 41 (da execucao)
    assert montadas.fontes["diasAteApresentacao"] == "formula"


def test_ambulatorial_conta_da_execucao(dic: DicionarioEntradasK1, decisoes: dict[str, Any]) -> None:
    fatos = _fatos(
        regime=mt.REGIME_AMBULATORIAL,
        tiss={"data_final_faturamento": ALTA, "data_execucao": EXECUCAO},
        fora_tiss={"data_recebimento_lote": "2026-11-15"},
    )
    assert _prazos(dic, decisoes, fatos).variaveis["diasAteApresentacao"] == 41


@pytest.mark.parametrize(
    ("regime", "tiss", "motivo"),
    [
        # regime da guia ausente ou fora do vocabulario: nulo, mesmo com as duas datas
        (None, {"data_final_faturamento": ALTA, "data_execucao": EXECUCAO}, "regime_da_guia_ausente"),
        ("misto", {"data_final_faturamento": ALTA}, "regime_da_guia_ausente"),
        # internacao sem a alta: nunca cai para a data de execucao
        (mt.REGIME_INTERNACAO, {"data_execucao": EXECUCAO}, "termo_da_formula_ausente"),
        # ambulatorial sem a execucao: nunca cai para a alta
        (mt.REGIME_AMBULATORIAL, {"data_final_faturamento": ALTA}, "termo_da_formula_ausente"),
        (mt.REGIME_INTERNACAO, {"data_final_faturamento": "  "}, "termo_da_formula_ausente"),
    ],
)
def test_sem_o_termo_do_regime_e_nulo(
    dic: DicionarioEntradasK1,
    decisoes: dict[str, Any],
    regime: str | None,
    tiss: dict[str, object],
    motivo: str,
) -> None:
    fatos = _fatos(regime=regime, tiss=tiss, fora_tiss={"data_recebimento_lote": "2026-11-15"})
    montadas = _prazos(dic, decisoes, fatos)
    assert montadas.variaveis["diasAteApresentacao"] is None
    assert montadas.nulos["diasAteApresentacao"] == motivo
    # nulo segue nulo ate a tabela (nunca 0, nunca glosa) e a auditoria diz por que
    assert montadas.variaveis[DIAS_ALEM] is None
    assert montadas.derivacoes[DIAS_ALEM]["motivoTolerancia"] == "termo_nulo"


def test_sem_recebimento_do_lote_e_nulo(dic: DicionarioEntradasK1, decisoes: dict[str, Any]) -> None:
    montadas = _prazos(dic, decisoes, _fatos(tiss={"data_final_faturamento": ALTA}))
    assert montadas.variaveis["diasAteApresentacao"] is None
    assert montadas.nulos["diasAteApresentacao"] == "termo_da_formula_ausente"
    assert montadas.variaveis[DIAS_ALEM] is None


def test_termo_sem_produtor_e_nulo_mesmo_com_a_chave(
    dic: DicionarioEntradasK1, decisoes: dict[str, Any]
) -> None:
    fatos = _fatos(
        tiss={"data_final_faturamento": ALTA},
        fora_tiss={"data_recebimento_lote": "2026-11-15"},
        sem_produtor=frozenset({"dataRecebimentoLote", "prazoApresentacaoContratualDias"}),
    )
    montadas = _prazos(dic, decisoes, fatos)
    assert montadas.variaveis["diasAteApresentacao"] is None
    assert montadas.nulos["diasAteApresentacao"] == "termo_sem_produtor_no_maezo"
    assert montadas.nulos[DIAS_ALEM] == "termo_sem_produtor_no_maezo"  # o prazo do contrato tambem


def test_prazo_contratual_sem_produtor(dic: DicionarioEntradasK1, decisoes: dict[str, Any]) -> None:
    fatos = _fatos(
        tiss={"data_final_faturamento": ALTA},
        fora_tiss={"data_recebimento_lote": "2026-11-15"},
        sem_produtor=frozenset({"prazoApresentacaoContratualDias"}),
    )
    montadas = _prazos(dic, decisoes, fatos)
    assert montadas.variaveis["diasAteApresentacao"] == 45
    assert montadas.variaveis[DIAS_ALEM] is None
    assert montadas.nulos[DIAS_ALEM] == "termo_sem_produtor_no_maezo"


def test_intervalo_negativo_passa_como_a_formula_diz(
    dic: DicionarioEntradasK1, decisoes: dict[str, Any], tolerancia_30: None
) -> None:
    """Recebimento ANTES da alta (dado incoerente): a formula publicada nao corta nem anula; o valor
    negativo esta no dominio (faixa -36500..36500) e fica dentro do prazo (nunca glosa)."""
    fatos = _fatos(tiss={"data_final_faturamento": ALTA}, fora_tiss={"data_recebimento_lote": "2026-09-26"})
    montadas = _prazos(dic, decisoes, fatos)
    assert montadas.variaveis["diasAteApresentacao"] == -5
    assert montadas.variaveis[DIAS_ALEM] == -35
    assert montadas.derivacoes[DIAS_ALEM]["motivoTolerancia"] == "dentro_do_prazo_contratual"


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [
        ("2026-10-01", date(2026, 10, 1)),
        (date(2026, 10, 1), date(2026, 10, 1)),
        ("2024-02-29", date(2024, 2, 29)),
        ("2026-02-29", None),  # nao existe
        ("2026-13-01", None),
        ("20261001", None),  # forma basica da ISO: fora do contrato
        ("2026-10-01T00:00:00", None),  # instante sem fuso
        ("2026-10-01T23:30:00-03:00", None),  # instante com fuso: o dia depende do fuso
        ("2026-10-01Z", None),
        (" 2026-10-01", None),
        (datetime(2026, 10, 1, tzinfo=UTC), None),  # datetime e subclasse de date: recusado
        (datetime(2026, 10, 1), None),  # instante ingenuo, de proposito
        (20261001, None),
        (None, None),
    ],
)
def test_data_so_aaaa_mm_dd(valor: object, esperado: date | None) -> None:
    assert mt.data_do_termo(valor) == esperado


def test_instante_com_fuso_e_nulo_nunca_truncado(dic: DicionarioEntradasK1, decisoes: dict[str, Any]) -> None:
    """23h30 de 15/11 em -03:00 e 02h30 de 16/11 em UTC: truncar daria 45 ou 46 conforme o fuso."""
    instante = datetime(2026, 11, 15, 23, 30, tzinfo=timezone(timedelta(hours=-3)))
    for recebido in (instante.isoformat(), instante):
        fatos = _fatos(tiss={"data_final_faturamento": ALTA}, fora_tiss={"data_recebimento_lote": recebido})
        montadas = _prazos(dic, decisoes, fatos)
        assert montadas.variaveis["diasAteApresentacao"] is None
        assert montadas.nulos["diasAteApresentacao"] == "termo_data_invalida"


def test_dias_ate_recurso(dic: DicionarioEntradasK1, decisoes: dict[str, Any]) -> None:
    fatos = _fatos(
        fora_tiss={"data_demonstrativo_analise": "2026-11-01", "data_recebimento_lote_recurso": "2026-11-20"}
    )
    montadas = _prazos(dic, decisoes, fatos)
    assert montadas.variaveis["diasAteRecurso"] == 19
    sem = _prazos(dic, decisoes, _fatos(fora_tiss={"data_recebimento_lote_recurso": "2026-11-20"}))
    assert sem.variaveis["diasAteRecurso"] is None
    assert sem.nulos["diasAteRecurso"] == "termo_da_formula_ausente"


# --- o caminho inteiro ate a tolerancia (MZO-TOL) ------------------------------------------------------


@pytest.mark.parametrize(
    ("dias_corridos", "bruto", "entregue", "motivo"),
    [
        (30, 0, 0, "dentro_do_prazo_contratual"),
        (31, 1, 0, "aplicada"),
        (60, 30, 0, "aplicada"),
        (61, 31, 1, "aplicada"),
    ],
)
@pytest.mark.parametrize("regime", [mt.REGIME_INTERNACAO, mt.REGIME_AMBULATORIAL])
def test_datas_ate_a_tolerancia_com_contrato_de_30(
    dic: DicionarioEntradasK1,
    decisoes: dict[str, Any],
    tolerancia_30: None,
    dias_corridos: int,
    bruto: int,
    entregue: int,
    motivo: str,
    regime: str,
) -> None:
    inicio = {"data_final_faturamento": ALTA} if regime == mt.REGIME_INTERNACAO else {"data_execucao": ALTA}
    fatos = _fatos(
        regime=regime, tiss=inicio, fora_tiss={"data_recebimento_lote": _mais(ALTA, dias_corridos)}, prazo=30
    )
    montadas = _prazos(dic, decisoes, fatos)
    assert montadas.variaveis["diasAteApresentacao"] == dias_corridos  # o termo chega intacto a tabela
    assert montadas.variaveis[DIAS_ALEM] == entregue
    assert dict(montadas.derivacoes[DIAS_ALEM]) == {
        "diasBrutos": bruto,
        "toleranciaAplicada": 30 if bruto > 0 else 0,
        "valorEntregue": entregue,
        "motivoTolerancia": motivo,
    }
    # a tolerancia so mexe em `diasAlemDoPrazoContratual` (nunca em `diasAteApresentacao` nem em outra)
    assert set(montadas.derivacoes) == {DIAS_ALEM}


def test_sem_pin_a_tolerancia_e_zero(dic: DicionarioEntradasK1, decisoes: dict[str, Any]) -> None:
    """Com a configuracao real e o pin real (`sem_pacote`, tabela fora do pin): a guarda zera a folga."""
    fatos = _fatos(
        tiss={"data_final_faturamento": ALTA}, fora_tiss={"data_recebimento_lote": _mais(ALTA, 45)}
    )
    montadas = _prazos(dic, decisoes, fatos)
    assert montadas.variaveis[DIAS_ALEM] == 15
    assert dict(montadas.derivacoes[DIAS_ALEM]) == {
        "diasBrutos": 15,
        "toleranciaAplicada": 0,
        "valorEntregue": 15,
        "motivoTolerancia": "dmn_nao_verificado",
    }


# --- nunca a data de hoje ----------------------------------------------------------------------------


@pytest.mark.parametrize("modulo", ["rco_montador.py", "rco_sombra.py"])
def test_montador_e_sombra_nao_leem_relogio(modulo: str) -> None:
    """Nenhuma data padrao: nem `now`/`today`/`utcnow` nem `time`. A data so vem dos fatos."""
    fonte = Path(mt.__file__).with_name(modulo).read_text(encoding="utf-8")
    proibidos = {"now", "today", "utcnow", "time", "time_ns", "monotonic"}
    for no in ast.walk(ast.parse(fonte)):
        if isinstance(no, ast.Attribute):
            assert no.attr not in proibidos, f"{modulo}: .{no.attr}"
        if isinstance(no, ast.ImportFrom | ast.Import):
            assert all(a.name not in {"time"} for a in no.names), modulo


def test_sem_recebimento_nunca_vira_hoje(dic: DicionarioEntradasK1, decisoes: dict[str, Any]) -> None:
    """Alta ha 45 dias e recebimento ausente: se o montador usasse hoje, daria um numero; da nulo."""
    alta = (datetime.now(UTC).date() - timedelta(days=45)).isoformat()
    montadas = _prazos(dic, decisoes, _fatos(tiss={"data_final_faturamento": alta}))
    assert montadas.variaveis["diasAteApresentacao"] is None
    assert montadas.variaveis[DIAS_ALEM] is None
