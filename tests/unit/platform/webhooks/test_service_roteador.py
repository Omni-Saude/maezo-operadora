"""A composicao do roteador em `service.py` (ADR-0062, plano §4: "construcao dentro do `if`").

Desligado, `_build_dispatcher` entrega `roteador=None` e nem importa o modulo do roteador por esse
caminho; ligado, entrega um `ConversaRouter` sobre o adaptador Postgres (pool preguicoso: nenhuma
conexao e' aberta na construcao). Um lexico invalido recusa no boot.
"""

from __future__ import annotations

import pytest

import maezo.platform.webhooks.service as svc
from maezo.platform.webhooks.whatsapp import pre_roteamento
from maezo.platform.webhooks.whatsapp.roteamento import ConversaRouter, PostgresAgenteAtivoStore
from maezo.platform.webhooks.whatsapp.settings import WhatsAppWebhookSettings

_DSN = "postgresql+asyncpg://user:pw@localhost:5432/maezo"


def _settings(**overrides: object) -> WhatsAppWebhookSettings:
    base: dict[str, object] = {
        "app_secret": "s3cret",
        "verify_token": "vt",
        "tenant_id": "amh",
        "phi_hmac_key": "test-phi-hmac-key",
        "database_url": _DSN,
    }
    base.update(overrides)
    return WhatsAppWebhookSettings(**base)  # type: ignore[arg-type]


def test_desligado_o_despachante_nasce_sem_roteador() -> None:
    dispatcher, _ = svc._build_dispatcher(_settings())
    assert dispatcher.roteador is None


def test_ligado_o_despachante_recebe_o_roteador_em_sombra() -> None:
    dispatcher, _ = svc._build_dispatcher(
        _settings(roteador_lucas_enabled=True, lucas_inatividade_minutos=45)
    )
    roteador = dispatcher.roteador
    assert isinstance(roteador, ConversaRouter)
    assert isinstance(roteador._store, PostgresAgenteAtivoStore)
    assert roteador._inatividade.total_seconds() == 45 * 60
    # onda (e): o Lucas nasce sob o MESMO interruptor (e uma falha dele recusa servir), entao o
    # roteador ja' nasce sabendo que ha' quem atenda. Sem `lucas_turno` no despachante, o
    # despachante nem pede handoff (sombra da onda c).
    assert roteador._lucas_disponivel is True
    assert roteador.versao_lexicos == "pre-roteamento-v1"


def test_ligado_com_lexico_invalido_recusa_no_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    def _quebrado() -> pre_roteamento.PreRoteamento:
        raise pre_roteamento.LexicoInvalidoError("pre-roteamento: teste")

    monkeypatch.setattr(pre_roteamento, "carregar", _quebrado)
    with pytest.raises(pre_roteamento.LexicoInvalidoError):
        svc._build_dispatcher(_settings(roteador_lucas_enabled=True))
    # desligado, o lexico nem e' lido
    assert svc._build_dispatcher(_settings())[0].roteador is None


# --- Onda (d): o turno do Lucas (`_build_lucas_turno`) -------------------------------------------


async def _ok_connect(conn_string: str) -> object:
    from langgraph.checkpoint.memory import InMemorySaver

    from maezo.runtime.checkpoint import Checkpointer

    del conn_string
    return Checkpointer(saver=InMemorySaver())


@pytest.fixture
def _checkpointer_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc.Checkpointer, "connect_and_setup", classmethod(lambda cls, dsn: _ok_connect(dsn)))


def test_desligado_nao_ha_turno_do_lucas() -> None:
    settings = _settings()
    dispatcher, _ = svc._build_dispatcher(settings)
    assert svc._build_lucas_turno(settings, dispatcher) is None


