"""Respostas FIXAS sem modelo de `_decidir_sem_modelo` (ADR-0067 §5: o grafo so' liga; a regra mora aqui).

- Avaliacao r5 (25/10/2026): o "sim"/"nao" a oferta da equipe (`atendimento_humano.oferta`).
- Avaliacao r5b (25/10/2026): o reembolso esta' fora do escopo deste canal (pausado) — texto fixo do pacote,
  nunca a confirmacao do modelo ("sim, e' possivel pedir reembolso").
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from maezo.agents.helena.habilidades.atendimento_humano import oferta as _oferta
from maezo.agents.helena.habilidades.conversa import reembolso as _reembolso

if TYPE_CHECKING:
    from maezo.agents.helena.graph import HelenaGraph, HelenaState


def responder(self: HelenaGraph, state: HelenaState, texto: str) -> dict[str, Any] | None:
    """A resposta fixa (oferta da equipe ou reembolso), ou `None` (o caminho de sempre)."""
    from maezo.agents.helena import graph as _g

    if (oferta := _oferta.responder(self, state)) is not None:
        return oferta
    if _reembolso.pergunta_de_reembolso(texto) and not _g._sinal_fraco_de_emergencia(texto):
        return self._base_texto_fixo(
            self._update_sem_modelo(state), self._textos[_reembolso.CHAVE_REEMBOLSO_FORA]
        )
    return None


__all__ = ["responder"]
