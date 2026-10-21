"""Suporte compartilhado das provas ponta a ponta do SP-OP-ESCALATION-001 (frente D).

Test-only — nunca importado por `src/`. Tudo aqui roda sobre infraestrutura REAL (motor CIB Seven,
broker Kafka, Postgres): nenhuma peca substitui o motor (AGENTS.md, regra 4; ADR-0011). Quatro pecas:

* `consumidor_kafka_real` — o consumidor de PRODUCAO (`AioKafkaBridgeConsumer`) num grupo de consumo
  UNICO por teste. Antes de entregar o consumidor: o topico existe com lider eleito
  (`_await_topic_ready`), a atribuicao cobre todas as particoes (`_await_assigned`) e a posicao do
  grupo e' ANCORADA no fim do log (`seek_to_end` + `commit`). Ancorar com commit e' o que torna a
  reentrega observavel: um segundo consumidor do MESMO grupo (`grupo=...`) retoma do offset
  confirmado, como o daemon depois de um reinicio — sem o commit ele cairia no `latest` e pularia a
  mensagem que nao foi confirmada.
* `ConsumidorLimitado` — entrega ao laco de producao (`run_resume_loop`, `run_inbox_loop`) SO' as
  mensagens DESTE caso, lidas do consumidor real, e encerra a iteracao depois delas. O laco de
  producao e' um `async for` sem fim; esta e' a mesma ideia de `_SingleMessageConsumer`
  (`test_events_kafka_producer_live.py`), generalizada. O `commit` continua sendo o do broker; o
  gancho `antes_do_commit` deixa o teste observar o estado do mundo NO INSTANTE do commit (a ordem
  "linha duravel antes do offset" e' exatamente isso).
* `drenar_por_chave` — `fetchAndLock` escopado pela businessKey do caso, entregue ao
  `WorkerHarness._handle` REAL (o mesmo caminho do laco de producao), em RODADAS LIMITADAS. Sem
  `sleep`: o ritmo e' o long-poll do proprio motor (`asyncResponseTimeout`).
* `CustodiaFalsa` — a custodia do telefone (ADR-0061) com os dubles de
  `tests/support/recipient_custody_fakes.py`: o lado do receptor so' cifra, o da retomada so'
  decifra, e os dois leem as MESMAS linhas.

Ponto de extensao (frentes paralelas, NAO cobertas aqui): os eventos `agent_resume.falha` /
`agent_resume.fila_morta` e o aviso ao supervisor no inbox (frente A) e o limite de 2000 caracteres
do recado / o registro de publicacao da fila (frente B) podem reutilizar `ConsumidorLimitado` e
`consumidor_kafka_real` quando entrarem.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from maezo.platform.integrations.notifications_bridge import AioKafkaBridgeConsumer, BridgeMessage
from maezo.tools.workers.harness import ExternalTask, WorkerHarness, _from_camunda_var
from tests.integration.platform.test_events_kafka_producer_live import _await_assigned, _await_topic_ready
from tests.support.recipient_custody_fakes import FakeKms, InMemoryRecipientVault

#: Os tres topicos de external task do SP-OP-ESCALATION-001 (BPMN `camunda:topic`).
TOPICO_PUBLICAR = "operadora.events.publish"
TOPICO_NOTIFICAR_TIME = "operadora.escalation.notify_team"
TOPICO_NOTIFICAR_SUPERVISOR = "operadora.escalation.notify_supervisor"
TOPICOS_DO_ESCALONAMENTO: tuple[str, ...] = (
    TOPICO_PUBLICAR,
    TOPICO_NOTIFICAR_TIME,
    TOPICO_NOTIFICAR_SUPERVISOR,
)


def kafka_bootstrap_servers() -> str:
    """O broker da lane (o mesmo default das outras suites ao vivo)."""
    return os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")


def grupo_unico(prefixo: str) -> str:
    """Um consumer group que nenhum outro teste (nem o daemon) usa."""
    return f"{prefixo}-{uuid.uuid4().hex}"


@asynccontextmanager
async def consumidor_kafka_real(
    topico: str, *, grupo: str, ancorar_no_fim: bool = True
) -> AsyncIterator[AioKafkaBridgeConsumer]:
    """O `AioKafkaBridgeConsumer` de producao, pronto para o teste. Ver o docstring do modulo.

    `ancorar_no_fim=False` e' para o SEGUNDO consumidor de um grupo ja' ancorado (a reentrega): ele
    deve retomar do offset confirmado, nunca saltar para o fim.
    """
    servidores = kafka_bootstrap_servers()
    particoes = await _await_topic_ready(servidores, topico)
    consumidor = AioKafkaBridgeConsumer(bootstrap_servers=servidores, topic=topico, group_id=grupo)
    await consumidor.start()
    try:
        real = consumidor._consumer  # so' prontidao/ancora; nenhuma assercao passa por aqui
        conhecidas = real.partitions_for_topic(topico) or particoes
        await _await_assigned(real, expected_partitions=len(conhecidas))
        if ancorar_no_fim:
            await real.seek_to_end()
            for tp in real.assignment():
                await real.position(tp)  # resolve a posicao AGORA, antes de qualquer producao
            await consumidor.commit()
        yield consumidor
    finally:
        await consumidor.stop()


async def offsets_confirmados(consumidor: AioKafkaBridgeConsumer) -> dict[int, int | None]:
    """O offset CONFIRMADO no broker para cada particao atribuida (o fato, nao a posicao local)."""
    real = consumidor._consumer
    return {tp.partition: await real.committed(tp) for tp in real.assignment()}


@dataclass
class ConsumidorLimitado:
    """Ver o docstring do modulo. Satisfaz `BridgeKafkaConsumer` (start/stop/commit/__aiter__)."""

    real: AioKafkaBridgeConsumer
    do_caso: Callable[[BridgeMessage], bool]
    quantas: int = 1
    antes_do_commit: Callable[[], Awaitable[None]] | None = None
    entregues: list[BridgeMessage] = field(default_factory=list)
    commits: int = 0

    async def start(self) -> None:
        return None  # o dono do consumidor real o inicia (`consumidor_kafka_real`)

    async def stop(self) -> None:
        return None

    async def commit(self) -> None:
        if self.antes_do_commit is not None:
            await self.antes_do_commit()
        self.commits += 1
        await self.real.commit()

    def __aiter__(self) -> AsyncIterator[BridgeMessage]:
        return self._iterar()

    async def _iterar(self) -> AsyncIterator[BridgeMessage]:
        async for mensagem in self.real:
            if not self.do_caso(mensagem):
                continue  # outro caso no topico compartilhado: nao e' deste teste
            self.entregues.append(mensagem)
            yield mensagem
            if len(self.entregues) >= self.quantas:
                return


def mensagem_da_chave(business_key: str) -> Callable[[BridgeMessage], bool]:
    """Filtro de `ConsumidorLimitado`: a mensagem cujo payload ancora nesta businessKey.

    `_business_key` e' o campo que `operadora.events.publish` injeta (process_completed);
    `business_key` e' o da notificacao interna (`escalation.notify_team`).
    """

    def _filtro(mensagem: BridgeMessage) -> bool:
        valor = mensagem.value
        if not isinstance(valor, dict):
            return False
        return business_key in (valor.get("_business_key"), valor.get("business_key"))

    return _filtro


async def drenar_por_chave(
    client: httpx.AsyncClient,
    harness: WorkerHarness,
    *,
    worker_id: str,
    business_key: str,
    instance_id: str,
    pronto: Callable[[], Awaitable[bool]],
    topicos: Iterable[str] = TOPICOS_DO_ESCALONAMENTO,
    rodadas: int = 60,
) -> list[ExternalTask]:
    """Serve as external tasks DESTE caso com os handlers reais ate' `pronto()`; rodadas limitadas.

    Mesmo desenho de `test_helena_escalation_broker._drain_instance`: `fetchAndLock` com filtro de
    `businessKey` (nunca consome tarefa de outro caso deixada na stack compartilhada), variaveis
    decodificadas pelo decodificador de producao e entregues ao `WorkerHarness._handle` real, cujo
    transporte faz o complete/bpmnError de verdade. Incidente na instancia e' falha imediata: um
    caminho modelado segue por boundary, nunca por incidente.
    """
    lista = list(topicos)
    tratadas: list[ExternalTask] = []
    for _ in range(rodadas):
        if await pronto():
            return tratadas
        resposta = await client.post(
            "/external-task/fetchAndLock",
            json={
                "workerId": worker_id,
                "maxTasks": 5,
                "asyncResponseTimeout": 500,
                "topics": [
                    {
                        "topicName": topico,
                        "lockDuration": 15000,
                        "businessKey": business_key,
                        "deserializeValues": False,
                    }
                    for topico in lista
                ],
            },
        )
        resposta.raise_for_status()
        for item in resposta.json():
            assert item["processInstanceId"] == instance_id
            assert item["businessKey"] == business_key
            tarefa = ExternalTask(
                task_id=item["id"],
                topic=item["topicName"],
                process_instance_id=instance_id,
                business_key=business_key,
                worker_id=worker_id,
                variables={nome: _from_camunda_var(valor) for nome, valor in item["variables"].items()},
                retries=item.get("retries"),
                process_definition_key=item.get("processDefinitionKey"),
                activity_id=item.get("activityId"),
            )
            tratadas.append(tarefa)
            await harness._handle(tarefa)
        incidentes = await client.get("/incident", params={"processInstanceId": instance_id})
        incidentes.raise_for_status()
        assert incidentes.json() == [], f"incidente na instancia {instance_id}: {incidentes.json()}"
    raise AssertionError(f"o caso {business_key} nao chegou ao estado esperado em {rodadas} rodadas")


async def tarefas_humanas(client: httpx.AsyncClient, instance_id: str) -> list[dict[str, Any]]:
    resposta = await client.get("/task", params={"processInstanceId": instance_id})
    resposta.raise_for_status()
    tarefas: list[dict[str, Any]] = resposta.json()
    return tarefas


async def instancia_encerrada(client: httpx.AsyncClient, instance_id: str) -> bool:
    resposta = await client.get(f"/history/process-instance/{instance_id}")
    resposta.raise_for_status()
    return resposta.json().get("state") == "COMPLETED"


@dataclass
class CustodiaFalsa:
    """Custodia do telefone (ADR-0061) com a separacao de IAM: receptor cifra, retomada decifra."""

    receptor: InMemoryRecipientVault
    retomada: InMemoryRecipientVault

    async def semear(self, *, conversation_id: str, phone: str, received_at: datetime | None = None) -> None:
        """Grava o numero CIFRADO pelo lado do receptor (o mesmo `upsert` do webhook)."""
        await self.receptor.upsert(
            conversation_id=conversation_id, phone=phone, received_at=received_at or datetime.now(UTC)
        )


def custodia_falsa(tenant: str) -> CustodiaFalsa:
    kms = FakeKms()
    receptor = InMemoryRecipientVault(
        tenant=tenant, kms=kms.with_permissions(allow_encrypt=True, allow_decrypt=False)
    )
    retomada = InMemoryRecipientVault(
        tenant=tenant, kms=kms.with_permissions(allow_encrypt=False, allow_decrypt=True), rows=receptor.rows
    )
    return CustodiaFalsa(receptor=receptor, retomada=retomada)