def test_ligado_os_seams_do_lucas_sao_do_principal_lucas() -> None:
    from maezo.agents.lucas.fonte_cobranca import FonteCobrancaSimulada
    from maezo.gateway.seams import is_gated_seam
    from maezo.platform.webhooks.whatsapp.lucas_turno import LucasTurno

    settings = _settings(roteador_lucas_enabled=True)
    dispatcher, _ = svc._build_dispatcher(settings)
    turno = svc._build_lucas_turno(settings, dispatcher)
    assert isinstance(turno, LucasTurno)
    for seam in (turno.dmn, turno.cibseven, turno.inference):
        assert is_gated_seam(seam)
        assert seam.seam_context.principal == "lucas"  # type: ignore[attr-defined]
    assert turno.seam_context.principal == "lucas"
    # o MESMO provedor cru da Helena, re-embrulhado (nenhum segundo InferenceProvider)
    assert turno.inference.inner is dispatcher.inference.inner  # type: ignore[attr-defined]
    # o MESMO guard de dedup: chaves de saida do mesmo pseudonimizador e do mesmo store
    assert turno.dedup is dispatcher.dedup
    assert isinstance(turno.fonte, FonteCobrancaSimulada)
    # o seam `whatsapp` de longa duracao do Lucas (envia para o hash literal) nao e' retido
    assert not hasattr(turno, "whatsapp")


async def test_bring_up_ligado_pendura_o_turno_no_estado(_checkpointer_ok: None) -> None:
    state = svc.WebhookState(settings=_settings(roteador_lucas_enabled=True, runtime_mode="kubernetes"))
    await svc._bring_up_dependencies(state)
    assert state.dispatcher is not None, state.dispatcher_error
    assert state.lucas_turno is not None
    # onda (e): o despachante so' ganha o Lucas DEPOIS da assercao das costuras dele.
    assert state.dispatcher.lucas_turno is state.lucas_turno


async def test_bring_up_desligado_nao_pendura_turno(_checkpointer_ok: None) -> None:
    state = svc.WebhookState(settings=_settings(runtime_mode="kubernetes"))
    await svc._bring_up_dependencies(state)
    assert state.dispatcher is not None, state.dispatcher_error
    assert state.lucas_turno is None
    assert state.dispatcher.lucas_turno is None


async def test_bring_up_ligado_com_lucas_fora_da_zona_recusa_servir(
    monkeypatch: pytest.MonkeyPatch, _checkpointer_ok: None
) -> None:
    from maezo.platform.webhooks.whatsapp import lucas_turno

    def _recusa(agent_id: str) -> None:
        raise lucas_turno.ZonaDeSegurancaError(f"{agent_id}: zona phi")

    monkeypatch.setattr(lucas_turno, "exigir_zona_geral", _recusa)
    state = svc.WebhookState(settings=_settings(roteador_lucas_enabled=True, runtime_mode="kubernetes"))
    await svc._bring_up_dependencies(state)
    assert state.dispatcher is None
    assert state.lucas_turno is None
    assert "ZonaDeSegurancaError" in (state.dispatcher_error or "")


# --- Fonte AMH do Lucas (decisao do dono 06/10/2026) ---------------------------------------------


def _amh(tmp_path: object, **sobre: object) -> dict[str, object]:
    from pathlib import Path

    base = Path(str(tmp_path))
    (base / "billing.yaml").write_bytes(b"x")
    (base / "subjects.yaml").write_bytes(b"y")
    valores: dict[str, object] = {
        "roteador_lucas_enabled": True,
        "lucas_fonte_cobranca": "amh",
        "amh_interop_base_url": "http://alb-interno.amh",
        "amh_interop_token_url": "https://cognito.example/oauth2/token",
        "amh_interop_client_id": "maezo-operadora-interop",
        "amh_interop_client_secret": "segredo-SINTETICO",
        "amh_phone_lookup_key": "chave-dedicada-SINTETICA",
        "amh_billing_status_openapi_path": str(base / "billing.yaml"),
        "amh_subject_resolution_openapi_path": str(base / "subjects.yaml"),
    }
    valores.update(sobre)
    return valores


def test_simulada_nao_calcula_hash_do_telefone_para_a_amh() -> None:
    settings = _settings(roteador_lucas_enabled=True)
    dispatcher, _ = svc._build_dispatcher(settings)
    turno = svc._build_lucas_turno(settings, dispatcher)
    assert turno is not None
    assert turno.hash_telefone_amh is None
    assert turno.hash_telefone_para_amh("5511987654321") is None


async def test_bring_up_amh_sem_configuracao_recusa_servir(_checkpointer_ok: None) -> None:
    state = svc.WebhookState(
        settings=_settings(roteador_lucas_enabled=True, lucas_fonte_cobranca="amh", runtime_mode="kubernetes")
    )
    await svc._bring_up_dependencies(state)
    assert state.dispatcher is None
    assert state.lucas_turno is None
    erro = state.dispatcher_error or ""
    assert "amh_interop_config_ausente" in erro
    assert "amh_interop_base_url" in erro and "amh_interop_client_secret" in erro


