"""A cerca de perspectiva sobre as tabelas K1 do rco (ADR-0040 D7; ADR-0075 §(b) ponto 3; PR-A).

O vocabulario D7 vale para `spec/processes/dmn/rco/` SEM isencao: a pasta e lida como o primeiro nivel. As 236
tabelas do rco em b8444f52 davam 2 hits, ambos R2-CTX X2 em `geral_prazos_conta.dmn` (copia em
`tests/fixtures/rco/perspectiva-b8444f52/`), em duas frases do pagador que RECEBE o recurso de glosa. A
extensao e EXATA (`perspective.RECEBIMENTO_PELO_PAGADOR`): so absolve o X2 cujo trecho casado esta dentro
de uma das duas frases, sem janela; qualquer outra redacao continua reprovando.

A varredura das 236 tabelas reais roda quando `MAEZO_RCO_CHECKOUT` aponta um clone do rco com o commit
(leitura por `git show`, sem alterar o clone); sem ele, so a tabela vendorizada e conferida.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from maezo.platform.validation import perspective
from maezo.platform.validation.perspective import TIER_A, scan_line, scan_xml_file
from maezo.platform.validation.result import Report

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "rco" / "perspectiva-b8444f52"
TABELA = FIXTURE / "geral_prazos_conta.dmn"
COMMIT_RCO = "b8444f52e33eda556b5d2dab7837b046d5bd769e"

_DMN = """<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="https://www.omg.org/spec/DMN/20191111/MODEL/" id="d" name="d" namespace="http://x.invalid">
  <decision id="rco_x" name="rco_x">
    <description>{texto}</description>
  </decision>
