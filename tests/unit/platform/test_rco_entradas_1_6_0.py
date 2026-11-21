"""Leitor do dicionario de entradas K1 nas versoes 1.5.1 e 1.6.0 (rco 1c5a2f2d e b8444f52; PR-A).

Recortes REAIS em `tests/fixtures/rco/entradas-1.6.0-recorte/` (procedencia em `ORIGEM.json`): as 39
entradas dos dominios do montador mais `prazoComprovacaoUsoVencido`, com o `sha256_conteudo` do dicionario
completo. O leitor so ACRESCENTA versoes: versao desconhecida segue recusada (fail-closed). Diferencas por
versao, medidas nos dicionarios completos e fixadas aqui sobre os recortes:

- 1.5.0 -> 1.5.1: so `cidSecundarioInformado` e `regime` mudam (texto de `de_onde` e de `formula.formula`);
- 1.5.1 -> 1.6.0: so `prazoComprovacaoUsoVencido` muda; dois termos de cadastro passam a trazer `referencia`.

Ler a versao nao e pinar: o montador confere a formula publicada (`conferir_mapeamento`) e, desde a 1.5.1,
recusa `regime` e `cidSecundarioInformado` ate ser revisto (fixado abaixo, para o PR do pin).
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from maezo.platform import rco_entradas as ent
from maezo.platform.rco_manifesto import ManifestoK1InvalidoError
from maezo.tools.workers import rco_montador as mt
from tests.unit.platform import rco_k1_2_3_recorte as rec

PASTA = Path(__file__).resolve().parents[2] / "fixtures" / "rco" / "entradas-1.6.0-recorte"
PRAZO = "prazoComprovacaoUsoVencido"
MUDAM_NA_1_5_1 = {"cidSecundarioInformado", "regime"}


def _origem() -> dict[str, Any]:
    dados: dict[str, Any] = json.loads((PASTA / "ORIGEM.json").read_text(encoding="utf-8"))
    return dados


def _bruto(versao: str) -> dict[str, Any]:
    dados: dict[str, Any] = json.loads(
        (PASTA / f"entradas-{versao}-recorte.json").read_text(encoding="utf-8")
    )
    return dados


def _ler(dados: dict[str, Any]) -> ent.DicionarioEntradasK1:
    return ent.ler_dicionario_entradas(json.dumps(dados, ensure_ascii=False).encode("utf-8"))


def _por_nome(dados: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {e["nome_dmn"]: e for e in dados["entradas"]}


# --- procedencia --------------------------------------------------------------------------------------


def test_a_pasta_so_tem_os_recortes_declarados() -> None:
    origem = _origem()
    arquivos = {v["arquivo"] for v in origem["versoes"].values()}
    assert {p.name for p in PASTA.iterdir()} == arquivos | {"ORIGEM.json", "README.md"}
    for versao, info in origem["versoes"].items():
        raw = (PASTA / info["arquivo"]).read_bytes()
        assert "sha256:" + hashlib.sha256(raw).hexdigest() == info["sha256"], versao
        dados = json.loads(raw)
        assert (dados["versao"], dados["sha256_conteudo"]) == (versao, info["sha256_conteudo"])


def test_o_1_6_0_e_o_do_commit_que_o_pin_vai_fixar() -> None:
    # Referencia da trilha rco (T-RCO) para b8444f52: o hash do conteudo do dicionario 1.6.0 e o do arquivo.
    info = _origem()["versoes"]["1.6.0"]
    assert info["commit"] == "b8444f52e33eda556b5d2dab7837b046d5bd769e"
    assert (
        info["sha256_conteudo"] == "sha256:1aff5670aadc8037c7142b68ce669cc4fe4078c4cfd45c20e447da7491daafab"
    )
    assert info["entradas_json_sha256"] == (
        "sha256:3d67a1fcd1bcd445f09ce4eb546d1bdacdc5763d7e9c4ff851caedc0eca88775"
    )


# --- o leitor aceita as duas versoes ----------------------------------------------------------------


@pytest.mark.parametrize("versao", ["1.5.1", "1.6.0"])
def test_o_recorte_real_e_lido(versao: str) -> None:
    dic = _ler(_bruto(versao))
    assert dic.versao == versao
    assert set(dic.entradas) == set(rec.dicionario().entradas) | {PRAZO}


def test_versoes_so_foram_acrescentadas() -> None:
    assert frozenset({"1.1.0", "1.2.0", "1.4.0", "1.5.0", "1.5.1", "1.6.0"}) == ent.VERSOES_SUPORTADAS
    assert ent.CAMPOS_ENTRADA_POR_VERSAO["1.5.1"] == ent.CAMPOS_ENTRADA_POR_VERSAO["1.5.0"]
    assert ent.CAMPOS_ENTRADA_POR_VERSAO["1.6.0"] == ent.CAMPOS_ENTRADA_POR_VERSAO["1.5.0"]
    assert {"1.5.1", "1.6.0"} <= ent.VERSOES_COM_REGIMES & ent.VERSOES_COM_VALOR_DE_PARAMETRO
    assert frozenset({"1.6.0"}) == ent.VERSOES_COM_REFERENCIA_EM_TERMO


@pytest.mark.parametrize("versao", ["1.3.0", "1.5.2", "1.6.1", "1.7.0", "2.0.0"])
def test_versao_desconhecida_segue_recusada(versao: str) -> None:
    dados = _bruto("1.6.0")
    dados["versao"] = versao
    with pytest.raises(ManifestoK1InvalidoError, match=f"versao '{versao}'"):
        _ler(dados)


# --- diferencas por versao ----------------------------------------------------------------------------


def test_1_5_0_para_1_5_1_muda_so_cid_secundario_e_regime() -> None:
    antes = _por_nome(json.loads((rec.RECORTE / "entradas-recorte.json").read_text(encoding="utf-8")))
    depois = _por_nome(_bruto("1.5.1"))
    mudaram = {n for n in antes if antes[n] != depois[n]}
    assert mudaram == MUDAM_NA_1_5_1
    for nome in MUDAM_NA_1_5_1:
        campos = {k for k in antes[nome] if antes[nome][k] != depois[nome][k]}
        assert campos == {"de_onde", "formula"}, nome
    # os nomes e as origens dos termos sao os mesmos; em `regime` muda so a fonte citada de um termo
    for nome in MUDAM_NA_1_5_1:
        par = [[(t["nome"], t["origem"]) for t in d[nome]["formula"]["termos"]] for d in (antes, depois)]
        assert par[0] == par[1], nome
    assert (
        antes["cidSecundarioInformado"]["formula"]["termos"]
        == depois["cidSecundarioInformado"]["formula"]["termos"]
    )
    # 1.5.1: zero diagnosticos deixa de ser falso e passa a nulo (o que o montador ja fazia, DL-0184).
    assert "não traz nenhum diagnóstico" in depois["cidSecundarioInformado"]["de_onde"]
    assert "os regimes derivados divergirem, nulo" in depois["regime"]["formula"]["formula"]


def test_1_5_1_para_1_6_0_muda_so_o_prazo_de_comprovacao_da_opme_de_urgencia() -> None:
    antes, depois = _por_nome(_bruto("1.5.1")), _por_nome(_bruto("1.6.0"))
    assert {n for n in antes if antes[n] != depois[n]} == {PRAZO}
    termos_antes = {t["nome"]: t for t in antes[PRAZO]["formula"]["termos"]}
    termos_depois = {t["nome"]: t for t in depois[PRAZO]["formula"]["termos"]}
    # o prazo deixa de ser `parametro` com `valor_por_contrato` e vira o termo de cadastro do contrato
    assert termos_antes["prazoEnvioContaDias"]["origem"] == "parametro"
    assert "valor_por_contrato" in termos_antes["prazoEnvioContaDias"]
    assert set(termos_depois) == {
        "comprovacaoUsoAusente",
        "diasDesdeAtendimento",
        "prazoApresentacaoContratualDias",
        "autorizacaoExtemporanea",
    }


def test_1_6_0_prazo_de_comprovacao_nulo_sem_termo_nunca_glosa() -> None:
    entrada = _ler(_bruto("1.6.0")).entradas[PRAZO]
    formula = entrada.formula
    assert formula is not None
    assert (entrada.origem, entrada.tipo, entrada.dominio.valores) == (
        "derivado",
        "boolean",
        ("true", "false"),
    )
    assert formula.formula == (
        "nulo se autorizacaoExtemporanea = em_analise ou ausente; "
        "falso se autorizacaoExtemporanea = concedida; "
        "nos demais casos (nao_solicitada, nao_concedida), comprovacaoUsoAusente e diasDesdeAtendimento > "
        "prazoApresentacaoContratualDias (nulo se faltar um termo)"
    )
    assert tuple((t.nome, t.origem) for t in formula.termos) == (
        ("comprovacaoUsoAusente", "derivado"),
        ("diasDesdeAtendimento", "derivado"),
        ("prazoApresentacaoContratualDias", "cadastro"),
        ("autorizacaoExtemporanea", "cadastro"),
    )
    referencias = {t.nome: t.referencia for t in formula.termos if t.referencia}
    assert referencias == {
        "prazoApresentacaoContratualDias": ("docs/dicionario/regras-gerais.yaml#diasAlemDoPrazoContratual",),
        "autorizacaoExtemporanea": ("dmn/regras-gerais/geral_prazos_conta.dmn#autorizacaoExtemporanea",),
    }
    # citacao nao e valor: nenhum termo de cadastro vira parametro
    assert all(t.valor is None and t.valor_por_contrato is None for t in formula.termos)
    assert "nulo nunca glosa" in _por_nome(_bruto("1.6.0"))[PRAZO]["de_onde"]


# --- `referencia` em termo que nao e `parametro` -------------------------------------------------------


def _termo_de_cadastro(dados: dict[str, Any]) -> dict[str, Any]:
    termo: dict[str, Any] = next(
        t for t in _por_nome(dados)[PRAZO]["formula"]["termos"] if t["nome"] == "autorizacaoExtemporanea"
    )
    return termo


def test_referencia_em_termo_de_cadastro_antes_da_1_6_0_e_campo_desconhecido() -> None:
    dados = _bruto("1.5.1")
    termo = next(t for t in _por_nome(dados)["diasAlemDoPrazoContratual"]["formula"]["termos"])
    termo["referencia"] = "docs/x.yaml#y"
    with pytest.raises(ManifestoK1InvalidoError, match="desconhecidos"):
        _ler(dados)


@pytest.mark.parametrize(
    "referencia",
    ["sem-chave", "#chave", "arquivo#", "", [], ["a#b", "c"], 5, None, ["a#b", 5]],
)
def test_referencia_malformada_em_termo_de_cadastro_e_recusada(referencia: Any) -> None:
    dados = _bruto("1.6.0")
    _termo_de_cadastro(dados)["referencia"] = copy.deepcopy(referencia)
    with pytest.raises(ManifestoK1InvalidoError, match="referencia"):
        _ler(dados)


def test_lista_de_referencias_em_termo_de_cadastro_e_aceita() -> None:
    dados = _bruto("1.6.0")
    _termo_de_cadastro(dados)["referencia"] = ["a.yaml#b", "c.dmn#d"]
    termos = _ler(dados).entradas[PRAZO].formula
    assert termos is not None
    assert next(t for t in termos.termos if t.nome == "autorizacaoExtemporanea").referencia == (
        "a.yaml#b",
        "c.dmn#d",
    )


@pytest.mark.parametrize("campo", ["valor", "valor_por_contrato", "lacuna", "unidade", "extra"])
def test_termo_de_cadastro_so_ganha_referencia(campo: str) -> None:
    dados = _bruto("1.6.0")
    _termo_de_cadastro(dados)[campo] = "x"
    with pytest.raises(ManifestoK1InvalidoError, match="desconhecidos"):
        _ler(dados)


# --- o montador ainda nao aceita a 1.5.1+ (para o PR do pin) -----------------------------------------


@pytest.mark.parametrize("versao", ["1.5.1", "1.6.0"])
def test_montador_recusa_so_as_formulas_que_mudaram_na_1_5_1(versao: str) -> None:
    """Fixa o trabalho que falta ao PR do pin: o montador guarda o texto EXATO da formula (1.4.0/1.5.0) e
    recusa (`MontadorEntradasError`, nunca valor) a decisao cujas formulas mudaram. Quando o montador for
    revisto contra o texto da 1.5.1, este teste muda junto."""
    dic = _ler(_bruto(versao))
    problemas: set[str] = set()
    for decisao in rec.manifesto().decisoes.values():
        if decisao.dominio in mt.MAPEAMENTOS:
            problemas.update(mt.conferir_mapeamento(dic, decisao.dominio, decisao.entradas))
    assert problemas == {
        f"{nome}: formula do dicionario diverge da implementada; reveja o montador" for nome in MUDAM_NA_1_5_1
    }