async def test_bring_up_amh_sem_contrato_pinado_recusa_servir(
    tmp_path: object, _checkpointer_ok: None
) -> None:
    """Hoje os contratos nao estao publicados nem pinados (XRG-2/XRG-3): ligar `amh` recusa o boot."""
    state = svc.WebhookState(settings=_settings(runtime_mode="kubernetes", **_amh(tmp_path)))
    await svc._bring_up_dependencies(state)
    assert state.dispatcher is None
    assert "BillingStatusContractError" in (state.dispatcher_error or "")
    assert "segredo-SINTETICO" not in (state.dispatcher_error or "")


async def test_amh_com_contratos_pinados_compoe_a_fonte_real_e_drena(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Com pin (sintetico, como nos testes dos adaptadores), o Lucas ganha a `FonteCobrancaAmh`, o
    hasher do telefone e a drenagem dos executores — nenhuma conexao e' aberta na construcao."""
    import hashlib
    from dataclasses import replace
    from pathlib import Path

    import yaml

    from maezo.adapters.amh import billing_status as bs
    from maezo.adapters.amh import subject_resolution as sr
    from maezo.adapters.amh.contract import load_contract_pin
    from maezo.agents.lucas.fonte_cobranca_amh import FonteCobrancaAmh
    from maezo.agents.lucas.identidade_amh import BaseLegalExecucaoDeContrato
    from maezo.gateway.amh_interop import AmhPhoneLookupHasher
    from tests.unit.adapters.amh.test_subject_resolution import _contrato as contrato_sr

    billing_raw = yaml.safe_dump(
        {
            "openapi": "3.0.3",
            "servers": [{"url": "/interop/billing-status/v1"}],
            "paths": {
                "/subjects/{portable_subject_ref}/billing/status": {
                    "get": {
                        "parameters": [],
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {"$ref": "#/components/schemas/BillingStatus"}
                                    }
                                }
                            }
                        },
                    }
                }
            },
            "components": {"schemas": {"BillingStatus": {"type": "object"}}},
        }
    ).encode()
    sr_raw = contrato_sr()
    base = Path(str(tmp_path))
    (base / "b.yaml").write_bytes(billing_raw)
    (base / "s.yaml").write_bytes(sr_raw)
    real_pin = load_contract_pin()
    monkeypatch.setattr(
        bs,
        "load_contract_pin",
        lambda path=None: replace(
            real_pin, artifact_digests={bs.ARTIFACT: hashlib.sha256(billing_raw).hexdigest()}
        ),
    )
    monkeypatch.setattr(
        sr,
        "load_contract_pin",
        lambda path=None: replace(
            real_pin, artifact_digests={sr.ARTIFACT: hashlib.sha256(sr_raw).hexdigest()}
        ),
    )
    settings = _settings(
        **_amh(
            tmp_path,
            amh_billing_status_openapi_path=str(base / "b.yaml"),
            amh_subject_resolution_openapi_path=str(base / "s.yaml"),
        )
    )
    dispatcher, _ = svc._build_dispatcher(settings)
    turno = svc._build_lucas_turno(settings, dispatcher)
    assert turno is not None
    assert isinstance(turno.fonte, FonteCobrancaAmh)
    assert isinstance(turno.fonte._consentimento, BaseLegalExecucaoDeContrato)
    assert isinstance(turno.hash_telefone_amh, AmhPhoneLookupHasher)
    assert turno.hash_telefone_amh.amh_tenant == "omni"
    assert len(turno.hash_telefone_para_amh("5511987654321") or "") == 64
    assert turno.hash_telefone_para_amh("14155550100") is None
    assert "segredo-SINTETICO" not in repr(turno) and "chave-dedicada-SINTETICA" not in repr(turno)
    assert turno.fonte_aclose is not None
    await turno.aclose()
    # drenado: a fonte recusa sem rede (o executor fechado devolve recusa fechada)
    resultado = await turno.fonte.fatos(
        "pseudo", None, phone_hash=turno.hash_telefone_para_amh("5511987654321")
    )
    assert not hasattr(resultado, "status_conciliado")
