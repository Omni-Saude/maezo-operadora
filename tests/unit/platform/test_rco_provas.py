"""Logica de `rco_provas`: carga de casos e comparacao com o `esperado`, com avaliador FALSO.

O motor real roda em `tests/integration/dmn/test_rco_provas_engine.py`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest

from maezo.platform import rco_manifesto as k1
from maezo.platform import rco_provas as rp

PROVA = "p"
BASE = f"provas/{PROVA}"
CASOS = f"{BASE}/validacao/casos"


DMN_REL = "dmn/p.dmn"
DMN = (
    '<definitions xmlns="https://www.omg.org/spec/DMN/20191111/MODEL/" id="d" name="d" namespace="x">'
    '<decision id="{id}" name="n"/></definitions>'
)


def _prova(tmp_path: Path, casos: object, nome: str = "c1.json", *, decisao_dmn: str = "c1") -> k1.ProvaK1:
    """Grava DMN + casos sob `tmp_path/provas/p/...` e devolve a prova com os sha256 reais."""
    dmn = DMN.format(id=decisao_dmn).encode()
    raw = json.dumps(casos).encode()
    for rel, conteudo in ((f"{BASE}/{DMN_REL}", dmn), (f"{CASOS}/{nome}", raw)):
        alvo = tmp_path / rel
        alvo.parent.mkdir(parents=True, exist_ok=True)
        alvo.write_bytes(conteudo)
    return k1.ProvaK1(
        PROVA,
        "d",
        (
            k1.ArquivoProvaK1(f"{BASE}/{DMN_REL}", k1.sha256_prefixado(dmn)),
            k1.ArquivoProvaK1(f"{CASOS}/{nome}", k1.sha256_prefixado(raw)),
        ),
        CASOS,
    )


def _arquivo(casos: list[dict[str, Any]]) -> dict[str, Any]:
    return {"dmn": DMN_REL, "decisao": "c1", "casos": casos}


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
        _arquivo([{"id": "a", "entradas": {}, "esperado": {"resultado": ["casa"]}}]),
        _arquivo([{"id": "a", "entradas": {}, "esperado": {"resultado": {"x": 1}}}]),
        _arquivo([_caso("a", "casa"), _caso("a", "casa")]),
        {**_arquivo([_caso("a", "casa")]), "decisao": "ausente"},
        {**_arquivo([_caso("a", "casa")]), "dmn": "dmn/outro.dmn"},
    ],
)
def test_arquivo_de_casos_malformado_ou_vazio_e_recusado(tmp_path: Path, conteudo: object) -> None:
    with pytest.raises(k1.ManifestoK1InvalidoError):
        rp.carregar_casos(_prova(tmp_path, conteudo), k1_dir=tmp_path)


def test_sha256_adulterado_e_recusado_antes_de_usar(tmp_path: Path) -> None:
    prova = _prova(tmp_path, _arquivo([_caso("a", "casa")]))
    (tmp_path / CASOS / "c1.json").write_bytes(b"{}")
    with pytest.raises(k1.ManifestoK1InvalidoError, match="sha256 diverge"):
        rp.carregar_casos(prova, k1_dir=tmp_path)


def test_link_simbolico_e_recusado(tmp_path: Path) -> None:
    prova = _prova(tmp_path, _arquivo([_caso("a", "casa")]))
    real = tmp_path / "real.json"
    real.write_bytes((tmp_path / CASOS / "c1.json").read_bytes())
    (tmp_path / CASOS / "c1.json").unlink()
    (tmp_path / CASOS / "c1.json").symlink_to(real)
    with pytest.raises(k1.ManifestoK1InvalidoError, match="link simbolico"):
        rp.carregar_casos(prova, k1_dir=tmp_path)


def test_decisao_com_prefixo_reservado_e_recusada(tmp_path: Path) -> None:
    prova = _prova(tmp_path, {**_arquivo([_caso("a", "casa")]), "decisao": "rco_x"}, decisao_dmn="rco_x")
    with pytest.raises(k1.ManifestoK1InvalidoError, match="prefixo reservado"):
        rp.carregar_casos(prova, k1_dir=tmp_path)


def _cliente(chamadas: list[httpx.Request], *, decisoes: dict[str, str]) -> httpx.AsyncClient:
    def tratar(req: httpx.Request) -> httpx.Response:
        chamadas.append(req)
        if req.url.path == "/deployment/create":
            return httpx.Response(
                200,
                json={"deployedDecisionDefinitions": {i: {"id": i, "key": k} for k, i in decisoes.items()}},
            )
        return httpx.Response(200, json=[{"resultado": {"value": "casa", "type": "String"}}])

    return httpx.AsyncClient(base_url="http://engine", transport=httpx.MockTransport(tratar))


async def test_implantar_recusa_decisao_do_repo(tmp_path: Path) -> None:
    prova = _prova(tmp_path, _arquivo([_caso("a", "casa")]))
    chamadas: list[httpx.Request] = []
    async with _cliente(chamadas, decisoes={"c1": "c1:1:x"}) as client:
        with pytest.raises(k1.ManifestoK1InvalidoError, match="colide com o repo"):
            await rp.implantar_prova(client, prova, k1_dir=tmp_path, chaves_do_repo=frozenset({"c1"}))
    assert chamadas == []  # nada foi implantado


async def test_avaliacao_vai_pela_definicao_implantada_e_nulo_como_na_producao(tmp_path: Path) -> None:
    prova = _prova(tmp_path, _arquivo([_caso("n", "casa", None)]))
    chamadas: list[httpx.Request] = []
    async with _cliente(chamadas, decisoes={"c1": "c1:7:abc"}) as client:
        definicoes = await rp.implantar_prova(client, prova, k1_dir=tmp_path, chaves_do_repo=frozenset())
        assert definicoes == {"c1": "c1:7:abc"}
        assert (
            await rp.executar_prova(
                prova, k1_dir=tmp_path, avaliador=rp.AvaliadorCibSeven(client, definicoes)
            )
            == []
        )
    avaliar = chamadas[-1]
    assert avaliar.url.path == "/decision-definition/c1:7:abc/evaluate"  # nao `key/<k>/evaluate`
    assert json.loads(avaliar.content) == {"variables": {"flag": {"value": None, "type": "String"}}}


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
