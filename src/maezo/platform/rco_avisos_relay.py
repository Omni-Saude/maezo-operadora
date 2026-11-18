"""Relay do outbox `rco_aviso_auditoria` (0036) para `operadora.notifications.internal` (DL-0185, unidade 2a).

Metade de entrega do outbox de `maezo.platform.rco_avisos`: o harness grava uma linha por (aviso, fila)
depois de completar a tarefa; este relay as publica, at-least-once, como `type="rco.aviso_auditoria"`.

    python -m maezo.platform.rco_avisos_relay --once          # drena ate nao sobrar linha e sai
    python -m maezo.platform.rco_avisos_relay --reprocessar   # as `falhou` voltam a `pendente`; drena e sai
    python -m maezo.platform.rco_avisos_relay                 # varre ate SIGTERM/SIGINT

SAIDA DO PROCESSO (`--once`/`--reprocessar`): 0 = drenou sem falha; 1 = alguma publicacao falhou
(`publicacao_falhou`) ou o limite de lotes (`MAX_LOTES_ONCE`) foi atingido com linhas restantes; 2 = outbox
indisponivel. No modo continuo, outbox indisponivel vira log, metrica (`outbox_indisponivel`) e espera do
intervalo, e a varredura continua.

INVOCADO EXPLICITAMENTE, nunca iniciado pelos daemons (mesmo motivo de `maezo.a2a.outbox_relay`): ligar
trafego real num topico e decisao de implantacao, e o consumidor das mensagens e de unidade posterior (2b).
Enquanto o relay nao roda, as linhas ficam `pendente` e nada se perde.

ORDEM POR LINHA: publicar e DEPOIS marcar `entregue`. Queda entre os dois deixa a linha `reivindicada` com
lease que vence e a proxima varredura a republica (o consumidor deduplica por `chave_idempotencia`, que vai
na mensagem). Publicacao que falha (`best_effort=False`: a falha do broker sobe aqui) marca a linha
`falhou` com o codigo fechado `publicacao_falhou` e `tentativas + 1`, para a varredura e devolve o resto do
lote a `pendente` sem contar tentativa. `falhou` so volta por `--reprocessar` (ato de operador): um broker
fora do ar nao vira laco de republicacao.

A MENSAGEM leva so `rco_avisos.montar_payload` (ids, sinal, fila, `prazo_parametro`); a chave de particao e
`{tenant}|{task_id}`. Nenhum log leva a mensagem: so o id da linha, as tentativas e o codigo.

FAIL-CLOSED NA COMPOSICAO: sem `DATABASE_URL` ou sem `KAFKA_BOOTSTRAP_SERVERS` o relay nao sobe.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import signal
import socket
import sys
from dataclasses import dataclass
from typing import Any, Final, Protocol

import structlog
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from maezo.platform.observability import record_rco_aviso
from maezo.platform.rco_avisos import (
    DEFAULT_LEASE_S,
    DEFAULT_LOTE,
    ERRO_PUBLICACAO,
    TOPICO,
    AvisoReivindicado,
    OutboxAvisosIndisponivelError,
    PostgresAvisosRco,
    chave_de_particao,
    montar_payload,
)

logger = structlog.get_logger(__name__)

DEFAULT_INTERVALO_S: Final[float] = 2.0
#: `--once`: teto de lotes de uma execucao (um outbox que nunca esvazia nao prende o processo).
MAX_LOTES_ONCE: Final[int] = 1000
#: Codigo do relatorio quando o outbox nao respondeu no modo continuo (nunca gravado na linha).
ERRO_OUTBOX_INDISPONIVEL: Final[str] = "outbox_indisponivel"
SAIDA_OK: Final[int] = 0
SAIDA_FALHA_PUBLICACAO: Final[int] = 1
SAIDA_OUTBOX_INDISPONIVEL: Final[int] = 2


class OutboxAvisos(Protocol):
    """O que o relay usa do outbox (o `PostgresAvisosRco` serve; os testes usam um em memoria)."""

    async def reivindicar(
        self, *, reivindicada_por: str, lote: int = ..., lease_s: float = ...
    ) -> list[AvisoReivindicado]: ...
    async def marcar_entregue(self, ids: list[int], *, reivindicada_por: str) -> int: ...
    async def marcar_falhou(self, ids: list[int], *, reivindicada_por: str, erro: str) -> int: ...
    async def liberar(self, ids: list[int], *, reivindicada_por: str) -> int: ...
    async def reprocessar_falhas(self) -> int: ...


class Publicador(Protocol):
    """O `KafkaPublisher` da casa (`AioKafkaEventsProducer`)."""

    async def publish(
        self, topic: str, value: dict[str, Any], *, key: str | None = None, best_effort: bool | None = None
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class RelatorioVarredura:
    """O que UMA varredura fez. So contagens e o codigo da falha, nunca a mensagem."""

    reivindicadas: int = 0
    entregues: int = 0
    falhas: int = 0
    liberadas: int = 0
    erro: str | None = None


def reivindicador_padrao() -> str:
    """Dono do lease: `{hostname}:{pid}` (o operador acha o processo de uma linha presa)."""
    return f"{socket.gethostname()}:{os.getpid()}"


async def drain_once(
    outbox: OutboxAvisos,
    publicador: Publicador,
    *,
    reivindicada_por: str,
    lote: int = DEFAULT_LOTE,
    lease_s: float = DEFAULT_LEASE_S,
) -> RelatorioVarredura:
    """Reivindica, publica e marca, uma vez (ordem e falha na docstring do modulo)."""
    avisos = await outbox.reivindicar(reivindicada_por=reivindicada_por, lote=lote, lease_s=lease_s)
    if not avisos:
        return RelatorioVarredura()
    entregues: list[int] = []
    for indice, aviso in enumerate(avisos):
        try:
            publicado = await publicador.publish(
                TOPICO,
                montar_payload(aviso.linha),
                key=chave_de_particao(aviso.linha),
                best_effort=False,
            )
            if publicado is False:
                raise RuntimeError("publicacao engolida")
        except Exception:  # a falha do broker nao pode perder a linha
            logger.error(
                "rco_aviso_relay_publicacao_falhou",
                linha_id=aviso.id,
                tentativas=aviso.tentativas + 1,
                erro=ERRO_PUBLICACAO,
            )
            selados = await outbox.marcar_entregue(entregues, reivindicada_por=reivindicada_por)
            falhas = await outbox.marcar_falhou(
                [aviso.id], reivindicada_por=reivindicada_por, erro=ERRO_PUBLICACAO
            )
            liberadas = await outbox.liberar(
                [a.id for a in avisos[indice + 1 :]], reivindicada_por=reivindicada_por
            )
            record_rco_aviso("entregue", selados)
            record_rco_aviso("falha_publicar", falhas)
            return RelatorioVarredura(
                reivindicadas=len(avisos),
                entregues=selados,
                falhas=falhas,
                liberadas=liberadas,
                erro=ERRO_PUBLICACAO,
            )
        entregues.append(aviso.id)
    # Janela de queda: tudo acima ja saiu; morrer aqui custa uma republicacao, nunca um aviso.
    selados = await outbox.marcar_entregue(entregues, reivindicada_por=reivindicada_por)
    record_rco_aviso("entregue", selados)
    logger.info("rco_aviso_relay_varredura", reivindicadas=len(avisos), entregues=selados)
    return RelatorioVarredura(reivindicadas=len(avisos), entregues=selados)


async def reprocessar(outbox: OutboxAvisos) -> int:
    """Devolve as linhas `falhou` a `pendente` com `ultimo_erro` limpo (as tentativas ficam)."""
    devolvidas = await outbox.reprocessar_falhas()
    record_rco_aviso("reprocessado", devolvidas)
    logger.info("rco_aviso_relay_reprocessadas", linhas=devolvidas)
    return devolvidas


async def run_relay_loop(
    outbox: OutboxAvisos,
    publicador: Publicador,
    *,
    reivindicada_por: str,
    lote: int = DEFAULT_LOTE,
    lease_s: float = DEFAULT_LEASE_S,
    intervalo_s: float = DEFAULT_INTERVALO_S,
    parar: asyncio.Event | None = None,
    max_varreduras: int | None = None,
) -> list[RelatorioVarredura]:
    """Varre ate `parar` (ou `max_varreduras`). Dorme so quando a varredura nao achou nada ou falhou."""
    relatorios: list[RelatorioVarredura] = []
    while parar is None or not parar.is_set():
        try:
            relatorio = await drain_once(
                outbox, publicador, reivindicada_por=reivindicada_por, lote=lote, lease_s=lease_s
            )
        except OutboxAvisosIndisponivelError as exc:
            # Banco fora: o processo continua e tenta de novo depois do intervalo (nada se perde).
            logger.error("rco_aviso_relay_outbox_indisponivel", erro=str(exc))
            record_rco_aviso("outbox_indisponivel")
            relatorio = RelatorioVarredura(erro=ERRO_OUTBOX_INDISPONIVEL)
        relatorios.append(relatorio)
        if max_varreduras is not None and len(relatorios) >= max_varreduras:
            break
        if relatorio.reivindicadas == 0 or relatorio.erro is not None:
            if parar is not None:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(parar.wait(), timeout=intervalo_s)
            else:
                await asyncio.sleep(intervalo_s)
    return relatorios


async def drenar(
    outbox: OutboxAvisos,
    publicador: Publicador,
    *,
    reivindicada_por: str,
    lote: int = DEFAULT_LOTE,
    lease_s: float = DEFAULT_LEASE_S,
    max_lotes: int = MAX_LOTES_ONCE,
) -> int:
    """`--once`: varre ate nao sobrar linha reivindicavel, no maximo `max_lotes` lotes; devolve o codigo de
    saida (docstring do modulo). `OutboxAvisosIndisponivelError` vira 2, nunca pilha."""
    try:
        for _ in range(max_lotes):
            relatorio = await drain_once(
                outbox, publicador, reivindicada_por=reivindicada_por, lote=lote, lease_s=lease_s
            )
            if relatorio.erro is not None:
                return SAIDA_FALHA_PUBLICACAO
            if relatorio.reivindicadas == 0:
                return SAIDA_OK
        logger.error("rco_aviso_relay_limite_de_lotes", max_lotes=max_lotes)
        return SAIDA_FALHA_PUBLICACAO
    except OutboxAvisosIndisponivelError as exc:
        logger.error("rco_aviso_relay_outbox_indisponivel", erro=str(exc))
        record_rco_aviso("outbox_indisponivel")
        return SAIDA_OUTBOX_INDISPONIVEL


async def executar(
    args: argparse.Namespace,
    outbox: OutboxAvisos,
    publicador: Publicador,
    *,
    reivindicada_por: str,
    lote: int = DEFAULT_LOTE,
    lease_s: float = DEFAULT_LEASE_S,
    intervalo_s: float = DEFAULT_INTERVALO_S,
    parar: asyncio.Event | None = None,
) -> int:
    """O que a CLI faz depois de compor o relay; devolve o codigo de saida do processo."""
    if args.reprocessar:
        try:
            await reprocessar(outbox)
        except OutboxAvisosIndisponivelError as exc:
            logger.error("rco_aviso_relay_outbox_indisponivel", erro=str(exc))
            record_rco_aviso("outbox_indisponivel")
            return SAIDA_OUTBOX_INDISPONIVEL
    if args.once or args.reprocessar:
        return await drenar(outbox, publicador, reivindicada_por=reivindicada_por, lote=lote, lease_s=lease_s)
    await run_relay_loop(
        outbox,
        publicador,
        reivindicada_por=reivindicada_por,
        lote=lote,
        lease_s=lease_s,
        intervalo_s=intervalo_s,
        parar=parar,
    )
    return SAIDA_OK


class RelaySettings(BaseSettings):
    """Configuracao por ambiente (molde `maezo.a2a.outbox_relay.OutboxRelaySettings`)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    tenant_id: str = Field(default="amh", alias="TENANT_ID")
    database_url: str | None = Field(default=None, alias="DATABASE_URL")
    kafka_bootstrap_servers: str | None = Field(default=None, alias="KAFKA_BOOTSTRAP_SERVERS")
    lote: int = Field(default=DEFAULT_LOTE, alias="RCO_AVISO_RELAY_LOTE")
    lease_s: float = Field(default=DEFAULT_LEASE_S, alias="RCO_AVISO_RELAY_LEASE_S")
    intervalo_s: float = Field(default=DEFAULT_INTERVALO_S, alias="RCO_AVISO_RELAY_INTERVALO_S")


