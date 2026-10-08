"""Acesso do beneficiario (DL-0083): flag, defaults, composicao fail-closed do receptor."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import maezo.platform.webhooks.service as svc
from tests.unit.platform.webhooks.test_service_roteador import _amh, _checkpointer_ok, _settings

__all__ = ["_checkpointer_ok"]  # fixture reexportada

_ESCOPOS = (
    "interop/billing.read interop/subject.resolve interop/profile.read "
    "interop/subject.verify interop/consent.write"
)


def test_defaults_desligado_e_validade_24h(monkeypatch: pytest.MonkeyPatch) -> None:
    for nome in ("MAEZO_ACESSO_BENEFICIARIO", "MAEZO_ACESSO_VALIDADE_HORAS", "MAEZO_AMH_SUBJECT_VERIFY_KEY"):
        monkeypatch.delenv(nome, raising=False)
    s = _settings()
    assert s.acesso_beneficiario is False and s.acesso_validade_horas == 24
    assert s.amh_subject_verify_key is None
    assert "interop/subject.verify" in s.amh_interop_scopes.split()
    assert "interop/consent.write" in s.amh_interop_scopes.split()


def test_le_os_nomes_canonicos_e_a_chave_nao_aparece_no_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAEZO_ACESSO_BENEFICIARIO", "true")
    monkeypatch.setenv("MAEZO_ACESSO_VALIDADE_HORAS", "12")
    monkeypatch.setenv("MAEZO_AMH_SUBJECT_VERIFY_KEY", "chave-SINTETICA-de-verificacao")
    s = _settings()
    assert s.acesso_beneficiario is True and s.acesso_validade_horas == 12
    assert s.amh_subject_verify_key == "chave-SINTETICA-de-verificacao"
    assert "chave-SINTETICA" not in repr(s) and "chave-SINTETICA" not in str(s.model_dump())


@pytest.mark.parametrize("valor", [0, -1, 169])
def test_validade_fora_da_faixa_recusa_no_boot(valor: int) -> None:
    with pytest.raises(ValueError):
        _settings(acesso_validade_horas=valor)


def test_desligado_o_despachante_nao_tem_acesso_nem_ponte() -> None:
    dispatcher, _ = svc._build_dispatcher(_settings())
    assert dispatcher.acesso is None and dispatcher.refs_verificadas is None
    assert svc._build_acesso(_settings(), dispatcher) is None


async def test_ligado_sem_configuracao_recusa_servir(_checkpointer_ok: None) -> None:  # noqa: F811
    state = svc.WebhookState(settings=_settings(runtime_mode="kubernetes", acesso_beneficiario=True))
    await svc._bring_up_dependencies(state)
    assert state.dispatcher is None and state.acesso is None
    assert state.dispatcher_error


async def test_ligado_com_tudo_mas_sem_pin_v1_3_recusa_servir(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    _checkpointer_ok: None,  # noqa: F811
) -> None:
    """Estado real de hoje: o lock NAO tem `manifest_v1_3`. Com billing/subject resolvidos, e' o adaptador de
    verificacao que recusa, e com ele o boot inteiro (nunca sobe 'sem acesso' em silencio)."""
    from maezo.adapters.amh import billing_status as bs
    from maezo.adapters.amh import subject_resolution as sr

    class _Adapter:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

    monkeypatch.setattr(bs, "AmhBillingStatusAdapter", _Adapter)
    monkeypatch.setattr(sr, "AmhSubjectResolutionAdapter", _Adapter)
    base = Path(str(tmp_path))
    (base / "ver.yaml").write_bytes(b"v")
    (base / "con.yaml").write_bytes(b"c")
    valores = _amh(
        tmp_path,
        roteador_lucas_enabled=False,
        lucas_fonte_cobranca="simulada",
        acesso_beneficiario=True,
        amh_interop_scopes=_ESCOPOS,
        amh_subject_verify_key="chave-SINTETICA-de-verificacao",
        amh_subject_verification_openapi_path=str(base / "ver.yaml"),
        amh_consent_record_openapi_path=str(base / "con.yaml"),
    )
    state = svc.WebhookState(settings=_settings(runtime_mode="kubernetes", **valores))
    await svc._bring_up_dependencies(state)
    assert state.dispatcher is None and state.acesso is None
    erro = state.dispatcher_error or ""
    assert "access build failed" in erro and "subject_verification_contract_invalid" in erro
    assert "chave-SINTETICA" not in erro and "segredo-SINTETICO" not in erro
