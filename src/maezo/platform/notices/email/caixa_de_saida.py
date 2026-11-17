"""Caixa de saida: o registro guarda so' referencia opaca e digest; nunca endereco nem corpo."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal

from maezo.platform.notices.email.admissao import QualificacaoDoCanal, admitir_canal
from maezo.platform.notices.email.composicao import compor_email_de_protocolo
from maezo.platform.notices.email.porta import MensagemDeEmail, TransporteDeEmail

EstadoDeSaida = Literal["pendente", "aceito_pelo_provedor", "entregue", "falhou"]


@dataclass(frozen=True, slots=True)
class RegistroDeSaida:
    message_id: str
    destinatario_ref: str
    digest_do_corpo: str
    estado: EstadoDeSaida


@dataclass(frozen=True, slots=True)
class ParametroSemBase:
    valor: int | None
    marca: str


PRAZO_DE_GUARDA_DA_PROVA: Final = ParametroSemBase(valor=None, marca="proposto, sem base")


async def enviar_protocolo_por_email(
    *,
    qualificacao: QualificacaoDoCanal,
    transporte: TransporteDeEmail,
    destinatario_ref: str,
    protocolo_ref: str,
    etapas: Sequence[str],
    message_id: str,
) -> RegistroDeSaida:
    admitir_canal(qualificacao)  # PRIMEIRO: recusa antes de compor ou chamar o transporte
    assunto, corpo = compor_email_de_protocolo(protocolo_ref=protocolo_ref, etapas=etapas)
    await transporte.enviar(
        MensagemDeEmail(
            message_id=message_id, destinatario_ref=destinatario_ref, assunto=assunto, corpo=corpo
        )
    )
    # Aceite do provedor NAO e' entrega.
    return RegistroDeSaida(
        message_id=message_id,
        destinatario_ref=destinatario_ref,
        digest_do_corpo=hashlib.sha256(corpo.encode("utf-8")).hexdigest(),
        estado="aceito_pelo_provedor",
    )
