"""O reembolso no despachante (DL-0156/DL-0157, `MAEZO_REEMBOLSO_V1`).

Prova, sem modelo e sem motor real:
  * a extracao do envelope da Meta guarda o id e o mime da midia SO' de `image`/`document` e o id nunca vai
    ao `repr`;
  * sem a chave (`reembolso=None`) um arquivo recebe o aviso de sempre (`NON_TEXT_ACK_TEXT`), byte a byte;
  * com a chave e uma coleta esperando arquivo, o arquivo e' o pedido: vai a custodia e a coleta responde;
  * com a chave e SEM coleta ativa, o aviso de sempre (nada e' baixado nem guardado);
  * o turno de texto roda DEPOIS do acesso: emergencia vence, assunto alheio segue o fluxo de sempre.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from maezo.domain.reembolso import pedido as rp
from maezo.gateway.effect_pep import DecisionContext
from maezo.gateway.pseudonymizer import Pseudonymizer
from maezo.gateway.reembolso_anexos import AnexoGuardado
from maezo.gateway.seams import SeamContext
from maezo.platform.webhooks.whatsapp.dispatch import (
    NON_TEXT_ACK_TEXT,
    HelenaDispatcher,
    InboundMessage,
    InboundNonTextMessage,
    extract_inbound_messages,
)
from maezo.platform.webhooks.whatsapp.reembolso_store import MemoriaColetaReembolsoStore
from maezo.platform.webhooks.whatsapp.reembolso_turno import ReembolsoTurno
from maezo.platform.webhooks.whatsapp.security import hash_phone
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.mcp_whatsapp.media import MidiaBaixada
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

NUMERO = "5511999999999"
MEDIA_ID = "1234567890"


class _Cliente:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_message(self, to: str, text: str, **_kwargs: Any) -> dict[str, Any]:
        self.sent.append((to, text))
        return {"messages": [{"id": "out-1"}]}


class _SemInferencia:
    async def generate(self, prompt: str, **_kwargs: Any) -> str:  # pragma: no cover
        raise AssertionError("o reembolso nunca chama modelo")


class _Custodia:
    def __init__(self) -> None:
        self.guardados = 0

    async def guardar_anexo(self, *, conversation_id: str, mime: str, conteudo: bytes) -> AnexoGuardado:
        self.guardados += 1
        return AnexoGuardado(f"doc-{self.guardados}", "a" * 64)


class _Baixador:
    def __init__(self) -> None:
        self.baixados: list[str] = []

    async def baixar(self, media_id: str) -> MidiaBaixada:
        self.baixados.append(media_id)
        return MidiaBaixada(conteudo=b"%PDF teste", mime="application/pdf")


def _envelope(mensagem: dict[str, Any]) -> dict[str, Any]:
    return {
        "entry": [{"changes": [{"value": {"messages": [{"from": NUMERO, "id": "wamid.1", **mensagem}]}}]}]
    }


def _conversa() -> str:
    return f"wa:amh:{hash_phone(NUMERO, 'amh', Pseudonymizer())}"


def _montar(
    *, com_reembolso: bool
) -> tuple[HelenaDispatcher, _Cliente, ReembolsoTurno | None, _Custodia, _Baixador]:
    cliente, custodia, baixador = _Cliente(), _Custodia(), _Baixador()
    turno = None
    if com_reembolso:
        turno = ReembolsoTurno(
            tenant_id="amh",
            store=MemoriaColetaReembolsoStore(),
            cibseven=FakeCibSevenTransport(),
            audit_sink=FakeStartAuditSink(),
            custodia=custodia,
            baixador=baixador,
        )
    dispatcher = HelenaDispatcher(
        tenant_id="amh",
        inference=_SemInferencia(),  # type: ignore[arg-type]
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        whatsapp_client=cliente,  # type: ignore[arg-type]
        pseudonymizer=Pseudonymizer(),
        audit_sink=FakeStartAuditSink(),
        seam_context=SeamContext(tenant="amh", principal="helena", decision=DecisionContext()),
        reembolso=turno,
    )
    return dispatcher, cliente, turno, custodia, baixador


def _arquivo(tipo: str = "document") -> InboundNonTextMessage:
    return InboundNonTextMessage(
        from_number=NUMERO,
        message_type=tipo,
        message_id="wamid.2",
        media_id=MEDIA_ID,
        mime_type="application/pdf",
    )


async def _ate_a_etapa_de_documentos(turno: ReembolsoTurno) -> None:
    for texto in ("quero um reembolso", "sim", "titular", "1", "1", "25/10/2026", "150,00"):
        assert await turno.tratar_texto(
            conversation_id=_conversa(), texto=texto, beneficiario_pseudo_id="b", sinal_de_emergencia=False
        )


# --- o envelope da Meta -------------------------------------------------------------------------------


@pytest.mark.parametrize("tipo", ["image", "document"])
def test_extracao_guarda_o_id_e_o_mime_so_de_imagem_e_documento(tipo: str) -> None:
    (evento,) = extract_inbound_messages(
        _envelope({"type": tipo, tipo: {"id": MEDIA_ID, "mime_type": "image/jpeg", "caption": "minha nota"}})
    )
    assert isinstance(evento, InboundNonTextMessage)
    assert (evento.media_id, evento.mime_type) == (MEDIA_ID, "image/jpeg")
    assert MEDIA_ID not in repr(evento) and "minha nota" not in repr(evento)


@pytest.mark.parametrize("tipo", ["audio", "video", "sticker", "location"])
def test_extracao_nao_guarda_midia_de_outros_tipos(tipo: str) -> None:
    (evento,) = extract_inbound_messages(_envelope({"type": tipo, tipo: {"id": MEDIA_ID}}))
    assert isinstance(evento, InboundNonTextMessage) and evento.media_id == "" and evento.mime_type == ""


def test_extracao_de_texto_continua_igual() -> None:
    (evento,) = extract_inbound_messages(_envelope({"type": "text", "text": {"body": "oi"}}))
    assert isinstance(evento, InboundMessage) and evento.text == "oi"


# --- o arquivo ----------------------------------------------------------------------------------------


async def test_sem_a_chave_o_arquivo_recebe_o_aviso_de_sempre() -> None:
    dispatcher, cliente, _, custodia, baixador = _montar(com_reembolso=False)
    await dispatcher.acknowledge_non_text(_arquivo())
    assert cliente.sent == [(NUMERO, NON_TEXT_ACK_TEXT)]
    assert custodia.guardados == 0 and baixador.baixados == []


async def test_com_a_chave_e_coleta_esperando_arquivo_o_arquivo_e_o_pedido() -> None:
    dispatcher, cliente, turno, custodia, baixador = _montar(com_reembolso=True)
    assert turno is not None
    await _ate_a_etapa_de_documentos(turno)
    await dispatcher.acknowledge_non_text(_arquivo())
    assert baixador.baixados == [MEDIA_ID] and custodia.guardados == 1
    (enviado,) = cliente.sent
    assert enviado[0] == NUMERO and "terminou de enviar" in enviado[1] and enviado[1] != NON_TEXT_ACK_TEXT


async def test_com_a_chave_e_sem_coleta_ativa_o_aviso_de_sempre_e_nada_e_baixado() -> None:
    dispatcher, cliente, _, custodia, baixador = _montar(com_reembolso=True)
    await dispatcher.acknowledge_non_text(_arquivo("image"))
    assert cliente.sent == [(NUMERO, NON_TEXT_ACK_TEXT)]
    assert custodia.guardados == 0 and baixador.baixados == []


async def test_audio_com_a_chave_ligada_continua_com_o_aviso_de_sempre() -> None:
    dispatcher, cliente, turno, custodia, baixador = _montar(com_reembolso=True)
    assert turno is not None
    await _ate_a_etapa_de_documentos(turno)
    await dispatcher.acknowledge_non_text(
        InboundNonTextMessage(from_number=NUMERO, message_type="audio", message_id="wamid.3")
    )
    assert cliente.sent == [(NUMERO, NON_TEXT_ACK_TEXT)] and baixador.baixados == []


# --- o texto ------------------------------------------------------------------------------------------


def _acesso(*, emergencia: bool) -> Any:
    return SimpleNamespace(sinal_de_emergencia=lambda texto: emergencia)


async def _responder(dispatcher: HelenaDispatcher, texto: str, cliente: _Cliente) -> dict[str, Any] | None:
    sender = SimpleNamespace(send=lambda to_hash, text: _enviar(cliente, text))
    return await dispatcher._responder_reembolso(
        InboundMessage(from_number=NUMERO, text=texto, message_id="wamid.9"),
        phone_hash="hk1_x",
        conversation_id=_conversa(),
        beneficiario_pseudo_id="benef-1",
        sender=sender,  # type: ignore[arg-type]
    )


async def _enviar(cliente: _Cliente, texto: str) -> dict[str, Any]:
    cliente.sent.append((NUMERO, texto))
    return {}


async def test_texto_de_reembolso_inicia_a_coleta_sem_chamar_o_agente() -> None:
    dispatcher, cliente, *_ = _montar(com_reembolso=True)
    dispatcher.acesso = _acesso(emergencia=False)
    resposta = await _responder(dispatcher, "quero um reembolso", cliente)
    assert resposta is not None and resposta["reembolso_evento"] == "iniciado"
    assert cliente.sent == [(NUMERO, rp.TEXTO_INICIO)]


async def test_assunto_alheio_segue_o_fluxo_de_sempre() -> None:
    dispatcher, cliente, *_ = _montar(com_reembolso=True)
    dispatcher.acesso = _acesso(emergencia=False)
    assert await _responder(dispatcher, "qual o valor da minha mensalidade?", cliente) is None
    assert cliente.sent == []


async def test_emergencia_vence_o_reembolso() -> None:
    dispatcher, cliente, *_ = _montar(com_reembolso=True)
    dispatcher.acesso = _acesso(emergencia=True)
    assert await _responder(dispatcher, "quero um reembolso, estou com dor no peito", cliente) is None
    assert cliente.sent == []


async def test_sem_a_chave_o_texto_de_reembolso_nao_e_tratado() -> None:
    dispatcher, cliente, *_ = _montar(com_reembolso=False)
    dispatcher.acesso = _acesso(emergencia=False)
    assert await _responder(dispatcher, "quero um reembolso", cliente) is None
    assert cliente.sent == []
