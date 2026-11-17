"""E-SF-13 / GTM-E-BPMN-00 — paridade `.bpmn` <-> `docs/processes/contracts/MESSAGE-CATALOG.md`.

Sem GT-ID (higiene de processo, por desenho). Relê os diagramas a cada build: toda
`<bpmn:message name>` e todo `errorCode` estao no catalogo com dono, correlacao e versao, e
vice-versa; convencao `msg.<dom>.<evento>`; zero sinais/escalacoes salvo decisao registrada.
Molde: `tests/unit/platform/test_validation_phi_completeness.py` (re-derivar o universo dos
artefatos vivos, nunca de uma lista copiada).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

_RAIZ = Path(__file__).resolve().parents[3]
_BPMN = sorted((_RAIZ / "spec" / "processes" / "bpmn").glob("*.bpmn"))
_CATALOGO = _RAIZ / "docs" / "processes" / "contracts" / "MESSAGE-CATALOG.md"
_NS = {"bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL"}
_MSG = re.compile(r"^msg\.[a-z][a-z0-9]*\.[a-z][a-z0-9_]*$")
_ERR = re.compile(r"^ERR_[A-Z][A-Z0-9_]*$")


def _do_bpmn() -> tuple[set[tuple[str, str, str]], set[tuple[str, str, str]], set[tuple[str, str]]]:
    mensagens, erros, sinais = set(), set(), set()
    for f in _BPMN:
        raiz = ET.parse(f).getroot()
        processo = raiz.find("bpmn:process", _NS)
        assert processo is not None, f.name
        pid = str(processo.get("id"))
        for m in raiz.findall("bpmn:message", _NS):
            mensagens.add((str(m.get("name")), pid, str(m.get("id"))))
        for e in raiz.findall("bpmn:error", _NS):
            erros.add((str(e.get("errorCode")), pid, str(e.get("id"))))
        for tag in ("signal", "escalation"):
            for s in raiz.findall(f"bpmn:{tag}", _NS):
                sinais.add((str(s.get("name") or s.get("id")), pid))
    return mensagens, erros, sinais


def _secao(texto: str, titulo: str) -> list[list[str]]:
    corpo = texto.split(f"## {titulo}", 1)[1].split("\n## ", 1)[0]
    linhas = []
    for linha in corpo.splitlines():
        if linha.startswith("| `"):
            linhas.append([c.strip() for c in linha.strip().strip("|").split("|")])
    return linhas


def _sem_crase(c: str) -> str:
    return c.strip().strip("`")


def _do_catalogo() -> tuple[list[list[str]], list[list[str]], list[list[str]]]:
    texto = _CATALOGO.read_text(encoding="utf-8")
    return (
        _secao(texto, "Mensagens (`<bpmn:message>`)"),
        _secao(texto, "Erros (`<bpmn:error errorCode>`)"),
        _secao(texto, "Sinais e escalações"),
    )


def test_ha_diagramas_e_linhas() -> None:
    assert len(_BPMN) >= 17
    msgs, errs, _ = _do_catalogo()
    assert msgs and errs


def test_toda_mensagem_do_bpmn_esta_no_catalogo_e_vice_versa() -> None:
    do_bpmn, _, _ = _do_bpmn()
    msgs, _, _ = _do_catalogo()
    do_catalogo = {(_sem_crase(r[0]), r[1], _sem_crase(r[2])) for r in msgs}
    assert do_bpmn - do_catalogo == set(), "mensagem no .bpmn sem linha no catalogo"
    assert do_catalogo - do_bpmn == set(), "linha do catalogo sem mensagem no .bpmn"
    assert len(msgs) == len(do_catalogo), "linha de mensagem duplicada"


def test_todo_error_code_do_bpmn_esta_no_catalogo_e_vice_versa() -> None:
    _, do_bpmn, _ = _do_bpmn()
    _, errs, _ = _do_catalogo()
    do_catalogo = {(_sem_crase(r[0]), r[1], _sem_crase(r[2])) for r in errs}
    assert do_bpmn - do_catalogo == set(), "errorCode no .bpmn sem linha no catalogo"
    assert do_catalogo - do_bpmn == set(), "linha do catalogo sem errorCode no .bpmn"
    assert len(errs) == len(do_catalogo), "linha de erro duplicada"


@pytest.mark.parametrize("secao", [0, 1])
def test_toda_linha_tem_dono_correlacao_e_versao(secao: int) -> None:
    linhas = _do_catalogo()[secao]
    for r in linhas:
        dono, versao = r[-2], r[-1]
        assert r[1] in dono and "(" in dono, r[0]
        assert re.fullmatch(r"[1-9][0-9]*", versao), r[0]
        if secao == 0:
            correlacao = r[4]
            assert correlacao, r[0]
            assert (
                "business key" in correlacao
                or "correlaciona" in correlacao
                or "GAP" in correlacao
                or ("protocolo_ans" in correlacao)
            ), r[0]


def test_convencao_de_nomes() -> None:
    mensagens, erros, _ = _do_bpmn()
    assert [m for m, _, _ in mensagens if not _MSG.match(m)] == []
    assert [e for e, _, _ in erros if not _ERR.match(e)] == []
    for nome, processo, _ in mensagens:
        dominio = nome.split(".")[1]
        assert dominio in processo.lower().replace("-", ""), (nome, processo)  # <dom> e' do proprio processo


def test_zero_sinais_salvo_decisao_registrada() -> None:
    _, _, sinais = _do_bpmn()
    _, _, registradas = _do_catalogo()
    decididos = {(_sem_crase(r[0]), r[1]) for r in registradas if len(r) >= 3 and r[2]}
    assert sinais - decididos == set(), "sinal/escalacao no .bpmn sem decisao registrada no catalogo"
