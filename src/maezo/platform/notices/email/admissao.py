"""Admissao do canal de e-mail. Nesta variante nao existe caminho de admissao."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from maezo.platform.notices.email.erros import CanalDeEmailRecusado


@dataclass(frozen=True, slots=True)
class QualificacaoDoCanal:
    provedor_ref: str | None
    processamento_no_brasil: bool
    ato_do_dono_ref: str | None
    # Qualificar e' ato do dono, em PR proprio: nesta variante o literal so' admite NAO_QUALIFICADO.
    estado: Literal["NAO_QUALIFICADO"]


QUALIFICACAO_DO_REPOSITORIO: Final = QualificacaoDoCanal(
    provedor_ref=None,
    processamento_no_brasil=False,
    ato_do_dono_ref=None,
    estado="NAO_QUALIFICADO",
)


def admitir_canal(q: QualificacaoDoCanal) -> None:
    """Recusa SEMPRE nesta variante (estado NAO_QUALIFICADO); tambem recusa se faltar qualquer requisito."""
    if q.estado == "NAO_QUALIFICADO":
        raise CanalDeEmailRecusado("protocol_email_channel_unqualified")
    if q.provedor_ref is None or q.ato_do_dono_ref is None or not q.processamento_no_brasil:
        raise CanalDeEmailRecusado("protocol_email_channel_unqualified")
    raise CanalDeEmailRecusado("protocol_email_channel_unqualified")
