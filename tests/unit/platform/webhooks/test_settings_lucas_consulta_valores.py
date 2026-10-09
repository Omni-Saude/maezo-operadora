"""`MAEZO_LUCAS_CONSULTA_VALORES` (DL-0086, revisao de seguranca do PR #709).

Nasce desligada; ligada EXIGE o acesso do beneficiario (ref. verificada, DL-0083) e a fonte `amh` —
sem verificacao quem segura o celular receberia os valores de outra pessoa, entao o settings recusa o
boot.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.unit.platform.webhooks.test_service_roteador import _amh, _settings


def test_nasce_desligada() -> None:
    assert _settings().lucas_consulta_valores is False


def test_le_o_nome_canonico_do_ambiente(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MAEZO_LUCAS_CONSULTA_VALORES", "true")
    monkeypatch.setenv("MAEZO_ACESSO_BENEFICIARIO", "true")
    assert _settings(**_amh(tmp_path)).lucas_consulta_valores is True


def test_sem_acesso_recusa_o_boot(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="MAEZO_ACESSO_BENEFICIARIO"):
        _settings(lucas_consulta_valores=True, **_amh(tmp_path))


def test_com_fonte_simulada_recusa_o_boot() -> None:
    with pytest.raises(ValidationError, match="MAEZO_LUCAS_FONTE_COBRANCA=amh"):
        _settings(lucas_consulta_valores=True, acesso_beneficiario=True)
