"""Lembrete proativo de cobranca (job agendado). Ver `docs/runbooks/lembrete-cobranca.md`."""

from .optout import e_pedido_de_parar, tratar_parar

__all__ = ["e_pedido_de_parar", "tratar_parar"]
