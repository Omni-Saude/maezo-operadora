"""Ingress de eventos do provedor (sem rota). Sem verificador real: ESP nao escolhido (GAP)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace

from maezo.platform.notices.email.caixa_de_saida import RegistroDeSaida
from maezo.platform.notices.email.erros import EventoRecusado


def aplicar_evento_do_provedor(
    registro: RegistroDeSaida,
    evento: Mapping[str, object],
    *,
    verificador: Callable[[Mapping[str, object]], bool],
) -> RegistroDeSaida:
    try:
        assinado = verificador(evento) is True
    except Exception:
        assinado = False
    if not assinado:
        return registro  # sem assinatura valida: so' pista
    if evento.get("message_id") != registro.message_id:
        raise EventoRecusado("delivery_event_for_foreign_message")
    tipo = evento.get("type")
    if tipo == "delivered":
        return replace(registro, estado="entregue")
    if tipo in ("bounced", "failed"):
        return replace(registro, estado="falhou")
    return registro
