"""Entrada do job: `python -m maezo.platform.lembretes [--simular --conversa ID [--ignorar-horario]]`.

Sem argumentos: uma rodada do lembrete. `--simular`: UM lembrete `[TESTE]` sintetico a UMA conversa da
allowlist. `--reativar --conversa ID`: remove o PARAR de uma conversa da allowlist (para repetir o teste).
Desligado (padrao), sai ANTES de montar qualquer dependencia.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from datetime import UTC, datetime

import structlog

from .composicao import ComposicaoDoLembreteError, configurar, montar, montar_store
from .config import LembreteSettings
from .job import executar, simular

logger = structlog.get_logger(__name__)


async def _rodar(args: argparse.Namespace) -> int:
    settings = LembreteSettings()
    if args.reativar:
        if not args.conversa:
            logger.error("lembrete_cobranca_uso", detail="--reativar exige --conversa")
            return 2
        cfg = configurar(settings)
        if cfg.allowlist is None or not cfg.allowlist.permite(args.conversa):
            logger.error("lembrete_cobranca_recusado", motivo="fora_da_allowlist")
            return 2
        store = montar_store(settings)
        try:
            await store.remover_optout(args.conversa)
        finally:
            await store.aclose()
        logger.info("lembrete_cobranca_optout_removido")
        return 0
    if not settings.habilitado:
        logger.info("lembrete_cobranca_resumo", modo="desligado", contagens={"desligado": 1})
        return 0
    if args.simular and not args.conversa:
        logger.error("lembrete_cobranca_uso", detail="--simular exige --conversa")
        return 2
    try:
        deps, cfg = await montar(settings)
    except ComposicaoDoLembreteError as exc:
        logger.error("lembrete_cobranca_nao_montou", detail=str(exc))
        return 1
    agora = datetime.now(UTC)
    if args.simular:
        await simular(
            deps, cfg, conversation_id=args.conversa, agora=agora, ignorar_horario=args.ignorar_horario
        )
    else:
        await executar(deps, cfg, agora=agora)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="maezo.platform.lembretes")
    parser.add_argument("--simular", action="store_true", help="lembrete [TESTE] a UMA conversa da allowlist")
    parser.add_argument("--conversa", help="conversation_id (wa:<tenant>:hk1_...) para --simular/--reativar")
    parser.add_argument("--ignorar-horario", action="store_true", help="so' com --simular: ignora 08h-20h")
    parser.add_argument("--reativar", action="store_true", help="remove o PARAR da conversa (allowlist)")
    return asyncio.run(_rodar(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
