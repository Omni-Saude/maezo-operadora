"""A ROTA GLOBAL do modelo da allowlist (Haiku 5.5 pela rota autorizada pelo DPO) e as CONVERSAS autorizadas.

Limpeza da saude, etapa 1 (dono, 02/11/2026): antes, o transporte do Haiku e a lista de conversas so' existiam
dentro do redator da saude gerativa (`RedatorDeSaude`), e o redator so' existia com a base de saude ligada.
A porta unica (`habilidades/saude_original`), o portao do roteador ativo e o `modelo_allowlist` dependiam
disso. Agora a composicao (`platform/webhooks/service.py`) monta esta rota SOZINHA, so' com a lista de
conversas: a base de saude e a saude gerativa podem sair sem desligar a porta nem o roteador.

Mesma env de antes por enquanto (`MAEZO_HELENA_SAUDE_GERATIVA_CONVERSAS`): no dev, a mesma lista e o mesmo
transporte (`experimento_global.py`, ao lado). Mora no runtime de inferencia (SDK dos agentes, ADR-0067): a
composicao da plataforma a monta sem importar o interior da Helena. Modulo FOLHA.

Etapa 2 da limpeza (dono, 02/11/2026; DPO, 27/10/2026): `todas=True` (env `MAEZO_HELENA_ROTA_GLOBAL_TODAS`,
desligada por padrao) autoriza TODA conversa keyed (`wa:{tenant}:hk1_...`), sem lista. E' um interruptor
explicito e booleano: a lista continua recusando curinga, e id fora do formato keyed nunca entra.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Final

#: O formato de um `conversation_id` keyed do receptor (`wa:{tenant}:hk1_<hmac-sha256>`).
_ID_DE_CONVERSA: Final[re.Pattern[str]] = re.compile(r"^wa:[a-z0-9][a-z0-9_-]{0,31}:hk1_[0-9a-f]{64}$")


def conversas_autorizadas(bruto: str | Iterable[str] | None) -> frozenset[str]:
    """A allowlist lida da env (`MAEZO_HELENA_SAUDE_GERATIVA_CONVERSAS`: ids separados por virgula/espaco).
    So' entra id no formato keyed; qualquer outra coisa e' DESCARTADA (nunca um curinga, nunca um
    telefone)."""
    if bruto is None:
        return frozenset()
    itens = re.split(r"[\s,;]+", bruto) if isinstance(bruto, str) else list(bruto)
    return frozenset(i.strip() for i in itens if isinstance(i, str) and _ID_DE_CONVERSA.fullmatch(i.strip()))


@dataclass(frozen=True)
class RotaGlobal:
    """O transporte da rota global (`TransporteGlobalExperimental`, ou um falso nos testes) e as conversas
    em que ele pode ser usado. A composicao constroi UM por processo; o grafo pergunta `autorizada`."""

    transporte: Any
    conversas: frozenset[str] = field(default_factory=frozenset)
    #: Todas as conversas keyed (etapa 2 da limpeza da saude): a lista deixa de limitar.
    todas: bool = False

    def autorizada(self, conversation_id: object) -> bool:
        if not isinstance(conversation_id, str):
            return False
        if self.todas:
            return _ID_DE_CONVERSA.fullmatch(conversation_id) is not None
        return conversation_id in self.conversas


__all__ = ["RotaGlobal", "conversas_autorizadas"]
