"""DL-0186 (unidade 2b): `filas.<f>.grupo_principal` do K6 lido como DADO, estrito na forma, exposto no pin.

O recorte REAL do K1 2.3.0 (`tests/fixtures/rco/k1-2.3.0-recorte/k6-filas.json`) declara `grupo_principal` em
algumas filas e nao em outras (`auditoria_enfermagem` tem so `grupo_a_criar`): ausente e permitido e a fila
fica sem grupo. Valor fora da forma de token recusa o pacote no leitor, no gate e no runtime (`grupos-k6-
invalido`). Abaixo de 2.3.0 nada e lido, como `avisos`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from maezo.platform import rco_manifesto as k1
from maezo.tools.workers import rco_pin
from tests.unit.platform import rco_k1_2_3_recorte as rec
from tests.unit.platform import rco_k1_sintetico as sint


def _ler(raiz: Path) -> k1.ManifestoK1:
    return k1.ler_manifesto((raiz / sint.K1_DIR / "manifesto.json").read_bytes())


def _pin(raiz: Path) -> rco_pin.RcoRulesPin:
    return rco_pin.load_rco_rules_pin(raiz / sint.LOCK_REL, rules_dir=raiz / sint.RULES_DIR)


def _filas(**grupos: Any) -> dict[str, Any]:
    filas: dict[str, Any] = {nome: dict(item) for nome, item in sint.K6_FILAS.items()}
    for fila, grupo in grupos.items():
        filas[fila]["grupo_principal"] = grupo
    return filas


def test_recorte_real_le_os_grupos_e_deixa_sem_grupo_a_fila_que_nao_declara() -> None:
    grupos = k1.carregar_grupos_k6(rec.manifesto(), k1_dir=rec.RECORTE)
    assert grupos["auditoria_medica"] == "medico-auditor"
    assert grupos["cadastro"] == "cadastro-beneficiarios"
    assert "auditoria_enfermagem" not in grupos  # so `grupo_a_criar` (proposta): nao e grupo
    # os destinos do aviso real tem grupo
    avisos = k1.carregar_avisos_k6(rec.manifesto(), rec.vocabulario(), k1_dir=rec.RECORTE)
    assert all(f in grupos for a in avisos.values() for f in a.filas)


def test_pin_sintetico_expoe_grupos_ao_lado_de_avisos(tmp_path: Path) -> None:
    sint.com_formato_2_3(
        tmp_path,
        sint.montar(tmp_path),
        k6=sint.k6_2_3(filas=_filas(auditoria_medica="grupo-sint-a", cadastro="grupo-sint-b")),
    )
    pin = _pin(tmp_path)
    assert dict(pin.grupos_principais) == {"auditoria_medica": "grupo-sint-a", "cadastro": "grupo-sint-b"}
    assert set(pin.avisos) == {sint.SINAL}
    with pytest.raises(TypeError):
        pin.grupos_principais["regulacao"] = "x"  # type: ignore[index]


def test_k6_sem_grupo_algum_continua_aceito_e_sem_grupos(tmp_path: Path) -> None:
    sint.com_formato_2_3(tmp_path, sint.montar(tmp_path))  # K6 sintetico padrao: nenhuma fila com grupo
    assert (
        k1.verificar_pacote(
            _ler(tmp_path), k1_dir=tmp_path / sint.K1_DIR, rules_dir=tmp_path / sint.RULES_DIR
        )
        == []
    )
    assert dict(_pin(tmp_path).grupos_principais) == {}


@pytest.mark.parametrize("ruim", ["", "grupo com espaco", 7, None, ["medico-auditor"], "x" * 65, "grupo/a"])
def test_grupo_fora_da_forma_recusa_no_leitor_no_gate_e_no_runtime(tmp_path: Path, ruim: Any) -> None:
    sint.com_formato_2_3(tmp_path, sint.montar(tmp_path), k6=sint.k6_2_3(filas=_filas(cadastro=ruim)))
    lido = _ler(tmp_path)
    with pytest.raises(k1.ManifestoK1InvalidoError) as exc:
        k1.carregar_grupos_k6(lido, k1_dir=tmp_path / sint.K1_DIR)
    assert "grupos-k6-invalido" in str(exc.value)
    problemas = k1.verificar_pacote(lido, k1_dir=tmp_path / sint.K1_DIR, rules_dir=tmp_path / sint.RULES_DIR)
    assert any("grupos-k6-invalido" in p for p in problemas)
    with pytest.raises(rco_pin.RcoRulesPinError):
        _pin(tmp_path)


def test_fila_que_nao_e_objeto_fica_sem_grupo(tmp_path: Path) -> None:
    filas = _filas(cadastro="grupo-sint-b")
    filas["regulacao"] = "nao-e-objeto"
    sint.com_formato_2_3(tmp_path, sint.montar(tmp_path), k6=sint.k6_2_3(filas=filas))
    assert dict(k1.carregar_grupos_k6(_ler(tmp_path), k1_dir=tmp_path / sint.K1_DIR)) == {
        "cadastro": "grupo-sint-b"
    }


def test_abaixo_de_2_3_nada_e_lido(tmp_path: Path) -> None:
    sint.com_composicoes(tmp_path, sint.montar(tmp_path))
    assert k1.carregar_grupos_k6(_ler(tmp_path), k1_dir=tmp_path / sint.K1_DIR) == {}
    assert dict(_pin(tmp_path).grupos_principais) == {}


def test_k6_ilegivel_nao_repete_o_problema_no_gate(tmp_path: Path) -> None:
    manifesto = sint.com_formato_2_3(tmp_path, sint.montar(tmp_path))
    sint.trocar_k6(tmp_path, manifesto, b"{nao e json")
    problemas = k1.verificar_pacote(
        _ler(tmp_path), k1_dir=tmp_path / sint.K1_DIR, rules_dir=tmp_path / sint.RULES_DIR
    )
    assert sum("k6-filas.json: JSON ilegivel" in p for p in problemas) == 1
    assert not any("grupos-k6-invalido" in p for p in problemas)
