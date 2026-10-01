"""Os tres campos do roteador do numero unico em `WhatsAppWebhookSettings` (ADR-0062, plano §4)."""

from __future__ import annotations

import os

import pytest
from pydantic import ValidationError

from maezo.platform.webhooks.whatsapp.settings import WhatsAppWebhookSettings

_NOMES = ("MAEZO_ROTEADOR_LUCAS", "MAEZO_LUCAS_INATIVIDADE_MINUTOS", "MAEZO_LUCAS_FONTE_COBRANCA")


@pytest.fixture(autouse=True)
def _ambiente_limpo(monkeypatch: pytest.MonkeyPatch) -> None:
    for nome in list(os.environ):
        if nome in _NOMES:
            monkeypatch.delenv(nome, raising=False)


def _settings(**extra: object) -> WhatsAppWebhookSettings:
    return WhatsAppWebhookSettings(app_secret="s", verify_token="v", **extra)  # type: ignore[arg-type]


def test_defaults_desligado_60_minutos_fonte_simulada() -> None:
    s = _settings()
    assert s.roteador_lucas_enabled is False
    assert s.lucas_inatividade_minutos == 60
    assert s.lucas_fonte_cobranca == "simulada"


def test_le_os_nomes_canonicos_do_ambiente(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAEZO_ROTEADOR_LUCAS", "true")
    monkeypatch.setenv("MAEZO_LUCAS_INATIVIDADE_MINUTOS", "30")
    monkeypatch.setenv("MAEZO_LUCAS_FONTE_COBRANCA", "simulada")
    s = _settings()
    assert (s.roteador_lucas_enabled, s.lucas_inatividade_minutos, s.lucas_fonte_cobranca) == (
        True,
        30,
        "simulada",
    )


@pytest.mark.parametrize("minutos", [4, 0, -1, 1441])
def test_inatividade_fora_da_faixa_recusa_no_boot(minutos: int) -> None:
    with pytest.raises(ValidationError):
        _settings(lucas_inatividade_minutos=minutos)


@pytest.mark.parametrize("minutos", [5, 1440])
def test_inatividade_nas_bordas_da_faixa(minutos: int) -> None:
    assert _settings(lucas_inatividade_minutos=minutos).lucas_inatividade_minutos == minutos


def test_fonte_de_cobranca_que_nao_existe_recusa_no_boot() -> None:
    with pytest.raises(ValidationError):
        _settings(lucas_fonte_cobranca="cnab")
