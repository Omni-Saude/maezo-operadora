"""Portao do BPMN com fila no portal x admissao Q2 viva (incidente de 17/10/2026, SP-OP-ESCALATION-001 v3)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from scripts.ci.check_portal_admissao import ESPELHO, avaliar, main

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BPMN = b"""<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" id="d">
  <bpmn:process id="SP-OP-X-001" isExecutable="true"><bpmn:userTask id="UT_A"/></bpmn:process>
</bpmn:definitions>
"""
_SHA = hashlib.sha256(_BPMN).hexdigest()
_OUTRO = "0" * 64


def _espelho(admitido: str, aguardando: list[dict[str, str]] | None = None) -> bytes:
    return json.dumps(
        {
            "schema": "maezo-portal-admissao.v1",
            "admitidos": [
                {
                    "process_definition_key": "SP-OP-X-001",
                    "process_definition_version": "3",
                    "process_definition_id": "SP-OP-X-001:3:54460c89-ca4f-11f1-9fd8-02ffe2b01f3f",
                    "process_definition_digest": admitido,
                    "tarefas": ["UT_A"],
                }
            ],
            "aguardando_admissao": aguardando or [],
        }
    ).encode()


@pytest.fixture
def pasta(tmp_path: Path) -> Path:
    (tmp_path / "SP-OP-X-001_X.bpmn").write_bytes(_BPMN)
    return tmp_path


def test_bytes_admitidos_passam(pasta: Path) -> None:
    assert avaliar(_espelho(_SHA), pasta)[0] == 0


def test_bpmn_mudado_sem_admissao_reprova(pasta: Path) -> None:
    codigo, linhas = avaliar(_espelho(_OUTRO), pasta)
    assert codigo == 1
    assert any("NAO e admitido no portal" in linha for linha in linhas)


def test_bpmn_mudado_declarado_aguardando_avisa_e_passa(pasta: Path) -> None:
    pend = [
        {"process_definition_key": "SP-OP-X-001", "process_definition_digest": _SHA, "motivo": "#1 ainda nao"}
    ]
    codigo, linhas = avaliar(_espelho(_OUTRO, pend), pasta)
    assert codigo == 0
    assert any(linha.startswith("::warning::") for linha in linhas)


def test_aguardando_velho_ou_ja_admitido_reprova(pasta: Path) -> None:
    velho = [{"process_definition_key": "SP-OP-X-001", "process_definition_digest": "1" * 64, "motivo": "x"}]
    assert avaliar(_espelho(_SHA, velho), pasta)[0] == 1
    admitido = [{"process_definition_key": "SP-OP-X-001", "process_definition_digest": _SHA, "motivo": "x"}]
    assert avaliar(_espelho(_SHA, admitido), pasta)[0] == 1


def test_processo_admitido_sem_bpmn_reprova(tmp_path: Path) -> None:
    assert avaliar(_espelho(_SHA), tmp_path)[0] == 1


@pytest.mark.parametrize(
    "raw",
    [b"{", b'{"schema": "outro"}', b'{"schema": "maezo-portal-admissao.v1", "admitidos": []}'],
)
def test_espelho_invalido_sai_2(raw: bytes, pasta: Path) -> None:
    assert avaliar(raw, pasta)[0] == 2


def test_id_de_outra_chave_e_invalido(pasta: Path) -> None:
    raw = _espelho(_SHA).replace(b'"SP-OP-X-001:3:', b'"SP-OP-Y-001:3:')
    assert avaliar(raw, pasta)[0] == 2


def test_repo_real_passa() -> None:
    """O espelho versionado e os BPMN da arvore estao coerentes (o portao do CI)."""
    assert (
        main(["--espelho", str(_REPO_ROOT / ESPELHO), "--bpmn", str(_REPO_ROOT / "spec/processes/bpmn")]) == 0
    )
