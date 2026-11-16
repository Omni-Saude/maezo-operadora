"""Leitor do dicionario de entradas do K1 (`maezo.platform.rco_entradas`, MZO-2b; contrato-k1.md §1/§5).

Contra o RECORTE REAL `tests/fixtures/rco/k1-1.1.0-recorte/entradas.json` (as entradas dos dominios
`regras-gerais` e `diarias-taxas`, copiadas sem alteracao do `entradas.json` gerado do rco no commit
fe1dc897, dicionario 1.1.0; procedencia em `decisoes.json`) e cada recusa fail-closed.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from maezo.platform import rco_entradas as ent
from maezo.platform.rco_manifesto import ManifestoK1InvalidoError

RECORTE = Path(__file__).resolve().parents[2] / "fixtures" / "rco" / "k1-1.1.0-recorte"


def _dados() -> dict[str, Any]:
    dados: dict[str, Any] = json.loads((RECORTE / "entradas.json").read_text(encoding="utf-8"))
    return dados


def _ler(dados: dict[str, Any]) -> ent.DicionarioEntradasK1:
    return ent.ler_dicionario_entradas(json.dumps(dados, ensure_ascii=False).encode("utf-8"))


def _indice(dados: dict[str, Any], nome: str) -> int:
    return next(i for i, e in enumerate(dados["entradas"]) if e["nome_dmn"] == nome)


def test_recorte_real_le_e_expoe_o_que_o_montador_usa() -> None:
    dic = ent.ler_dicionario_entradas((RECORTE / "entradas.json").read_bytes())
    assert dic.versao == "1.1.0"
    assert dic.sha256_conteudo.startswith("sha256:")
    data = dic.entradas["dataAtendimento"]
    assert (data.origem, data.tipo, data.variavel_maezo) == ("tiss", "string", "data_atendimento")
    assert data.dominio.tipo == "padrao" and data.dominio.verificavel
    carater = dic.entradas["caraterAtendimento"]
    assert carater.variavel_maezo == "carater_atendimento"
    assert carater.dominio.valores == ("1", "2")
    idade = dic.entradas["idadeBeneficiario"]
    assert idade.variavel_maezo == "idade_anos"  # nome_maezo declarado pelo rco
    assert (idade.dominio.minimo, idade.dominio.maximo) == (0, 130)
    prazo = dic.entradas["diasAlemDoPrazoContratual"]
    assert prazo.origem == "derivado" and prazo.formula is not None
    assert [(t.nome, t.origem) for t in prazo.formula.termos] == [
        ("diasAteApresentacao", "derivado"),
        ("prazoApresentacaoContratualDias", "cadastro"),
    ]
    assert dic.entradas["familiaDiariaTaxa"].dominio.verificavel is False  # lista truncada (73 valores)
    assert dic.entradas["grupoCarencia"].classe_lago == "derivavel"
    assert dic.entradas["autorizacaoExtemporanea"].classe_lago is None  # lacuna do lago


@pytest.mark.parametrize(
    ("texto", "tipo", "verificavel"),
    [
        ("{true, false}", "lista", True),
        ("{a, b, c, d, e, f, … (73 valores)}", "lista", False),
        ("faixa 0..300", "faixa", True),
        ("faixa -36500..36500", "faixa", True),
        ("faixa 0..None", "faixa", True),
        ("faixa 0.5..1.5", "faixa", True),
        ("padrão ^[0-9]{4}$", "padrao", True),
        ("tabela TUSS 22 (procedimentos e eventos em saúde)", "tabela", False),
    ],
)
def test_resumo_de_dominio(texto: str, tipo: str, verificavel: bool) -> None:
    dominio = ent.ler_dominio(texto)
    assert dominio is not None
    assert (dominio.tipo, dominio.verificavel) == (tipo, verificavel)


@pytest.mark.parametrize("texto", ["LACUNA", "faixa 3..1", "{}", "padrão [", "faixa a..b"])
def test_resumo_de_dominio_desconhecido(texto: str) -> None:
    assert ent.ler_dominio(texto) is None


def _mutar(caminho: list[Any], valor: Any) -> Any:
    def aplicar(dados: dict[str, Any]) -> None:
        alvo: Any = dados
        for chave in caminho[:-1]:
            alvo = (
                alvo[_indice(dados, chave)]
                if isinstance(chave, str) and isinstance(alvo, list)
                else alvo[chave]
            )
        if valor is _REMOVER:
            del alvo[caminho[-1]]
        else:
            alvo[caminho[-1]] = valor

    return aplicar


_REMOVER = object()


@pytest.mark.parametrize(
    ("mutar", "trecho"),
    [
        (_mutar(["versao"], "1.2.0"), "versao '1.2.0'"),
        (_mutar(["versao"], "1.1"), "versao"),
        (_mutar(["sha256_conteudo"], "a" * 64), "sha256_conteudo"),
        (_mutar(["novo"], 1), "desconhecidos ['novo']"),
        (_mutar(["entradas", "grupoCarencia", "fallback_nulo"], "negar"), "fallback_nulo"),
        (_mutar(["entradas", "grupoCarencia", "origem"], "lago"), "origem"),
        (_mutar(["entradas", "grupoCarencia", "tipo"], "date"), "tipo"),
        (_mutar(["entradas", "grupoCarencia", "dominio"], "LACUNA"), "dominio"),
        (_mutar(["entradas", "grupoCarencia", "extra"], 1), "desconhecidos ['extra']"),
        (_mutar(["entradas", "grupoCarencia", "tabelas"], _REMOVER), "faltam ['tabelas']"),
        (_mutar(["entradas", "grupoCarencia", "nome_dmn"], "grupo carencia"), "nome_dmn"),
        (
            _mutar(
                ["entradas", "grupoCarencia", "formula"],
                {"formula": "x", "termos": [{"nome": "a", "origem": "tiss", "fonte": "f"}]},
            ),
            "so entrada de origem derivado",
        ),
        (
            _mutar(["entradas", "diasAlemDoPrazoContratual", "formula", "termos", 1, "origem"], "lago"),
            "termos[1].origem",
        ),
        (_mutar(["entradas", "diasAlemDoPrazoContratual", "formula", "termos"], []), "lista nao vazia"),
        (_mutar(["entradas", "grupoCarencia", "derivacao_lago", "classe_lago"], ""), "classe_lago"),
        (_mutar(["entradas"], []), "lista nao vazia"),
    ],
)
def test_forma_invalida_reprova(mutar: Any, trecho: str) -> None:
    dados = copy.deepcopy(_dados())
    mutar(dados)
    with pytest.raises(ManifestoK1InvalidoError) as excinfo:
        _ler(dados)
    assert trecho in str(excinfo.value)


def test_nome_repetido_reprova() -> None:
    dados = _dados()
    dados["entradas"].append(copy.deepcopy(dados["entradas"][0]))
    with pytest.raises(ManifestoK1InvalidoError, match="repetido"):
        _ler(dados)


@pytest.mark.parametrize("raw", [b'{"versao": "1.1.0", "versao": "1.1.0"}', b'{"x": NaN}', b"\xff"])
def test_json_estrito(raw: bytes) -> None:
    with pytest.raises(ManifestoK1InvalidoError, match="JSON ilegivel"):
        ent.ler_dicionario_entradas(raw)
