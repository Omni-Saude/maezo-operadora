"""Fence R-053 / F-109: DMN de PAGTO e contrato SP-OP-PAGTO-001 dizem a MESMA coisa.

Duas divergencias provadas pela auditoria forense (F-109), ambas tree-derived aqui (lidas dos
arquivos reais, nunca copiadas a mao):

1. `pagto_sla`: o contrato (secao SLAs) fixa o alerta de risco em 60-70% do prazo de aprovacao;
   a DMN emitia razoes 50%/67%/75%/80%. O teste parseia cada regra da tabela (ISO 8601 em
   dias/horas) e exige `sla_alerta / sla_aprovacao` em [0.60, 0.70] para TODA regra, catch-all
   incluso.
2. `pagto_alcada`: todo `grupo_aprovador` emitido pela DMN tem de estar declarado no contrato
   (a faixa clerical emitia `clerical-pagamentos`, que o contrato listava como "(nenhum)").

A DMN e a fonte unica da regra (AGENTS.md regra 5); o contrato espelha a DMN.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_DMN_DIR = _ROOT / "spec" / "processes" / "dmn"
_CONTRATO = _ROOT / "docs" / "processes" / "contracts" / "SP-OP-PAGTO-001.md"
_NS = {"dmn": "https://www.omg.org/spec/DMN/20191111/MODEL/"}
_ISO = re.compile(r"^P(?:(?P<d>\d+)D)?(?:T(?P<h>\d+)H)?$")


def _horas(iso: str) -> int:
    match = _ISO.match(iso)
    assert match is not None and (match["d"] or match["h"]), f"duracao ISO nao suportada: {iso!r}"
    return int(match["d"] or 0) * 24 + int(match["h"] or 0)


def _regras(dmn_file: str) -> list[dict[str, str]]:
    root = ET.parse(_DMN_DIR / dmn_file).getroot()  # noqa: S314 - spec do proprio repo
    table = root.find(".//dmn:decisionTable", _NS)
    assert table is not None
    names = [o.get("name", "") for o in table.findall("dmn:output", _NS)]
    rows = []
    for rule in table.findall("dmn:rule", _NS):
        texts = [
            (entry.findtext("dmn:text", "", _NS) or "").strip().strip('"')
            for entry in rule.findall("dmn:outputEntry", _NS)
        ]
        rows.append({"id": rule.get("id", ""), **dict(zip(names, texts, strict=True))})
    return rows


_SLA_ROWS = _regras("pagto_sla.dmn")


def test_pagto_sla_cobre_todas_as_faixas_e_o_catch_all() -> None:
    assert len(_SLA_ROWS) >= 4


@pytest.mark.parametrize("row", _SLA_ROWS, ids=[r["id"] for r in _SLA_ROWS])
def test_pagto_sla_alerta_entre_60_e_70_por_cento_da_aprovacao(row: dict[str, str]) -> None:
    ratio = _horas(row["sla_alerta"]) / _horas(row["sla_aprovacao"])
    assert 0.60 <= ratio <= 0.70, f"{row['id']}: alerta/aprovacao = {ratio:.2f} fora de 60-70%"


@pytest.mark.parametrize("row", _SLA_ROWS, ids=[r["id"] for r in _SLA_ROWS])
def test_pagto_sla_prazo_de_aprovacao_inalterado(row: dict[str, str]) -> None:
    """R-053 alinha SO o alerta; o prazo de aprovacao (DRAFT/verify financas) nao e re-decidido."""
    assert row["sla_aprovacao"] in {"P2D", "P3D", "P4D", "P5D"}


def test_pagto_alcada_grupos_declarados_no_contrato() -> None:
    contrato = _CONTRATO.read_text(encoding="utf-8")
    grupos = {row["grupo_aprovador"] for row in _regras("pagto_alcada.dmn")}
    assert grupos, "pagto_alcada sem grupo_aprovador"
    faltando = sorted(g for g in grupos if f"`{g}`" not in contrato)
    assert faltando == [], f"grupos emitidos pela DMN e ausentes do contrato: {faltando}"


def test_contrato_nao_declara_faixa_clerical_sem_grupo() -> None:
    assert "(nenhum — caminho clerical L2)" not in _CONTRATO.read_text(encoding="utf-8")
