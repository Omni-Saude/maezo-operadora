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


# --- Fonte AMH do Lucas (decisao do dono 06/10/2026) ---------------------------------------------

_NOMES_AMH = (
    "MAEZO_AMH_INTEROP_BASE_URL",
    "MAEZO_AMH_INTEROP_TOKEN_URL",
    "MAEZO_AMH_INTEROP_CLIENT_ID",
    "MAEZO_AMH_INTEROP_SCOPES",
    "MAEZO_AMH_INTEROP_CLIENT_SECRET",
    "MAEZO_AMH_PHONE_LOOKUP_KEY",
    "MAEZO_AMH_INTEROP_TENANT",
    "MAEZO_AMH_INTEROP_PURPOSE_OF_USE",
    "MAEZO_AMH_BILLING_STATUS_OPENAPI_PATH",
    "MAEZO_AMH_SUBJECT_RESOLUTION_OPENAPI_PATH",
    "AMH_INTEROP_CLIENT_SECRET",
    "AMH_PHONE_LOOKUP_KEY",
)


@pytest.fixture
def _ambiente_amh_limpo(monkeypatch: pytest.MonkeyPatch) -> None:
    for nome in _NOMES_AMH:
        monkeypatch.delenv(nome, raising=False)


def test_fonte_amh_e_aceita_e_os_defaults_ficam_desligados(_ambiente_amh_limpo: None) -> None:
    s = _settings(lucas_fonte_cobranca="amh")
    assert s.lucas_fonte_cobranca == "amh"
    assert s.amh_interop_base_url is None
    assert s.amh_interop_token_url is None
    assert s.amh_interop_client_secret is None
    assert s.amh_phone_lookup_key is None
    assert s.amh_interop_tenant == "austa_operadora"
    assert s.amh_interop_purpose_of_use == "sharing_amh_internal"
    assert s.amh_interop_scopes.split() == [
        "interop/billing.read",
        "interop/subject.resolve",
        "interop/profile.read",
    ]


def test_fonte_amh_le_os_nomes_canonicos_do_ambiente(
    monkeypatch: pytest.MonkeyPatch, _ambiente_amh_limpo: None
) -> None:
    monkeypatch.setenv("MAEZO_LUCAS_FONTE_COBRANCA", "amh")
    monkeypatch.setenv("MAEZO_AMH_INTEROP_BASE_URL", "http://alb-interno")
    monkeypatch.setenv("MAEZO_AMH_INTEROP_TOKEN_URL", "https://cognito.example/oauth2/token")
    monkeypatch.setenv("MAEZO_AMH_INTEROP_CLIENT_ID", "cliente")
    monkeypatch.setenv("MAEZO_AMH_INTEROP_CLIENT_SECRET", "segredo-SINTETICO")
    monkeypatch.setenv("MAEZO_AMH_PHONE_LOOKUP_KEY", "chave-SINTETICA")
    monkeypatch.setenv("MAEZO_AMH_BILLING_STATUS_OPENAPI_PATH", "/contratos/billing.yaml")
    s = _settings()
    assert s.lucas_fonte_cobranca == "amh"
    assert s.amh_interop_base_url == "http://alb-interno"
    assert s.amh_interop_client_secret == "segredo-SINTETICO"
    assert s.amh_phone_lookup_key == "chave-SINTETICA"
    assert s.amh_billing_status_openapi_path == "/contratos/billing.yaml"
    # segredos nunca renderizados nem exportados
    assert "segredo-SINTETICO" not in repr(s) and "chave-SINTETICA" not in repr(s)
    assert "amh_interop_client_secret" not in s.model_dump()
    assert "amh_phone_lookup_key" not in s.model_dump()


def test_segredos_amh_nao_abrem_nome_sem_prefixo_no_ambiente(
    monkeypatch: pytest.MonkeyPatch, _ambiente_amh_limpo: None
) -> None:
    monkeypatch.setenv("AMH_INTEROP_CLIENT_SECRET", "nao-deveria-entrar")
    monkeypatch.setenv("AMH_PHONE_LOOKUP_KEY", "nao-deveria-entrar")
    s = _settings()
    assert s.amh_interop_client_secret is None
    assert s.amh_phone_lookup_key is None
    # mas o kwarg de construcao pelo nome do campo continua funcionando
    assert _settings(amh_interop_client_secret="x").amh_interop_client_secret == "x"


# --- Identidade do beneficiario na Helena (DL-0077) ------------------------------------------------


def test_helena_identidade_amh_default_desligado(
    _ambiente_amh_limpo: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MAEZO_HELENA_IDENTIDADE_AMH", raising=False)
    assert _settings().helena_identidade_amh is False


def test_helena_identidade_amh_le_o_nome_canonico_do_ambiente(
    monkeypatch: pytest.MonkeyPatch, _ambiente_amh_limpo: None
) -> None:
    monkeypatch.setenv("MAEZO_HELENA_IDENTIDADE_AMH", "true")
    assert _settings().helena_identidade_amh is True
    monkeypatch.setenv("MAEZO_HELENA_IDENTIDADE_AMH", "false")
    assert _settings().helena_identidade_amh is False


def test_helena_identidade_amh_independe_da_fonte_do_lucas(_ambiente_amh_limpo: None) -> None:
    s = _settings(helena_identidade_amh=True)
    assert s.helena_identidade_amh is True
    assert s.lucas_fonte_cobranca == "simulada"
    assert s.roteador_lucas_enabled is False
