"""A partida do servico do agente de entrada (`python -m maezo.platform.agente_remoto`, ADR-0067 fase 4b).

Variaveis (alem das do receptor, que a composicao reaproveita: `DATABASE_URL`, `PHI_HMAC_KEY`, `TENANT_ID`,
chaves `MAEZO_HELENA_*`, credenciais AMH...):

    AGENT_ID                      o agente carregado pelo framework (padrao `helena`)
    MAEZO_RECEPTOR_URL            a URL interna do receptor, para a volta do envio (OBRIGATORIA)
    MAEZO_AGENTE_REMOTO_PORTA     a porta do app interno (padrao 8000)

Falha FECHADA na partida: sem `MAEZO_RECEPTOR_URL` ou sem `PHI_HMAC_KEY` o processo nao sobe; com
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
from maezo.platform.webhooks.whatsapp.settings import WhatsAppWebhookSettings

from .cliente import PRAZO_PADRAO_S, cliente_http
from .servidor import ServicoDoAgente, criar_app

logger = structlog.get_logger(__name__)

ENV_AGENT_ID: Final[str] = "AGENT_ID"
ENV_RECEPTOR_URL: Final[str] = "MAEZO_RECEPTOR_URL"
ENV_PORTA: Final[str] = "MAEZO_AGENTE_REMOTO_PORTA"
PORTA_PADRAO: Final[int] = 8000
#: Prazo da volta do envio ao receptor (um envio de WhatsApp, nao um turno).
PRAZO_DO_ENVIO_S: Final[float] = 30.0


async def run(settings: WhatsAppWebhookSettings) -> None:
    """Sobe o servico ate' SIGTERM/SIGINT."""
    if os.environ.get(ENV_AGENTE_DE_ENTRADA_URL, "").strip():
        raise RuntimeError(
            f"servico do agente com {ENV_AGENTE_DE_ENTRADA_URL} definida: recusando delegar o turno"
        )
    receptor_url = os.environ.get(ENV_RECEPTOR_URL, "").strip()
    if not receptor_url:
        raise RuntimeError(f"{ENV_RECEPTOR_URL} ausente: sem ela o envio do turno nao volta ao receptor")
    chave = fio.derivar_chave(settings.phi_hmac_key or "")
    agent_id = os.environ.get(ENV_AGENT_ID, "").strip() or "helena"
    porta = int(os.environ.get(ENV_PORTA, "").strip() or PORTA_PADRAO)
    entrada = carregar_agente_de_entrada(agent_id)

    estado = await build_agent_dependencies(settings)
    receptor = cliente_http(receptor_url, prazo_s=PRAZO_DO_ENVIO_S)
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


__all__ = ["ENV_AGENT_ID", "ENV_PORTA", "ENV_RECEPTOR_URL", "run"]
