"""`auth_sla` — o catch-all r4 (carater nulo, vazio ou desconhecido) recebe o MENOR prazo da tabela.

Decisao do coordenador (17/11/2026, em nome do dono): dado ausente nunca cai no tratamento mais
lento ("urgencia garante acesso"; mesma logica da regra D4 do rco). Antes, r4 devolvia o prazo do
eletivo padrao (P5D / P3D / 5 dias uteis) com a descricao "tratado como prazo curto"; agora devolve
os mesmos prazos de r1 (urgencia).

Leitura SEM motor: um leitor FIRST minimo sobre o XML, que so aceita as duas formas de teste
unario que esta tabela usa (`-` e lista de literais string) e falha fechado em qualquer outra. A
mesma matriz roda no MOTOR REAL em `tests/integration/dmn/test_auth_sla_carater_engine.py`.

Fixtures sinteticas; nenhum dado real.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final
from xml.etree.ElementTree import Element

import pytest

from maezo.platform.xml_safe import parse_xml_without_dtd

_ROOT: Final = Path(__file__).resolve().parents[3]
_DMN: Final = _ROOT / "spec/processes/dmn/auth_sla.dmn"
_NS: Final = {"d": "https://www.omg.org/spec/DMN/20191111/MODEL/"}
_LISTA_LITERAIS: Final = re.compile(r'^"[^"]*"(\s*,\s*"[^"]*")*$')

#: Colunas de PRAZO: as que o timer BPMN e o worker de dias uteis leem.
PRAZOS: Final = ("sla_analise", "sla_alerta", "prazo_resposta_dias_uteis")

CATEGORIAS: Final = ("consulta", "exame", "terapia", "alta_complexidade", "opme", "internacao", "", None)
CARATERES_NAO_CLASSIFICADOS: Final = (None, "", "emergencia", "URGENCIA", "eletiva")


def _texto(el: Element | None) -> str:
    return "" if el is None else (el.findtext("d:text", "", _NS) or "").strip()


def _tabela() -> Element:
    root = parse_xml_without_dtd(_DMN.read_bytes())
    (decisao,) = root.findall("d:decision", _NS)
    assert decisao.get("id") == "auth_sla"
    tabela = decisao.find("d:decisionTable", _NS)
    assert tabela is not None
    return tabela


def _casa(teste: str, valor: str | None) -> bool:
    if teste == "-":
        return True
    assert _LISTA_LITERAIS.match(teste), f"forma de teste unario fora do leitor: {teste!r}"
    if valor is None:
        return False  # FEEL: literal string nunca casa entrada nula
    return valor in re.findall(r'"([^"]*)"', teste)


def _saida(texto: str) -> str | int | None:
    if texto == "null":
        return None
    if texto.startswith('"') and texto.endswith('"'):
        return texto[1:-1]
    return int(texto)


def _saidas_da_regra(regra_id: str) -> dict[str, str | int | None]:
    tabela = _tabela()
    nomes = [o.get("name") or "" for o in tabela.findall("d:output", _NS)]
    (regra,) = [r for r in tabela.findall("d:rule", _NS) if r.get("id") == regra_id]
    return {n: _saida(_texto(o)) for n, o in zip(nomes, regra.findall("d:outputEntry", _NS), strict=True)}


def _avaliar(carater: str | None, categoria: str | None) -> tuple[str, dict[str, str | int | None]]:
    tabela = _tabela()
    assert tabela.get("hitPolicy") == "FIRST"
    entradas = [_texto(i.find("d:inputExpression", _NS)) for i in tabela.findall("d:input", _NS)]
    assert entradas == ["carater_atendimento", "categoria_procedimento"]
    valores = (carater, categoria)
    for regra in tabela.findall("d:rule", _NS):
        testes = [_texto(e) for e in regra.findall("d:inputEntry", _NS)]
        if all(_casa(t, v) for t, v in zip(testes, valores, strict=True)):
            rid = regra.get("id") or ""
            return rid, _saidas_da_regra(rid)
    raise AssertionError(f"nenhuma regra casou: {valores!r}")


def _prazos(saidas: dict[str, str | int | None]) -> dict[str, str | int | None]:
    return {k: saidas[k] for k in PRAZOS}


def test_r1_urgencia_e_o_menor_prazo_da_tabela() -> None:
    r1 = _saidas_da_regra("r1")
    assert _prazos(r1) == {"sla_analise": "PT2H", "sla_alerta": "PT1H", "prazo_resposta_dias_uteis": None}
    for outra in ("r2", "r3"):
        assert _prazos(_saidas_da_regra(outra))["prazo_resposta_dias_uteis"] is not None


def test_r4_catch_all_e_o_ultimo_e_copia_os_prazos_de_r1() -> None:
    regras = _tabela().findall("d:rule", _NS)
    assert regras[-1].get("id") == "r4"
    assert [_texto(e) for e in regras[-1].findall("d:inputEntry", _NS)] == ["-", "-"]
    assert _prazos(_saidas_da_regra("r4")) == _prazos(_saidas_da_regra("r1"))
    fonte = _saidas_da_regra("r4")["fonte_regulatoria"]
    assert isinstance(fonte, str) and fonte.startswith("Fail-safe:") and "DRAFT/verify" in fonte


@pytest.mark.parametrize("categoria", CATEGORIAS)
@pytest.mark.parametrize("carater", CARATERES_NAO_CLASSIFICADOS)
def test_carater_nao_classificado_recebe_os_prazos_da_urgencia(
    carater: str | None, categoria: str | None
) -> None:
    regra, saidas = _avaliar(carater, categoria)
    assert regra == "r4"
    assert _prazos(saidas) == _prazos(_saidas_da_regra("r1"))


@pytest.mark.parametrize("categoria", CATEGORIAS)
def test_urgencia_inalterada(categoria: str | None) -> None:
    regra, saidas = _avaliar("urgencia", categoria)
    assert regra == "r1"
    assert _prazos(saidas) == {"sla_analise": "PT2H", "sla_alerta": "PT1H", "prazo_resposta_dias_uteis": None}


@pytest.mark.parametrize(
    ("categoria", "regra_esperada", "prazos"),
    [
        ("alta_complexidade", "r2", ("P10D", "P7D", 10)),
        ("opme", "r2", ("P10D", "P7D", 10)),
        ("internacao", "r2", ("P10D", "P7D", 10)),
        ("consulta", "r3", ("P5D", "P3D", 5)),
        ("exame", "r3", ("P5D", "P3D", 5)),
        ("terapia", "r3", ("P5D", "P3D", 5)),
        ("", "r3", ("P5D", "P3D", 5)),
        (None, "r3", ("P5D", "P3D", 5)),
    ],
)
def test_eletivo_inalterado(categoria: str | None, regra_esperada: str, prazos: tuple[str, str, int]) -> None:
    regra, saidas = _avaliar("eletivo", categoria)
    assert regra == regra_esperada
    assert _prazos(saidas) == dict(zip(PRAZOS, prazos, strict=True))
