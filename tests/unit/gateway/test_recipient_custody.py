"""ADR-0061 — custodia cifrada do telefone: envelope, amarracao ao contexto, separacao de papeis,
janela da Meta e o telefone em claro NUNCA num log."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import structlog

from maezo.gateway.pseudonymizer import Pseudonymizer
from maezo.gateway.recipient_custody import (
    DEFAULT_TTL_DAYS,
    AwsKmsKeyWrapper,
    PostgresRecipientVault,
    RecipientCustodyError,
    RecipientRecord,
    dentro_da_janela,
    encryption_context,
    open_phone,
    seal_phone,
)
from maezo.platform.webhooks.whatsapp.dispatch import HelenaDispatcher, InboundMessage, InboundNonTextMessage
from maezo.platform.webhooks.whatsapp.security import hash_phone
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.mcp_whatsapp.server import WhatsAppServer
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink
from tests.support.recipient_custody_fakes import FakeKms, InMemoryRecipientVault

_PHONE = "5511987654321"
_CONV = "wa:amh:hk1_" + "c" * 64


async def test_seal_open_roundtrip_e_ciphertext_nao_contem_o_numero() -> None:
    kms = FakeKms()
    sealed = await seal_phone(_PHONE, kms, tenant="amh", conversation_id=_CONV)
    assert _PHONE.encode() not in sealed.ciphertext + sealed.wrapped_key
    assert _PHONE not in repr(sealed)
    assert await open_phone(sealed, kms, tenant="amh", conversation_id=_CONV) == _PHONE
    assert kms.calls[0] == ("encrypt", encryption_context("amh", _CONV))


async def test_blob_copiado_para_outra_conversa_nao_abre() -> None:
    kms = FakeKms()
    sealed = await seal_phone(_PHONE, kms, tenant="amh", conversation_id=_CONV)
    with pytest.raises(RecipientCustodyError) as exc:
        await open_phone(sealed, kms, tenant="amh", conversation_id="wa:amh:hk1_" + "d" * 64)
    assert _PHONE not in str(exc.value)


async def test_separacao_de_papeis_receptor_so_cifra_retomada_so_decifra() -> None:
    kms = FakeKms()
    receptor = kms.with_permissions(allow_encrypt=True, allow_decrypt=False)
    retomada = kms.with_permissions(allow_encrypt=False, allow_decrypt=True)
    sealed = await seal_phone(_PHONE, receptor, tenant="amh", conversation_id=_CONV)
    with pytest.raises(RecipientCustodyError):
        await open_phone(sealed, receptor, tenant="amh", conversation_id=_CONV)
    with pytest.raises(PermissionError):
        await seal_phone(_PHONE, retomada, tenant="amh", conversation_id=_CONV)
    assert await open_phone(sealed, retomada, tenant="amh", conversation_id=_CONV) == _PHONE


async def test_aws_wrapper_chama_encrypt_e_decrypt_com_o_contexto() -> None:
    class _Client:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []

        def encrypt(self, **kw: Any) -> dict[str, Any]:
            self.calls.append(("encrypt", kw))
            return {"CiphertextBlob": b"W" + kw["Plaintext"]}

        def decrypt(self, **kw: Any) -> dict[str, Any]:
            self.calls.append(("decrypt", kw))
            return {"Plaintext": kw["CiphertextBlob"][1:]}

    client = _Client()
    wrapper = AwsKmsKeyWrapper(key_arn="arn:aws:kms:sa-east-1:1:key/x", client=client)
    sealed = await seal_phone(_PHONE, wrapper, tenant="amh", conversation_id=_CONV)
    assert await open_phone(sealed, wrapper, tenant="amh", conversation_id=_CONV) == _PHONE
    assert [c[0] for c in client.calls] == ["encrypt", "decrypt"]
    assert client.calls[0][1]["EncryptionContext"] == encryption_context("amh", _CONV)


def test_janela_da_meta_nos_dois_lados() -> None:
    agora = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
    assert dentro_da_janela(agora - timedelta(hours=23, minutes=59), agora=agora) is True
    assert dentro_da_janela(agora - timedelta(hours=24), agora=agora) is False
    assert dentro_da_janela(agora - timedelta(days=3), agora=agora) is False


def test_ttl_padrao_proposto_e_minimo() -> None:
    assert DEFAULT_TTL_DAYS == 30
    with pytest.raises(ValueError):
        PostgresRecipientVault(dsn="postgresql://x/y", tenant="amh", wrapper=FakeKms(), ttl_days=0)


def test_record_nao_imprime_o_numero() -> None:
    record = RecipientRecord(phone=_PHONE, last_inbound_at=datetime.now(UTC))
    assert _PHONE not in repr(record) and _PHONE not in str(record)


# --- o receptor grava a custodia, e o numero nunca vai para log ---------------------------------


class _Inference:
    async def generate(self, prompt: str, **_: Any) -> str:
        if "message_body" in prompt and "intent" in prompt:
            return '{"intent": "greeting", "population": "none", "psychosocial_risk": false}'
        return "Ola! Sou Helena."


class _WhatsApp(WhatsAppServer):
    async def send_message(self, to: str, text: str, *, idempotency_key: str | None = None) -> dict[str, Any]:
        return {"messages": [{"id": "wamid.out"}]}


def _dispatcher(vault: Any) -> HelenaDispatcher:
    return HelenaDispatcher(
        tenant_id="amh",
        inference=_Inference(),  # type: ignore[arg-type]
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        whatsapp_client=_WhatsApp(),
        pseudonymizer=Pseudonymizer(),
        audit_sink=FakeStartAuditSink(),
        recipient_vault=vault,
    )


async def test_receptor_grava_custodia_cifrada_em_texto_e_nao_texto() -> None:
    vault = InMemoryRecipientVault(tenant="amh", kms=FakeKms(allow_decrypt=False))
    dispatcher = _dispatcher(vault)
    await dispatcher.dispatch(InboundMessage(from_number=_PHONE, text="oi", message_id="wamid.1"))
    await dispatcher.acknowledge_non_text(
        InboundNonTextMessage(from_number=_PHONE, message_type="image", message_id="wamid.2")
    )
    conv = f"wa:amh:{hash_phone(_PHONE, 'amh', Pseudonymizer())}"
    assert list(vault.rows) == [conv]  # upsert: uma linha por conversa
    sealed, _ = vault.rows[conv]
    assert _PHONE.encode() not in sealed.ciphertext


async def test_telefone_em_claro_nunca_aparece_em_log(caplog: pytest.LogCaptureFixture) -> None:
    class _Broken:
        async def upsert(self, *, conversation_id: str, phone: str, received_at: datetime) -> None:
            raise RuntimeError(f"db down while writing {phone}")  # uma excecao que VAZA o numero

    caplog.set_level(logging.DEBUG)
    with structlog.testing.capture_logs() as logs:
        for vault in (InMemoryRecipientVault(tenant="amh", kms=FakeKms()), _Broken()):
            dispatcher = _dispatcher(vault)
            await dispatcher.dispatch(InboundMessage(from_number=_PHONE, text="oi", message_id="wamid.9"))
    assert logs, "a captura precisa ver os logs do turno"
    assert any(e["event"] == "whatsapp_recipient_custody_failed" for e in logs)
    assert _PHONE not in repr(logs)
    assert _PHONE not in caplog.text
