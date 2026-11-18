"""PARAR: o opt-out do lembrete, e a funcao que o caminho de ENTRADA do WhatsApp chama.

O gancho (uma linha, a ser ligada pelo coordenador no despachante, ANTES de rotear para qualquer agente):

    resposta = await tratar_parar(message.text, conversation_id=conversation_id, store=lembrete_store)
    if resposta is not None:
        <enviar `resposta` pelo remetente do turno e encerrar o turno>

`tratar_parar` devolve o texto de confirmacao quando a mensagem e' um PARAR (e ja' gravou o opt-out), ou
`None` quando nao e' (o turno segue normal). Falha de banco PROPAGA: o turno falha e a Meta reentrega,
em vez de confirmar um "voce nao recebera mais" que nao foi gravado.

O reconhecimento e' ESTRITO (a mensagem inteira, sem acento nem pontuacao): "parar" e "parar lembretes".
"Nao quero parar de pagar" nao e' um PARAR. REVOGAR (consentimento) continua do fluxo de acesso e vale mais:
quem revogou nao entra mais na selecao do job.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from typing import Final

from .armazenamento import LembreteStore
from .texto import CONFIRMACAO_PARAR

_PEDIDOS: Final[frozenset[str]] = frozenset({"parar", "parar lembretes"})


def _normalizar(texto: str) -> str:
    sem_acento = "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))
    so_palavras = re.sub(r"[^a-z0-9 ]+", " ", sem_acento.casefold())
    return " ".join(so_palavras.split())


def e_pedido_de_parar(texto: str | None) -> bool:
    return texto is not None and _normalizar(texto) in _PEDIDOS


async def tratar_parar(
    texto: str | None,
    *,
    conversation_id: str,
    store: LembreteStore,
    agora: datetime | None = None,
) -> str | None:
    if not e_pedido_de_parar(texto):
        return None
    await store.registrar_optout(conversation_id, agora or datetime.now(UTC))
    return CONFIRMACAO_PARAR


class OposicaoPeloParar:
    """E-SF-21r: a oposicao EXECUTAVEL do beneficiario ao lembrete, pelo PARAR (efeito imediato).

    Implementa `job.PortaDeOposicaoBeneficiario`. A oposicao so' e' executavel quando as duas pontas
    existem: o gancho de ENTRADA (`tratar_parar` ligado no despachante, antes de qualquer agente) e o store
    de opt-out respondendo. `gancho_de_entrada_ligado` e' declarado por quem compoe o despachante; sem ele
    (o padrao), ou com o store fora do ar, a oposicao nao e' executavel e a rodada inteira e' recusada.
    Nenhuma composicao de `src/` instancia esta classe: ligar o gancho e' ato proprio.
    """

    def __init__(self, store: LembreteStore, *, gancho_de_entrada_ligado: bool = False) -> None:
        self._store = store
        self._gancho = gancho_de_entrada_ligado is True

    async def executavel(self) -> bool:
        if not self._gancho:
            return False
        await self._store.optouts()  # levanta se a fonte nao responde; o job conta como nao executavel
        return True