</definitions>
"""


def _x2(texto: str) -> list[str]:
    return [m for _, r, m in scan_line(texto, tier=TIER_A) if r == "X2"]


def _sem_a_extensao() -> tuple[perspective.ProseRule, ...]:
    return tuple(
        dataclasses.replace(r, phrase_absolves=None) if r.rule_id == "X2" else r
        for r in perspective.R2_CTX_RULES
    )


# --- (b) as tabelas reais passam -------------------------------------------------------------------------


def test_a_copia_e_a_do_rco() -> None:
    origem = json.loads((FIXTURE / "ORIGEM.json").read_text(encoding="utf-8"))
    assert origem["origem_commit"] == COMMIT_RCO
    assert {p.name for p in FIXTURE.iterdir()} == {"ORIGEM.json", "README.md", TABELA.name}
    raw = TABELA.read_bytes()
    assert "sha256:" + hashlib.sha256(raw).hexdigest() == origem["arquivos"][TABELA.name]["sha256"]


def test_a_tabela_real_passa_com_zero_hits() -> None:
    report = Report()
    assert scan_xml_file(TABELA, report) == []
    assert report.ok


def test_sem_a_extensao_a_tabela_real_da_exatamente_os_dois_hits_x2() -> None:
    """Nao-vacuidade: as duas frases da extensao sao as que disparavam, uma por linha (4 e 15)."""
    linhas = TABELA.read_text(encoding="utf-8").splitlines()
    regras = perspective.R2_CORE_RULES + _sem_a_extensao()
    achados: dict[int, list[tuple[str, str, str]]] = {}
    for n, linha in enumerate(linhas, start=1):
        x2 = [h for h in perspective._scan_prose(linha, regras) if h[1] == "X2"]
        if x2:
            achados[n] = x2
    assert achados == {4: [("R2-CTX", "X2", "recebido")], 15: [("R2-CTX", "X2", "recebimento")]}
    for frase in perspective.RECEBIMENTO_PELO_PAGADOR:
        assert sum(frase in perspective.fold(linha) for linha in linhas) == 1, frase


@pytest.mark.skipif(not os.environ.get("MAEZO_RCO_CHECKOUT"), reason="sem clone do rco (MAEZO_RCO_CHECKOUT)")
def test_as_236_tabelas_reais_do_commit_passam(tmp_path: Path) -> None:
    clone = os.environ["MAEZO_RCO_CHECKOUT"]
    listagem = subprocess.run(
        ["git", "-C", clone, "ls-tree", "-r", "--name-only", COMMIT_RCO, "dmn"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    caminhos = [c for c in listagem if c.endswith(".dmn")]
    assert len(caminhos) == 236
    report = Report()
    hits = []
    for i, caminho in enumerate(caminhos):
        destino = tmp_path / f"{i:03d}.dmn"
        destino.write_bytes(
            subprocess.run(
                ["git", "-C", clone, "show", f"{COMMIT_RCO}:{caminho}"], check=True, capture_output=True
            ).stdout
        )
        hits.extend(scan_xml_file(destino, report))
    assert hits == [] and report.ok


# --- (a) a pasta rco/ segue lida, sem isencao -----------------------------------------------------------


@pytest.mark.parametrize(
    "texto",
    [
        "Conferir o demonstrativo recebido",
        "recurso de glosa recebido mais de 30 dias apos o demonstrativo; conferir o demonstrativo recebido",
    ],
)
def test_texto_de_perspectiva_errada_em_rco_reprova(tmp_path: Path, texto: str) -> None:
    rco = tmp_path / "dmn" / "rco"
    rco.mkdir(parents=True)
    (rco / "rco_x.dmn").write_text(_DMN.format(texto=texto), encoding="utf-8")
    report = Report()
    perspective.check_processes_root(tmp_path, report)
    assert {f.path for f in report.findings} == {rco / "rco_x.dmn"}
    assert all("X2" in f.message for f in report.findings)


def test_as_frases_exatas_em_rco_passam(tmp_path: Path) -> None:
    rco = tmp_path / "dmn" / "rco"
    rco.mkdir(parents=True)
    texto = "; ".join(perspective.RECEBIMENTO_PELO_PAGADOR)
    (rco / "rco_x.dmn").write_text(_DMN.format(texto=texto), encoding="utf-8")
    report = Report()
    perspective.check_processes_root(tmp_path, report)
    assert report.ok


# --- (c) a extensao e exata: variante proxima continua reprovando --------------------------------------


@pytest.mark.parametrize(
    "texto",
    [
        # o prestador (perspectiva do credor) recebendo o demonstrativo, com redacao proxima das frases
        "demonstrativo de glosa recebido mais de 30 dias apos o recurso",
        "recebido o demonstrativo, recurso de glosa mais de 30 dias apos",
        "entre o recebimento do demonstrativo de analise da conta e o lote de recurso de glosa",
        "o recebimento do demonstrativo de analise da conta e o lote de recurso de glosa",
        # a frase com qualquer palavra trocada, a mais ou a menos
        "recurso de glosa recebido mais de 31 dias apos o demonstrativo",
        "recurso de glosa recebido ate 30 dias apos o demonstrativo",
        "recurso de glosa  recebido mais de 30 dias apos o demonstrativo",
        "recurso recebido mais de 30 dias apos o demonstrativo",
        "entre o demonstrativo de analise da conta e o recebimento do lote de glosa",
        "entre o demonstrativo da conta e o recebimento do lote de recurso de glosa",
    ],
)
def test_variante_proxima_continua_reprovando(texto: str) -> None:
    assert _x2(texto), texto


def test_a_frase_nao_absolve_outro_trecho_da_mesma_linha() -> None:
    frase = perspective.RECEBIMENTO_PELO_PAGADOR[0]
    assert _x2(frase) == []
    assert _x2(f"Conferir o demonstrativo recebido. {frase}") == ["Conferir", "recebido"]


def test_as_frases_com_acento_sao_as_da_tabela() -> None:
    """A tabela traz `após` e `análise`: a comparacao e no texto dobrado (sem acento), como toda regra."""
    assert _x2("recurso de glosa recebido mais de 30 dias após o demonstrativo") == []
    assert (
        _x2(
            "dias corridos entre o demonstrativo de análise da conta e o recebimento "
            "do lote de recurso de glosa"
        )
        == []
    )


def test_so_x2_ganhou_a_extensao() -> None:
    com = [r.rule_id for r in (*perspective.R2_CORE_RULES, *perspective.R2_CTX_RULES) if r.phrase_absolves]
    assert com == ["X2"]
    assert len(perspective.RECEBIMENTO_PELO_PAGADOR) == 2
