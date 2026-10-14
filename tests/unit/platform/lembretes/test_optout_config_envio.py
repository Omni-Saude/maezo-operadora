"""PARAR (opt-out), configuracao segura por padrao, template do cliente WhatsApp e a migration 0027."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import maezo
from maezo.platform.lembretes.composicao import ComposicaoDoLembreteError, configurar
from maezo.platform.lembretes.config import LembreteSettings, conversas_da_lista, telefones_da_lista
from maezo.platform.lembretes.optout import e_pedido_de_parar, tratar_parar
from tests.support.lembrete_fakes import AGORA, CONV_A, CONV_B, FakeStore


# ----------------------------------------------------------------------------- PARAR
@pytest.mark.parametrize("texto", ["PARAR", "parar", "  Parar. ", "parar lembretes", "PARAR!", "Pârar"])
def test_reconhece_parar(texto: str) -> None:
    assert e_pedido_de_parar(texto) is True


@pytest.mark.parametrize(
    "texto",
    [
        "",
        None,
        "nao quero parar de pagar",
        "quero parar o plano",
        "pagar",
        "REVOGAR",
        "ACEITO",
        "parar de ligar",
    ],
)
def test_nao_e_parar(texto: str | None) -> None:
    assert e_pedido_de_parar(texto) is False


async def test_tratar_parar_grava_e_confirma() -> None:
    store = FakeStore()
    resposta = await tratar_parar("PARAR", conversation_id=CONV_A, store=store, agora=AGORA)
    assert resposta == "Você não receberá mais lembretes."
    assert await store.optouts() == frozenset({CONV_A})


async def test_tratar_parar_ignora_o_resto_e_nao_grava() -> None:
    store = FakeStore()
    assert await tratar_parar("qual o valor?", conversation_id=CONV_A, store=store, agora=AGORA) is None
    assert await store.optouts() == frozenset()


# ----------------------------------------------------------------------------- configuracao
def _limpar(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    for k in list(os.environ):
        if k.startswith("MAEZO_LEMBRETE_"):
            monkeypatch.delenv(k)


def test_padroes_sao_desligado_dry_run_e_exigem_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    _limpar(monkeypatch)
    s = LembreteSettings()
    assert (s.habilitado, s.dry_run, s.dias_antecedencia, s.exigir_allowlist) == (False, True, 3, True)
    assert s.template_nome == "" and s.allowlist_conversas is None
    cfg = configurar(s)
    assert cfg.habilitado is False and cfg.dry_run is True and cfg.allowlist is None


def test_variaveis_de_ambiente(monkeypatch: pytest.MonkeyPatch) -> None:
    _limpar(monkeypatch)
    monkeypatch.setenv("MAEZO_LEMBRETE_COBRANCA", "true")
    monkeypatch.setenv("MAEZO_LEMBRETE_DRY_RUN", "false")
    monkeypatch.setenv("MAEZO_LEMBRETE_DIAS_ANTECEDENCIA", "5")
    monkeypatch.setenv("MAEZO_LEMBRETE_TEMPLATE_NAME", "lembrete_mensalidade_v1")
    monkeypatch.setenv("MAEZO_LEMBRETE_ALLOWLIST_CONVERSAS", f"{CONV_A}, {CONV_B}")
    cfg = configurar(LembreteSettings())
    assert cfg.habilitado and not cfg.dry_run and cfg.dias_antecedencia == 5
    assert cfg.template_nome == "lembrete_mensalidade_v1"
    assert cfg.allowlist is not None and cfg.allowlist.conversas == frozenset({CONV_A, CONV_B})


def test_allowlist_com_erro_de_digitacao_so_reduz_nunca_amplia() -> None:
    assert conversas_da_lista(None) is None
    assert conversas_da_lista("  ") is None
    assert (
        conversas_da_lista("lixo, *, wa:amh:") == frozenset()
    )  # definida, mas ninguem valido: ninguem recebe
    assert conversas_da_lista(f"lixo,{CONV_A}") == frozenset({CONV_A})


def test_allowlist_de_telefones_vira_conversas_e_exige_pseudonimizador(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _limpar(monkeypatch)
    monkeypatch.setenv("MAEZO_LEMBRETE_ALLOWLIST_TELEFONES", "+55 (11) 97777-0001, 5511977770002")
    assert telefones_da_lista("+55 (11) 97777-0001, 5511977770002") == ["5511977770001", "5511977770002"]
    s = LembreteSettings()
    with pytest.raises(ComposicaoDoLembreteError):
        configurar(s)
    cfg = configurar(s, hash_do_telefone=lambda t: "hk1_" + t[-4:])
    assert cfg.allowlist is not None
    assert cfg.allowlist.conversas == frozenset({"wa:amh:hk1_0001", "wa:amh:hk1_0002"})
    assert "5511" not in repr(cfg.allowlist) and "5511" not in repr(s)


def test_dias_fora_de_1_a_15_e_recusado(monkeypatch: pytest.MonkeyPatch) -> None:
    _limpar(monkeypatch)
    monkeypatch.setenv("MAEZO_LEMBRETE_DIAS_ANTECEDENCIA", "30")
    with pytest.raises(ValueError):
        LembreteSettings()


# ----------------------------------------------------------------------------- send_template (cliente real)
def _httpx_mock() -> Any:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"messages": [{"id": "wamid.t"}]})
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.post = AsyncMock(return_value=resp)
    return client


async def test_send_template_monta_o_payload_da_meta() -> None:
    from maezo.tools.mcp_whatsapp.server import WhatsAppServer, WhatsAppSettings

    server = WhatsAppServer(
        settings=WhatsAppSettings(whatsapp_token="t", whatsapp_verify_token="v", phone_number_id="123")
    )
    client = _httpx_mock()
    with patch("httpx.AsyncClient", return_value=client):
        await server.send_template(
            "5511999999999", "lembrete_mensalidade_v1", ["outubro de 2026", "320,25", "16/10"]
        )
    payload = client.post.call_args[1]["json"]
    assert payload == {
        "messaging_product": "whatsapp",
        "to": "5511999999999",
        "type": "template",
        "template": {
            "name": "lembrete_mensalidade_v1",
            "language": {"code": "pt_BR"},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": "outubro de 2026"},
                        {"type": "text", "text": "320,25"},
                        {"type": "text", "text": "16/10"},
                    ],
                }
            ],
        },
    }


async def test_send_template_recusa_nome_vazio_e_faixa_sintetica() -> None:
    from maezo.tools.mcp_whatsapp.server import WhatsAppServer, WhatsAppSettings

    server = WhatsAppServer(
        settings=WhatsAppSettings(whatsapp_token="t", whatsapp_verify_token="v", phone_number_id="123")
    )
    with pytest.raises(ValueError, match="template name"):
        await server.send_template("5511999999999", " ", ["a"])
    client = _httpx_mock()
    with patch("httpx.AsyncClient", return_value=client):
        ack = await server.send_template("5511900000123", "t", ["a"])
    assert ack == {"suppressed_synthetic": True}
    client.post.assert_not_awaited()


# ----------------------------------------------------------------------------- migration 0027
_VERSIONS = Path(maezo.__file__).parent / "platform" / "migrations" / "versions"
_SRC = (_VERSIONS / "0027_lembrete_cobranca.py").read_text(encoding="utf-8")
_CODE = _SRC.split('"""', 2)[2]


def test_migration_encadeia_em_0025_e_nao_tem_dado_pessoal() -> None:
    assert re.search(r'^revision: str = "0027"$', _SRC, re.MULTILINE)
    assert re.search(r'^down_revision: str \| None = "0026"$', _SRC, re.MULTILINE)
    for proibido in ("telefone", "phone", "cpf", "nome", "valor", "texto text", "mensagem"):
        assert not re.search(rf"^\s{{12}}{proibido}\b", _CODE, re.MULTILINE)
    assert "PRIMARY KEY (tenant, conversation_id, competencia)" in _CODE
    assert "PRIMARY KEY (tenant, conversation_id)" in _CODE
    assert "RAISE EXCEPTION" in _CODE  # o downgrade recusa apagar PARAR
