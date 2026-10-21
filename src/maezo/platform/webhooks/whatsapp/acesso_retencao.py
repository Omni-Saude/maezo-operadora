"""Retencao do estado do acesso do beneficiario: 90 DIAS sem atividade (DL-0084, decisao do dono).

`conversa_acesso_beneficiario` (migration 0022) guarda so' estado fechado, contadores, carimbos e a referencia
pseudonima da AMH. A linha e' apagada quando a conversa fica 90 dias sem mensagem (`ultima_mensagem_em`),
EXCETO com `revogacao_pendente`: uma revogacao que o lago da AMH ainda nao recebeu nunca e' apagada (seria
perder o pedido do titular; ela segue o runbook `acesso-beneficiario-revogacao-pendente`). Quem volta depois
consente e se verifica de novo. O registro do consentimento no lago e' da AMH e nao e' tocado aqui.

DOIS jeitos de rodar, ambos IDEMPOTENTES (o DELETE so' pega o que ja' passou do limite; rodar duas vezes, ou
em duas replicas ao mesmo tempo, nao apaga nada a mais):

  * no receptor, com `MAEZO_ACESSO_BENEFICIARIO` ligada: `laco_de_retencao` roda no boot e a cada 6 h, ate' o
    SIGTERM (`service.run`). Falha de banco vira log de erro e a proxima rodada tenta de novo;
  * por linha de comando (tarefa avulsa, ex. ECS run-task):
    `python -m maezo.platform.webhooks.whatsapp.acesso_retencao --tenant <tenant>` com `DATABASE_URL`.

LOG: so' contagens e o nome da classe do erro. Nenhum identificador de conversa.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Final, Protocol

import structlog

from .acesso import RETENCAO_ESTADO

logger = structlog.get_logger(__name__)

#: Intervalo entre rodadas no receptor (a regra e' de dias; 6 h e' folga de sobra e custo desprezivel).
INTERVALO_S: Final[float] = 6 * 3600.0


class _Expurgavel(Protocol):
    async def expurgar_inativos(self) -> int: ...


async def laco_de_retencao(
    acesso: _Expurgavel, parar: asyncio.Event, *, intervalo_s: float = INTERVALO_S
) -> None:
    """Roda o expurgo agora e a cada `intervalo_s`, ate' `parar`. Nunca derruba o receptor."""
    if intervalo_s <= 0:
        raise ValueError("acesso: intervalo da retencao deve ser positivo")
    while not parar.is_set():
        try:
            await acesso.expurgar_inativos()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # isolado: a retencao nunca derruba o atendimento
            logger.error("acesso_retencao_falhou", erro=type(exc).__name__)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(parar.wait(), timeout=intervalo_s)


async def expurgar_uma_vez(*, dsn: str, tenant: str, agora: datetime | None = None) -> int:
    """Uma rodada por linha de comando: apaga o inativo ha' mais de 90 dias DESTE tenant."""
    from .acesso_store import PostgresAcessoStore

    instante = (agora or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    store = PostgresAcessoStore(dsn=dsn, tenant=tenant)
    try:
        apagadas = await store.expurgar_inativos(instante - RETENCAO_ESTADO)
    finally:
        await store.aclose()
    logger.info("acesso_retencao_expurgo", apagadas=apagadas, dias=RETENCAO_ESTADO.days, origem="cli")
    return apagadas


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Retencao (90 dias) do estado do acesso do beneficiario")
    parser.add_argument("--tenant", required=True)
    args = parser.parse_args(argv)
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        print("DATABASE_URL ausente", file=sys.stderr)
        return 2
    apagadas = asyncio.run(expurgar_uma_vez(dsn=dsn, tenant=args.tenant))
    print(f"apagadas={apagadas}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["INTERVALO_S", "expurgar_uma_vez", "laco_de_retencao", "main"]
