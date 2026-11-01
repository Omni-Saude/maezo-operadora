"""Politica de ATRASO do Lucas — o LIMIAR NAO MORA MAIS AQUI (R-047/F-031, decisao do dono de 29/10/2026).

Historia: na rodada 12 (decisao do dono, sessao de 14/10/2026) o Lucas passou a abrir caso humano so' para
ATRASO REAL — pagador PESSOA FISICA com >= 2 meses `vencida` OU maior atraso > 30 dias. Ate' R-047 esse
limiar era calculado AQUI (`LimiaresDeAtraso.critico`) e a DMN `lucas_billing_admissibility` so' lia o
booleano `atraso_critico`: uma regra deterministica de negocio fora da tabela (AGENTS.md, regra 5; F-031).

Agora: o grafo conta os FATOS (`meses_vencidos`, `dias_atraso_max` — `agents/lucas/graph.py`) e a tabela
decide, com os MESMOS valores (regras `lba_r_atraso_critico`: `meses_vencidos >= 2`, e
`lba_r_atraso_critico_dias`: `dias_atraso_max > 30`, ambas com `pagador_empresa=false`). O ajuste por ambiente
(`MAEZO_LUCAS_ATRASO_MESES_MIN`/`_DIAS_MIN`) deixou de existir: mudar o limiar e' mudar a tabela.

O que sobra neste modulo e' uma LAPIDE, sem consumidor em runtime:
  - `MESES_MINIMO_PADRAO`/`DIAS_MINIMO_PADRAO` sao ESPELHOS documentais dos literais da tabela; um teste
    (`tests/unit/agents/test_lucas_limiar_atraso_dmn.py`) os prende ao XML, entao nunca divergem em silencio,
    e nenhum codigo de `src/` os le';
  - `LimiaresDeAtraso().critico(...)` devolve `None` — "indecidido aqui": o Python nao decide mais se um
    atraso e' critico. Fica so' para importadores antigos (e para a prova do F-031) nao quebrarem.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

#: Espelho documental do literal `>= 2` da regra `lba_r_atraso_critico` (pinado por teste contra o XML).
MESES_MINIMO_PADRAO: Final[int] = 2
#: Espelho documental do literal `> 30` da regra `lba_r_atraso_critico_dias` (pinado por teste contra o XML).
DIAS_MINIMO_PADRAO: Final[int] = 30


@dataclass(frozen=True, slots=True)
class LimiaresDeAtraso:
    """Lapide (R-047): o limiar mora na DMN `lucas_billing_admissibility`; este tipo nao decide nada."""

    def critico(self, *, meses_vencidos: int, dias_atraso_max: int | None) -> None:
        """Sempre `None`: quem decide se o atraso e' critico e' a tabela, nunca o Python (F-031)."""
        del meses_vencidos, dias_atraso_max


__all__ = ["DIAS_MINIMO_PADRAO", "MESES_MINIMO_PADRAO", "LimiaresDeAtraso"]
