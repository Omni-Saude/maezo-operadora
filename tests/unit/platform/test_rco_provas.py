"""Logica de `rco_provas`: carga de casos e comparacao com o `esperado`, com avaliador FALSO.

O motor real roda em `tests/integration/dmn/test_rco_provas_engine.py`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from maezo.platform import rco_manifesto as k1
from maezo.platform import rco_provas as rp

PROVA = "p"
BASE = f"provas/{PROVA}"
CASOS = f"{BASE}/validacao/casos"


def _prova(tmp_path: Path, casos: object, nome: str = "c1.json") -> k1.ProvaK1:
    arq = tmp_path / CASOS / nome
    arq.parent.mkdir(parents=True, exist_ok=True)
    arq.write_text(json.dumps(casos), encoding="utf-8")
    return k1.ProvaK1(PROVA, "d", (k1.ArquivoProvaK1(f"{CASOS}/{nome}", "sha256:" + "0" * 64),), CASOS)


def _arquivo(casos: list[dict[str, Any]]) -> dict[str, Any]:
    return {"dmn": "dmn/p.dmn", "decisao": "c1", "casos": casos}


def _caso(i: str, esperado: str, flag: object = True) -> dict[str, Any]:
    return {"id": i, "entradas": {"flag": flag}, "esperado": {"resultado": esperado}}


class FakeAvaliador:
    """Responde por `flag`: True -> casa, qualquer outra coisa -> nao_casa. `resposta` forca a saida."""

    def __init__(self, resposta: list[dict[str, Any]] | Exception | None = None) -> None:
        self.resposta = resposta
        self.chamadas: list[tuple[str, Mapping[str, Any]]] = []

    async def avaliar(self, decisao: str, entradas: Mapping[str, Any]) -> list[dict[str, Any]]:
        self.chamadas.append((decisao, entradas))
        if isinstance(self.resposta, Exception):
            raise self.resposta
        if self.resposta is not None:
            return self.resposta
        return [{"resultado": "casa" if entradas["flag"] is True else "nao_casa"}]


async def test_tudo_igual_ao_esperado_nao_diverge(tmp_path: Path) -> None:
    prova = _prova(tmp_path, _arquivo([_caso("a", "casa"), _caso("b", "nao_casa", False)]))
    fake = FakeAvaliador()
    assert await rp.executar_provas([prova], k1_dir=tmp_path, avaliador=fake) == []
    assert [c[0] for c in fake.chamadas] == ["c1", "c1"]


async def test_esperado_invertido_diverge_e_nomeia_o_caso(tmp_path: Path) -> None:
    prova = _prova(tmp_path, _arquivo([_caso("a", "nao_casa")]))
    (div,) = await rp.executar_prova(prova, k1_dir=tmp_path, avaliador=FakeAvaliador())
    assert (div.prova, div.esperado, div.obtido) == (PROVA, "nao_casa", "casa")
    assert div.caso.endswith("c1.json#a")
    assert "esperado 'nao_casa'" in div.render()


@pytest.mark.parametrize(
    ("resposta", "obtido"),
    [
        ([], "0 linhas"),
        ([{"resultado": "casa"}, {"resultado": "casa"}], "2 linhas"),
        ([{"outra": 1}], "resultado=None"),
        ([{"resultado": "talvez"}], "resultado='talvez'"),
        (RuntimeError("motor caiu"), "erro: RuntimeError"),
    ],
)
async def test_forma_inesperada_ou_erro_do_motor_diverge(
    tmp_path: Path, resposta: list[dict[str, Any]] | Exception, obtido: str
) -> None:
    prova = _prova(tmp_path, _arquivo([_caso("a", "casa")]))
    (div,) = await rp.executar_prova(prova, k1_dir=tmp_path, avaliador=FakeAvaliador(resposta))
    assert div.obtido == obtido


@pytest.mark.parametrize(
    "conteudo",
    [
        {"dmn": "x", "decisao": "c1"},
        _arquivo([{"id": "a", "entradas": {}, "esperado": {"resultado": "talvez"}}]),
        _arquivo([{"id": "a", "esperado": {"resultado": "casa"}}]),
        _arquivo([]),
    ],
)
def test_arquivo_de_casos_malformado_ou_vazio_e_recusado(tmp_path: Path, conteudo: object) -> None:
    with pytest.raises(k1.ManifestoK1InvalidoError):
        rp.carregar_casos(_prova(tmp_path, conteudo), k1_dir=tmp_path)


def test_so_carrega_o_que_o_manifesto_lista(tmp_path: Path) -> None:
    prova = _prova(tmp_path, _arquivo([_caso("a", "casa")]))
    (tmp_path / CASOS / "extra.json").write_text("{}", encoding="utf-8")  # fora do manifesto: ignorado
    assert [c.id_caso for c in rp.carregar_casos(prova, k1_dir=tmp_path)] == ["a"]


@pytest.mark.parametrize(
    ("linhas", "esperado"),
    [([{"resultado": "casa"}], "casa"), ([{"resultado": "nao_casa"}], "nao_casa")],
)
def test_resultado_da_avaliacao(linhas: list[dict[str, Any]], esperado: str) -> None:
    assert rp.resultado_da_avaliacao(linhas) == esperado
