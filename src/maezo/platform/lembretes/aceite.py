"""E-SF-21r (E37): o criterio de selecao do lembrete e' o ACEITE do termo de contato, finalidade 2.

Base decidida para o lembrete de cobranca: consentimento do termo de contato (aceite unico), finalidade 2
(cobranca). O consentimento de ATENDIMENTO (`atendimento_whatsapp` / `wa-consent-v2`, "nesta conversa")
deixa de ser o criterio: ele so' identifica a conversa verificada, nunca permite contato proativo.

O que este modulo faz:
  * declara o aceite como fato fechado (`AceiteDeContato`): prova, versao e hash do texto, finalidades,
    canais e se esta' revogado;
  * declara a FONTE do aceite como porta (`LeitorDeAceiteDeContato`). Nenhuma implementacao existe em
    `src/`: o termo de contato e' minuta (validade com o juridico e o encarregado) e o registro de
    aceites nao esta' publicado. Sem fonte, ninguem e' selecionado;
  * traduz um aceite valido para a permissao que o `OutboundGate` qualifica (`permissao_do_aceite`).
    Aceite ausente, revogado, sem a finalidade 2 ou sem o canal WhatsApp = sem permissao.

Revogar o aceite (ou a finalidade 2) tira a pessoa da selecao na rodada seguinte, sem prazo: efeito
imediato. O PARAR do lembrete (`optout.py`) continua valendo por cima.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Protocol

from maezo.gateway.communications.outbound_gate import (
    ESCOPO_CONTATO_PROATIVO,
    ORIGEM_ACEITE_CONTATO,
    PermissaoDeContato,
)

if TYPE_CHECKING:
    from .job import Beneficiario

__all__ = [
    "CANAL_WHATSAPP",
    "FINALIDADE_COBRANCA",
    "AceiteDeContato",
    "LeitorDeAceiteDeContato",
    "permissao_do_aceite",
]

#: A finalidade 2 da lista fechada de finalidades do aceite unico: cobranca.
FINALIDADE_COBRANCA: Final[int] = 2
CANAL_WHATSAPP: Final[str] = "whatsapp"


@dataclass(frozen=True, slots=True)
class AceiteDeContato:
    """O aceite do termo de contato de UMA pessoa, como a fonte o registra. So' referencias."""

    prova_ref: str = field(repr=False)
    texto_versao: str
    texto_sha256: str
    finalidades: frozenset[int]
    canais: frozenset[str]
    revogado: bool = False


class LeitorDeAceiteDeContato(Protocol):
    """PORTA: o aceite vigente do termo de contato do beneficiario, ou `None` se nao ha'.

    Levanta quando a fonte nao responde; o job conta e nao seleciona (desconhecido nunca vira aceite)."""

    async def de(self, beneficiario: Beneficiario) -> AceiteDeContato | None: ...


def permissao_do_aceite(aceite: AceiteDeContato | None) -> PermissaoDeContato | None:
    """A permissao de contato proativo para a finalidade 2, ou `None` quando o aceite nao a cobre."""
    if (
        aceite is None
        or aceite.revogado
        or FINALIDADE_COBRANCA not in aceite.finalidades
        or CANAL_WHATSAPP not in aceite.canais
        or not aceite.prova_ref
    ):
        return None
    return PermissaoDeContato(
        origem=ORIGEM_ACEITE_CONTATO,
        escopo=ESCOPO_CONTATO_PROATIVO,
        texto_versao=aceite.texto_versao,
        texto_sha256=aceite.texto_sha256,
        canal=CANAL_WHATSAPP,
        prova_ref=aceite.prova_ref,
    )
