"""A COMPETENCIA de cobranca (`AAAA-MM`): UMA definicao para o repositorio inteiro.

Mora em `runtime/`, e nao num agente nem na plataforma, porque quatro lugares a validam e eles
estao dos dois lados da fronteira: o classify da Helena (`agents/helena/graph.py`), a fonte de
cobranca do Lucas (`agents/lucas/fonte_cobranca.py`), o handoff (`platform/webhooks/whatsapp/
lucas_turno.py`) e o roteador (`.../roteamento.py`, que espelha o CHECK da migration 0017).
`agents/` nao importa `platform/`, e um agente nao importa o outro; `runtime/` todos ja' importam.
Quatro copias da mesma regex eram quatro chances de uma aceitar o que a outra recusa.
"""

from __future__ import annotations

import re
from typing import Final

#: `AAAA-MM`, mes 01..12. O mesmo padrao do CHECK `lucas_competencia` da migration 0017.
COMPETENCIA_RE: Final[re.Pattern[str]] = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def competencia_valida(valor: object) -> bool:
    """`valor` e' uma competencia `AAAA-MM`? Recusa tudo que nao for `str` (valor de modelo ou de
    entrada nao validada pode ser lista, numero, dict)."""
    return isinstance(valor, str) and COMPETENCIA_RE.match(valor) is not None


__all__ = ["COMPETENCIA_RE", "competencia_valida"]