def build_relay(settings: RelaySettings) -> tuple[PostgresAvisosRco, Any]:
    """Raiz de composicao. Recusa subir sem banco ou sem broker."""
    if not settings.database_url:
        raise RuntimeError("rco aviso relay: DATABASE_URL e obrigatorio (nao ha outbox para drenar)")
    if not settings.kafka_bootstrap_servers:
        raise RuntimeError(
            "rco aviso relay: KAFKA_BOOTSTRAP_SERVERS e obrigatorio (nao ha para onde entregar)"
        )
    from maezo.platform.integrations.events_kafka_producer import build_notifications_publisher

    outbox = PostgresAvisosRco(dsn=settings.database_url, tenant=settings.tenant_id)
    return outbox, build_notifications_publisher(settings.kafka_bootstrap_servers)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m maezo.platform.rco_avisos_relay",
        description=(
            "Publica o outbox rco_aviso_auditoria em operadora.notifications.internal, at-least-once. "
            "Invocado explicitamente; nenhum daemon o inicia."
        ),
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="drena ate nao sobrar linha reivindicavel e sai (codigo de saida na docstring)",
    )
    parser.add_argument(
        "--reprocessar",
        action="store_true",
        help="devolve as linhas `falhou` a `pendente` e drena como `--once`",
    )
    return parser


async def main(argv: list[str] | None = None) -> int:
    """Entrada: `python -m maezo.platform.rco_avisos_relay [--once | --reprocessar]`."""
    args = build_arg_parser().parse_args(argv)
    settings = RelaySettings()
    outbox, publicador = build_relay(settings)
    reivindicada_por = reivindicador_padrao()
    parar = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):  # pragma: no cover - ex.: Windows
            loop.add_signal_handler(sig, parar.set)
    await publicador.start()
    try:
        return await executar(
            args,
            outbox,
            publicador,
            reivindicada_por=reivindicada_por,
            lote=settings.lote,
            lease_s=settings.lease_s,
            intervalo_s=settings.intervalo_s,
            parar=parar,
        )
    finally:
        await publicador.close()


if __name__ == "__main__":  # pragma: no cover - entrada do processo
    sys.exit(asyncio.run(main()))


__all__ = [
    "OutboxAvisos",
    "Publicador",
    "RelatorioVarredura",
    "RelaySettings",
    "build_arg_parser",
    "build_relay",
    "drain_once",
    "drenar",
    "executar",
    "main",
    "reivindicador_padrao",
    "reprocessar",
    "run_relay_loop",
]
