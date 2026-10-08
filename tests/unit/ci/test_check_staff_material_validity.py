"""Alarme de vencimento do material staff/human de dev (incidente de 08/10/2026)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from scripts.ci.check_staff_material_validity import ARQUIVO, avaliar, main

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BASE = {
    "schema": "maezo-staff-material-validity.v1",
    "valid_until": "2026-10-22T18:19:46Z",
    "renovado_em": "2026-10-08T18:20:46Z",
    "designation_revision": "3",
    "admission_revision": "6",
    "aviso_dias": 3,
    "runbook": "docs/runbooks/renovar-material-staff-dev.md",
}


def _agora(valor: str) -> datetime:
    return datetime.strptime(valor, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


@pytest.mark.parametrize(
    ("agora", "codigo"),
    [
        ("2026-10-09T00:00:00Z", 0),  # folga
        ("2026-10-19T18:19:45Z", 0),  # faltam 3 dias e 1 s: ainda nao avisa
        ("2026-10-19T18:19:46Z", 1),  # exatamente 3 dias: avisa
        ("2026-10-22T18:19:45Z", 1),  # ultimo segundo
        ("2026-10-22T18:19:46Z", 1),  # vencido: o motor nao sobe no proximo restart
    ],
)
def test_avisa_a_partir_de_tres_dias(agora: str, codigo: int) -> None:
    assert avaliar(dict(_BASE), _agora(agora))[0] == codigo


def test_mensagem_de_vencido_aponta_o_runbook() -> None:
    codigo, mensagem = avaliar(dict(_BASE), _agora("2026-10-23T00:00:00Z"))
    assert codigo == 1 and "VENCEU" in mensagem and _BASE["runbook"] in mensagem


@pytest.mark.parametrize(
    "mudanca",
    [
        {"valid_until": "2026-10-23T18:20:47Z"},  # janela > 14 dias (N2)
        {"valid_until": "2026-10-08T18:20:46Z"},  # janela vazia
        {"valid_until": "2026-10-22 18:19:46"},  # sem Z
        {"aviso_dias": 0},
        {"aviso_dias": True},
        {"schema": "outro"},
        {"extra": "x"},
    ],
)
def test_arquivo_fora_da_forma_e_recusado(mudanca: dict[str, object]) -> None:
    assert avaliar({**_BASE, **mudanca}, _agora("2026-10-09T00:00:00Z"))[0] == 2


def test_arquivo_versionado_tem_a_forma_certa() -> None:
    doc = json.loads((_REPO_ROOT / ARQUIVO).read_text(encoding="utf-8"))
    codigo, _ = avaliar(doc, _agora(doc["renovado_em"]))
    assert codigo == 0
    assert (_REPO_ROOT / doc["runbook"]).is_file()


def test_main_le_o_arquivo(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    arquivo = tmp_path / "v.json"
    arquivo.write_text(json.dumps(_BASE), encoding="utf-8")
    assert main(["--arquivo", str(arquivo), "--agora", "2026-10-21T00:00:00Z"]) == 1
    assert capsys.readouterr().out.startswith("::error::")
    assert main(["--arquivo", str(tmp_path / "nao-existe.json")]) == 2
