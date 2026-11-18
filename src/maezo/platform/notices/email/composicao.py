"""Composicao do corpo do e-mail de protocolo: texto fixo PROVISORIO (redacao com o juridico)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Final

from maezo.gateway.communications.whatsapp_templates import contem_dado_de_saude, valor_valido_para_classe
from maezo.platform.notices.email.erros import ComposicaoRecusada

ASSUNTO_FIXO: Final[str] = "Protocolo da sua contratação"
ETAPAS_EXIBIVEIS: Final[tuple[str, ...]] = ("proposta", "assinatura", "pagamento", "ativacao")
_ROTULOS: Final[dict[str, str]] = {
    "proposta": "proposta",
    "assinatura": "assinatura",
    "pagamento": "pagamento",
    "ativacao": "ativação",
}
_URL: Final = re.compile(r"https?://|www\.", re.IGNORECASE)


def compor_email_de_protocolo(*, protocolo_ref: str, etapas: Sequence[str]) -> tuple[str, str]:
    """Devolve (assunto, corpo). Valor fora da classe fechada recusa sem eco."""
    if not valor_valido_para_classe("protocolo_ref", protocolo_ref):
        raise ComposicaoRecusada("email_body_field_class_refused")
    if isinstance(etapas, str) or any(type(e) is not str or e not in ETAPAS_EXIBIVEIS for e in etapas):
        raise ComposicaoRecusada("email_body_field_class_refused")
    lista = ", ".join(_ROTULOS[e] for e in etapas)
    corpo = (
        f"Registramos sua solicitação de contratação. Protocolo: {protocolo_ref}. Próximas etapas: {lista}."
    )
    if contem_dado_de_saude(corpo):
        raise ComposicaoRecusada("PHI_IN_COMMERCIAL_INPUT")
    if _URL.search(corpo):
        raise ComposicaoRecusada("email_body_has_url")
    return ASSUNTO_FIXO, corpo
