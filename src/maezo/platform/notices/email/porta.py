"""Porta de transporte de e-mail. Aceite do provedor NAO e' entrega."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from maezo.platform.notices.email.erros import CanalDeEmailRecusado


@dataclass(frozen=True, slots=True)
class MensagemDeEmail:
    """`destinatario_ref` e' referencia opaca de custodia, nunca o endereco."""

    message_id: str
    destinatario_ref: str
    assunto: str
    corpo: str


@dataclass(frozen=True, slots=True)
class AceiteDoProvedor:
    """O provedor aceitou a mensagem. Isto nao prova entrega."""

    message_id: str
    provedor_ref: str


class TransporteDeEmail(Protocol):
    async def enviar(self, mensagem: MensagemDeEmail) -> AceiteDoProvedor: ...


class TransporteEscuro:
    """Transporte desligado: sempre recusa, sem tocar nenhum recurso externo."""

    async def enviar(self, mensagem: MensagemDeEmail) -> AceiteDoProvedor:
        raise CanalDeEmailRecusado("channel_disabled")
