"""Pacote PAGTO PR-E — a forma da DMN `pagto_duplicidade` (sem motor; a semantica esta' provada no
motor real em `tests/integration/dmn/test_pagto_duplicidade_engine.py`).

Cercas: FIRST com catch-all INDETERMINADA por ultimo; nenhuma saida fora do vocabulario de FATO
(nada que libere/cancele/bloqueie/acuse); NENHUMA so' com os tres niveis avaliados; a janela W do
nivel 3 e' a MESMA nas duas linhas que a usam; entradas = exatamente o que o detector manda; e a
tabela nao e' lida por BPMN nenhum (consumidor unico = costura dmn= do detector).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final
from xml.etree.ElementTree import Element

from maezo.platform.xml_safe import parse_xml_without_dtd
from maezo.tools.workers import pagto_duplicidade as dup

_ROOT: Final = Path(__file__).resolve().parents[3]
_DMN: Final = _ROOT / "spec/processes/dmn/pagto_duplicidade.dmn"
_NS: Final = {"d": "https://www.omg.org/spec/DMN/20191111/MODEL/"}


def _texto(el: Element | None) -> str:
    return "" if el is None else (el.findtext("d:text", "", _NS) or "").strip()


def _tabela() -> Element:
    root = parse_xml_without_dtd(_DMN.read_bytes())
    (decisao,) = root.findall("d:decision", _NS)
    assert decisao.get("id") == dup.DMN_DUPLICIDADE
    dt = decisao.find("d:decisionTable", _NS)
    assert dt is not None
    return dt


def _regras() -> list[tuple[str, tuple[str, ...], tuple[str, ...]]]:
    return [
        (
            r.get("id", ""),
            tuple(_texto(e) for e in r.findall("d:inputEntry", _NS)),
            tuple(_texto(o) for o in r.findall("d:outputEntry", _NS)),
        )
        for r in _tabela().findall("d:rule", _NS)
    ]


def test_first_com_catch_all_indeterminada_por_ultimo() -> None:
    assert _tabela().get("hitPolicy") == "FIRST"
    regra, entradas, saidas = _regras()[-1]
    assert regra == "r_catchall"
    assert set(entradas) == {"-"}
    assert saidas == ('"INDETERMINADA"', '"NIVEL_NAO_AVALIADO"')


def test_entradas_sao_exatamente_o_que_o_detector_manda() -> None:
    entradas = [
        (i.find("d:inputExpression", _NS).get("typeRef"), _texto(i.find("d:inputExpression", _NS)))  # type: ignore[union-attr]
        for i in _tabela().findall("d:input", _NS)
    ]
    assert entradas == [
        ("string", "nivel1_situacao"),
        ("string", "nivel2_situacao"),
        ("boolean", "nivel3_avaliado"),
        ("integer", "nivel3_similar_idade_dias"),
    ]
    fonte = Path(dup.__file__).read_text(encoding="utf-8")
    for _tipo, nome in entradas:
        assert f'"{nome}":' in fonte


def test_saidas_so_no_vocabulario_de_fato() -> None:
    saidas = [o.get("name") for o in _tabela().findall("d:output", _NS)]
    assert saidas == ["duplicidade_status", "duplicidade_motivo_codigo"]
    for _regra, _entradas, (status, motivo) in _regras():
        assert status.strip('"') in dup.DUPLICIDADE_STATUS
        assert re.fullmatch(r'"[A-Z0-9_]+"', motivo)
        assert not re.search(r"LIBER|CANCEL|BLOQ|FRAUDE|NEGA", status)


def test_valores_de_nivel_usados_estao_no_vocabulario_do_detector() -> None:
    for _regra, entradas, _saidas in _regras():
        if entradas[0] != "-":
            assert entradas[0].strip('"') in dup.NIVEL1_SITUACOES
        if entradas[1] != "-":
            assert entradas[1].strip('"') in dup.NIVEL2_SITUACOES


def test_nenhuma_so_com_os_tres_niveis_avaliados_e_sem_indicio() -> None:
    nenhumas = [entradas for _r, entradas, saidas in _regras() if saidas[0] == '"NENHUMA"']
    assert nenhumas
    for nivel1, nivel2, avaliado, _idade in nenhumas:
        assert (nivel1, nivel2, avaliado) == ('"SEM_OUTRA_ORDEM"', '"NAO_EXCEDE"', "true")


def test_janela_w_e_a_mesma_nas_duas_linhas_e_esta_declarada_draft() -> None:
    idades = {r: entradas[3] for r, entradas, _s in _regras() if entradas[3] not in ("-", "null")}
    assert idades == {"r_dup_similar_na_janela": "[0..180]", "r_dup_nenhuma_similar_fora_da_janela": "> 180"}
    texto = _DMN.read_text(encoding="utf-8")
    assert "status: DRAFT" in texto and "W=180" in texto


def test_confirmada_vem_antes_de_suspeita_que_vem_antes_de_nenhuma() -> None:
    ordem = [saidas[0].strip('"') for _r, _e, saidas in _regras()]
    ultima_confirmada = max(i for i, s in enumerate(ordem) if s == "CONFIRMADA")
    primeira_suspeita = min(i for i, s in enumerate(ordem) if s == "SUSPEITA")
    ultima_suspeita = max(i for i, s in enumerate(ordem) if s == "SUSPEITA")
    primeira_nenhuma = min(i for i, s in enumerate(ordem) if s == "NENHUMA")
    assert ultima_confirmada < primeira_suspeita and ultima_suspeita < primeira_nenhuma


def test_nenhum_bpmn_le_a_tabela() -> None:
    for bpmn in (_ROOT / "spec/processes/bpmn").glob("*.bpmn"):
        assert "pagto_duplicidade" not in bpmn.read_text(encoding="utf-8"), bpmn.name


def test_registrada_como_pendente_e_na_fila_de_revisao() -> None:
    aprovacoes = (_ROOT / "spec/aprovacoes-de-regras.yaml").read_text(encoding="utf-8")
    assert "artefato: spec/processes/dmn/pagto_duplicidade.dmn" in aprovacoes.split("pendentes:")[1]
    assert "`spec/processes/dmn/pagto_duplicidade.dmn`" in (_ROOT / "docs/review-queue.md").read_text(
        encoding="utf-8"
    )
