"""Ledger append-only do status do prestador (MPTT P3) — escuro atras de `MAEZO_PRESTADOR_STATUS_LEDGER`.

    projecao      contrato tipado e versionado: terminal CRED/FRAUDE -> delta do ledger
    ledger        forma da linha, porta e store Postgres da migracao 0032
    baseline      importacao unica da baseline da rede atestada pela AMH
    reconciliador terminais do motor (costura do PR #1167) -> ledger, falha fechada
    cli           `python -m maezo.platform.prestador_status`

Importar o pacote nao tem efeito colateral.
"""

from __future__ import annotations

__all__: list[str] = []
