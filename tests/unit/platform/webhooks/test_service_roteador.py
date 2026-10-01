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
    assert roteador._lucas_disponivel is False  # onda (c): sombra
    assert roteador.versao_lexicos == "pre-roteamento-v1"


def test_ligado_com_lexico_invalido_recusa_no_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    def _quebrado() -> pre_roteamento.PreRoteamento:
        raise pre_roteamento.LexicoInvalidoError("pre-roteamento: teste")

    monkeypatch.setattr(pre_roteamento, "carregar", _quebrado)
    with pytest.raises(pre_roteamento.LexicoInvalidoError):
        svc._build_dispatcher(_settings(roteador_lucas_enabled=True))
    # desligado, o lexico nem e' lido
    assert svc._build_dispatcher(_settings())[0].roteador is None
