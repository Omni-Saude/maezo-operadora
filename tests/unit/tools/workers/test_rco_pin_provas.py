"""`load_rco_rules_pin` com pacote K1 2.1 (`provas[]`): runtime e gate usam os mesmos nomes declarados."""

from __future__ import annotations

from pathlib import Path

import pytest

from maezo.tools.workers import rco_pin
from tests.unit.platform import rco_k1_sintetico as sint


def _carregar(raiz: Path) -> rco_pin.RcoRulesPin:
    return rco_pin.load_rco_rules_pin(raiz / sint.LOCK_REL, rules_dir=raiz / sint.RULES_DIR)


def test_pin_21_com_provas_carrega(tmp_path: Path) -> None:
    sint.com_provas(tmp_path, sint.montar(tmp_path))
    assert (tmp_path / sint.K1_DIR / "provas" / sint.PROVA_ID / "dmn" / "prova-sint.dmn").is_file()
    assert "rco_sint_a" in _carregar(tmp_path).tables


def test_arquivo_solto_em_provas_e_recusado_no_runtime(tmp_path: Path) -> None:
    sint.com_provas(tmp_path, sint.montar(tmp_path))
    solto = tmp_path / sint.K1_DIR / "provas" / "outra" / "x.json"
    solto.parent.mkdir(parents=True)
    solto.write_text("{}", encoding="utf-8")
    with pytest.raises(rco_pin.RcoRulesPinError, match="provas/outra/x.json"):
        _carregar(tmp_path)


def test_arquivo_de_prova_adulterado_e_recusado_no_runtime(tmp_path: Path) -> None:
    sint.com_provas(tmp_path, sint.montar(tmp_path))
    (tmp_path / sint.K1_DIR / "provas" / sint.PROVA_ID / "dmn" / "prova-sint.dmn").write_bytes(b"x")
    with pytest.raises(rco_pin.RcoRulesPinError, match="sha256 diverge"):
        _carregar(tmp_path)
