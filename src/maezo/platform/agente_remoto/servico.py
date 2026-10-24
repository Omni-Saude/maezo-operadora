"""A partida do servico do agente de entrada (`python -m maezo.platform.agente_remoto`, ADR-0067 fase 4b).

Variaveis (as da composicao do receptor, `AgenteRemotoSettings`: `DATABASE_URL`, `PHI_HMAC_KEY`, `TENANT_ID`,
chaves `MAEZO_HELENA_*`, credenciais AMH... — SEM `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN` e a
chave A2A):

    AGENT_ID                      o agente carregado pelo framework (padrao `helena`)
    MAEZO_AGENTE_REMOTO_PORTA     a porta do app interno (padrao 8000)

O envio do turno volta ao endereco que o RECEPTOR manda em cada pedido (a replica que abriu o turno), nao a um
nome configurado aqui. Falha FECHADA na partida: sem `PHI_HMAC_KEY` o processo nao sobe; com
`MAEZO_AGENTE_DE_ENTRADA_URL` definida tambem nao (o servico do agente nao pode, ele mesmo, delegar o turno).
Uma composicao que falha (DSN, costura nao cercada, checkpointer em producao) mantem `/healthz` de pe' e
responde 503 nas operacoes, como o receptor responde 501 sem despachante.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from typing import Final

import structlog

from maezo.agents import carregar_agente_de_entrada
from maezo.contratos import conversa_remota as fio
from maezo.platform.health import build_health_server
from maezo.platform.webhooks.service import aquecer_catalogo, build_agent_dependencies
from maezo.platform.webhooks.whatsapp.dispatch import ENV_AGENTE_DE_ENTRADA_URL

from .cliente import PRAZO_PADRAO_S, cliente_http
from .servidor import ServicoDoAgente, criar_app
from .settings import AgenteRemotoSettings

logger = structlog.get_logger(__name__)

ENV_AGENT_ID: Final[str] = "AGENT_ID"
ENV_PORTA: Final[str] = "MAEZO_AGENTE_REMOTO_PORTA"
PORTA_PADRAO: Final[int] = 8000
#: Prazo da volta do envio ao receptor (um envio de WhatsApp, nao um turno).
PRAZO_DO_ENVIO_S: Final[float] = 30.0


async def run(settings: AgenteRemotoSettings) -> None:
    """Sobe o servico ate' SIGTERM/SIGINT."""
    if os.environ.get(ENV_AGENTE_DE_ENTRADA_URL, "").strip():
        raise RuntimeError(
            f"servico do agente com {ENV_AGENTE_DE_ENTRADA_URL} definida: recusando delegar o turno"
        )
    chave = fio.derivar_chave(settings.phi_hmac_key or "")
    agent_id = os.environ.get(ENV_AGENT_ID, "").strip() or "helena"
    porta = int(os.environ.get(ENV_PORTA, "").strip() or PORTA_PADRAO)
    entrada = carregar_agente_de_entrada(agent_id)

    estado = await build_agent_dependencies(settings)
    receptor = cliente_http(None, prazo_s=PRAZO_DO_ENVIO_S)
    servico = (
        ServicoDoAgente(entrada=entrada, dependencias=estado.dispatcher, chave=chave, receptor=receptor)
        if estado.dispatcher is not None
        else None
    )
    vivo = True
    app = criar_app(servico, chave=chave, is_live=lambda: vivo, motivo_indisponivel=estado.dispatcher_error)
    server = build_health_server(app, port=porta)
    server.capture_signals = contextlib.nullcontext  # type: ignore[assignment]  # os sinais sao nossos
    parar = asyncio.Event()
    laco = asyncio.get_running_loop()
    tarefa = asyncio.create_task(server.serve(), name="agente-remoto")

    def _pedir_parada(sig: signal.Signals) -> None:
        nonlocal vivo
        logger.info("shutdown_signal", signal=sig.name)
        vivo = False
        server.should_exit = True
        parar.set()

    def _servidor_parou(_tarefa: asyncio.Task[None]) -> None:
        nonlocal vivo
        vivo = False
        parar.set()

    tarefa.add_done_callback(_servidor_parou)
    for _sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            laco.add_signal_handler(_sig, _pedir_parada, _sig)

    # DL-0128: o catalogo da cobertura assistida e' usado pelo grafo, que agora roda aqui.
    catalogo = (
        asyncio.create_task(aquecer_catalogo(estado.dispatcher), name="catalogo-procedimentos")
        if estado.dispatcher is not None
        else None
    )
    logger.info(
        "agente_remoto_rodando",
        agent_id=agent_id,
        porta=porta,
        pronto=servico is not None,
        prazo_padrao_do_cliente_s=PRAZO_PADRAO_S,
    )
    await parar.wait()
    if catalogo is not None and not catalogo.done():
        catalogo.cancel()
    server.should_exit = True
    with contextlib.suppress(asyncio.CancelledError, SystemExit):
        await tarefa
    with contextlib.suppress(Exception):
        await receptor.aclose()
    if estado.cibseven_transport is not None:
        with contextlib.suppress(Exception):
            await estado.cibseven_transport.close()
    if estado.helena_identidade is not None:
        with contextlib.suppress(Exception):
            await estado.helena_identidade.aclose()
    if estado.checkpointer is not None:
        with contextlib.suppress(Exception):
            await estado.checkpointer.aclose()
    logger.info("agente_remoto_parado", agent_id=agent_id)


__all__ = ["ENV_AGENT_ID", "ENV_PORTA", "run"]
