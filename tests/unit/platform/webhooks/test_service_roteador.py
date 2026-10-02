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
