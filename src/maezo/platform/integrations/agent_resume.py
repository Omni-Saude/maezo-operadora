"""Consumidor de retomada pos-humano (GAP-XHITL-4) — `agents.events.process_completed` -> agente.

**A lacuna.** Quando o humano conclui `UT_TratarEscalonamento` (SP-OP-ESCALATION-001) como
`devolvido_agente`, o passo `ST_PublishProcessCompleted` publica `agents.events.process_completed`.
Ate' este modulo NADA escutava: a conversa no WhatsApp nunca era retomada e o beneficiario nao
recebia a instrucao que o humano escreveu. O BPMN, o contrato e o publicador ja' existiam; faltava
esta peca (`docs/processes/contracts/SP-OP-ESCALATION-001.md`, Notas de design).

**A forma e' a do `notifications_bridge`, nao uma nova.** O seam Kafka (`BridgeKafkaConsumer`,
`BridgeMessage`, `AioKafkaBridgeConsumer`, `FakeBridgeKafkaConsumer`), o erro de mensagem
malformada e a fila morta (`BridgeDlqShunt`, `<topic>.dlq`) sao IMPORTADOS de
`notifications_bridge`, como `notifications_inbox` ja' faz. Tres daemons, uma postura fail-closed,
um contrato de DLQ, um vocabulario de motivos (`BRIDGE_DLQ_REASONS`). Particoes: as do registro
(`topic_registry.TopicEntry.partitions`, default 3); a chave de particao do publicador e'
por entidade, entao eventos da mesma conversa caem na mesma particao e a ordem por conversa vale
para qualquer numero de replicas no mesmo consumer group.

**O evento, exatamente como o BPMN publica** (`event_payload_vars` do `ST_PublishProcessCompleted`):
`tenant_id`, `agent_id`, `conversation_id`, `resultado`, mais o que o worker
`operadora.events.publish` injeta incondicionalmente (`tools/workers/events.py`): `_business_key`
(forma fixa `ESC-{tenant_id}-{conversation_id}`) e `_process_instance_id`. Tudo identificador
estrutural, Zona Geral.

**A instrucao do humano NUNCA viaja no evento (ADR-0006).** `notas_resolucao` e' variavel de
processo gravada na conclusao da tarefa (`gateway/human/completion_engine.py`); este consumidor a
le' do HISTORICO do motor, pela instancia que o evento nomeia
(`HistoricVariableReadingTransport.read_historic_variables`, operacao catalogada
`cibseven.read_historic_variables`, classe `consulta_processo`, pela MESMA costura cercada de
`gate_cibseven` que o resto do motor usa). Um evento que TRAGA qualquer variavel de
`phi_vars.PHI_PROCESS_VARS` e' recusado para a fila morta (`resume_phi_in_event`) e o conteudo
nunca e' lido — o texto nao passa a viajar por aqui nem por engano do publicador.

**Por que o historico, e por quanto tempo ele guarda.** O evento e' publicado no ULTIMO passo antes
de `End_DevolvidoAoAgente`; quando o consumidor chega, a instancia ja' terminou e
`/process-instance/{id}/variables` nao a acha mais. `HistoricVariableInstance` e' gravada pelo
motor a partir do nivel de historico `audit` (o padrao do CIB Seven Run e' `full`; este repo nao o
rebaixa em lugar nenhum) e sobrevive ao fim da instancia ate' o `historyTimeToLive` do processo —
`1825` dias em SP-OP-ESCALATION-001. O que este modulo NAO prova (fronteira honesta, como a do
bridge): o nivel de historico efetivo do motor implantado; o teste ao vivo que o provaria precisa de
um motor de pe'. E a perna D7 (`SecuredCibSevenTransport`, perfil mTLS do motor): ela ainda nao
expoe esta leitura — com esse perfil ligado, `EngineHistoryInstructionSource` recusa na construcao
(`TypeError`) e o daemon nao sobe, em vez de ler por outro caminho. Liga-la e' mapear a leitura em
`EngineOperation.READ_HISTORY`, trabalho da trilha D7.

**Filtro.** SO' `resultado == "devolvido_agente"` acorda o agente. `resolvido_humano` e
`emergencia_acionada` encerram o caso: sao confirmados (offset avanca) sem efeito. Um `resultado`
fora dos tres e' malformacao -> fila morta. Um `agent_id` sem retomador registrado (hoje so' a
Helena tem porta de retomada) e' registrado em WARNING e confirmado — nao e' malformacao, e
bloquear a particao por um agente que ainda nao tem porta pararia a retomada de todos os outros.

**Cruzamento com o historico.** O evento e' Zona Geral e pode ser forjado por qualquer produtor do
topico; o historico e' o fato. Antes de acordar alguem, o consumidor confere que a instancia existe
no historico, que a chave de negocio e a definicao batem com o evento, e que o historico TAMBEM
diz `resultado=devolvido_agente`. Qualquer divergencia -> fila morta (`resume_history_mismatch`).

**O destinatario — custodia cifrada (ADR-0061, Proposto; decisao do dono de 25/09/2026).** O
WhatsApp exige o numero e a conversa so' carrega o `phone_hash` KEYED (ADR-0035). O receptor do
webhook grava o numero CIFRADO (envelope KMS + AES-GCM, `gateway/recipient_custody.py`) a cada
mensagem recebida; SO' este servico decifra (`kms:Decrypt` na role dele, e so' nela). Sem
`RECIPIENT_VAULT_KMS_KEY_ARN` configurado, `main()` RECUSA SUBIR
(`RecipientCustodyUnavailableError`) — e o ADR exige ciencia do DPO antes de ligar em qualquer
ambiente.

**Janela de 24h da Meta.** Fora dela a Meta so' aceita template aprovado. Se a ultima mensagem do
beneficiario (`last_inbound_at`, guardado na mesma linha da custodia) tem 24h ou mais, NADA e'
enviado: o turno registra `retomada_fora_da_janela` na telemetria e a equipe e' avisada pelo
caminho de notificacao existente (`escalation.notify_team` em `operadora.notifications.internal`,
que o inbox de escalonamento ja' consome), para alguem contatar por outro canal.

**Fail-closed, sem engolir erro.** Offset confirmado SO' depois de: retomada concluida (a mensagem
saiu), evento filtrado/ignorado com log, ou malformacao CONFIRMADA na fila morta com fato de
auditoria. Falha de motor, de envio (`RetomadaEnvioFalhouError`) ou de checkpoint PROPAGA: o
offset fica, o evento volta. O envio e' idempotente por instancia de escalonamento (chave de saida
derivada de `_process_instance_id`), entao a reentrega converge em vez de mandar duas vezes.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final, Protocol, runtime_checkable

import structlog
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from maezo.gateway.recipient_custody import (
    DEFAULT_TTL_DAYS,
    RecipientRecord,
    dentro_da_janela,
)
from maezo.platform.integrations.notifications_bridge import (
    REASON_NOT_A_JSON_OBJECT,
    REASON_RESUME_ANCHOR_MISMATCH,
    REASON_RESUME_HISTORY_MISMATCH,
    REASON_RESUME_MISSING_ANCHOR,
    REASON_RESUME_PHI_IN_EVENT,
    REASON_RESUME_RECIPIENT_UNAVAILABLE,
    REASON_RESUME_UNKNOWN_RESULTADO,
    AioKafkaBridgeConsumer,
    BridgeDlqShunt,
    BridgeKafkaConsumer,
    MalformedBridgeMessageError,
    NotificationsBridgeSettings,
    build_dlq_shunt,
)
from maezo.platform.notification_bridge import PROCESS_KEY_ESCALATION
from maezo.platform.topic_registry import dlq_topic_for
from maezo.tools.mcp_cibseven.transport import HistoricVariableReadingTransport
from maezo.tools.workers.phi_vars import PHI_PROCESS_VARS

logger = structlog.get_logger(__name__)

#: O topico de retomada humano->agente para TODOS os agentes (BPMN `ST_PublishProcessCompleted`).
PROCESS_COMPLETED_TOPIC: Final[str] = "agents.events.process_completed"

#: Consumer group PROPRIO: compartilhar o de outro daemon faria os dois disputarem particoes.
DEFAULT_RESUME_CONSUMER_GROUP_ID: Final[str] = "maezo-agent-resume"

#: Os tres desfechos do contrato (SP-OP-ESCALATION-001, `GW_Resultado`).
RESULTADO_DEVOLVIDO_AGENTE: Final[str] = "devolvido_agente"
RESULTADO_RESOLVIDO_HUMANO: Final[str] = "resolvido_humano"
RESULTADO_EMERGENCIA_ACIONADA: Final[str] = "emergencia_acionada"
RESULTADOS_DO_CONTRATO: Final[frozenset[str]] = frozenset(
    {RESULTADO_DEVOLVIDO_AGENTE, RESULTADO_RESOLVIDO_HUMANO, RESULTADO_EMERGENCIA_ACIONADA}
)

#: As variaveis que o consumidor le do historico — e SO' elas saem do motor.
_HISTORY_NAMES: Final[tuple[str, ...]] = ("notas_resolucao", "resultado")


# ---------------------------------------------------------------------------
# Erros — todos os deterministicos sao `MalformedBridgeMessageError` (o gatilho da fila morta).
# ---------------------------------------------------------------------------


class MalformedResumeEventError(MalformedBridgeMessageError):
    """Um evento que NENHUMA reentrega vai consertar: vai para `<topic>.dlq`, o laco continua.

    Subclasse do erro do bridge pela mesma razao de `MalformedTeamNoticeError`: o `BridgeDlqShunt`
    compartilhado e o vocabulario fechado de motivos sao os mesmos para os tres daemons.
    """


class RecipientCustodyUnavailableError(RuntimeError):
    """Nao ha custodia de destinatario configurada — o daemon RECUSA SUBIR (ver docstring)."""


# ---------------------------------------------------------------------------
# O evento
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProcessCompletedEvent:
    """Um `agents.events.process_completed` validado. So' identificadores estruturais."""

    tenant_id: str
    agent_id: str
    conversation_id: str
    resultado: str
    business_key: str
    process_instance_id: str


def _campo(payload: Mapping[str, Any], nome: str) -> str | None:
    valor = payload.get(nome)
    if not isinstance(valor, str) or not valor.strip():
        return None
    return valor.strip()


def parse_process_completed(value: Any, *, tenant_id: str) -> ProcessCompletedEvent:
    """Valida UM evento. Levanta `MalformedResumeEventError` em toda malformacao deterministica.

    Ordem deliberada: a cerca de PHI vem ANTES de qualquer outra, para que um evento que carrega
    `notas_resolucao` seja recusado por ISSO — e nunca aceito por ter, por acaso, todos os outros
    campos certos.
    """
    if not isinstance(value, Mapping):
        raise MalformedResumeEventError("not a JSON object", value, code=REASON_NOT_A_JSON_OBJECT)
    phi = sorted(PHI_PROCESS_VARS.intersection(value))
    if phi:
        # O VALOR nao entra na excecao, no log nem no audit: so' os NOMES (que sao contrato).
        raise MalformedResumeEventError(
            f"event carries Zona PHI variable(s) {phi} (ADR-0006) — refused, content not read",
            {"phi_keys": phi},
            code=REASON_RESUME_PHI_IN_EVENT,
        )
    campos = {
        nome: _campo(value, nome)
        for nome in (
            "tenant_id",
            "agent_id",
            "conversation_id",
            "resultado",
            "_business_key",
            "_process_instance_id",
        )
    }
    ausentes = sorted(nome for nome, valor in campos.items() if valor is None)
    if ausentes:
        raise MalformedResumeEventError(
            f"missing/blank field(s) {ausentes}", {"missing": ausentes}, code=REASON_RESUME_MISSING_ANCHOR
        )
    resultado = str(campos["resultado"])
    if resultado not in RESULTADOS_DO_CONTRATO:
        raise MalformedResumeEventError(
            "resultado outside the contract's three outcomes",
            {"resultado_len": len(resultado)},
            code=REASON_RESUME_UNKNOWN_RESULTADO,
        )
    evento_tenant = str(campos["tenant_id"])
    conversation_id = str(campos["conversation_id"])
    business_key = str(campos["_business_key"])
    if evento_tenant != tenant_id:
        raise MalformedResumeEventError(
            "event tenant is not this daemon's tenant", {}, code=REASON_RESUME_ANCHOR_MISMATCH
        )
    if business_key != f"ESC-{evento_tenant}-{conversation_id}":
        raise MalformedResumeEventError(
            "_business_key is not ESC-{tenant_id}-{conversation_id}", {}, code=REASON_RESUME_ANCHOR_MISMATCH
        )
    return ProcessCompletedEvent(
        tenant_id=evento_tenant,
        agent_id=str(campos["agent_id"]),
        conversation_id=conversation_id,
        resultado=resultado,
        business_key=business_key,
        process_instance_id=str(campos["_process_instance_id"]),
    )


# ---------------------------------------------------------------------------
# Costuras: instrucao (historico do motor), destinatario (custodia), retomador (agente)
# ---------------------------------------------------------------------------


class HumanInstructionSource(Protocol):
    """De onde vem a instrucao do humano para um evento ja' validado."""

    async def fetch(self, event: ProcessCompletedEvent) -> str: ...


class EngineHistoryInstructionSource:
    """`notas_resolucao` do HISTORICO do motor, cruzado com o evento. A unica fonte de producao.

    Recebe o transporte CERCADO (`gate_cibseven`) — nunca um cliente HTTP proprio (fence §8.2).
    Recusa na construcao um transporte que nao le historico de variaveis: descobrir isso na
    primeira mensagem seria descobrir com um evento ja' em maos.
    """

    def __init__(self, transport: Any) -> None:
        if not isinstance(transport, HistoricVariableReadingTransport):
            raise TypeError(
                "EngineHistoryInstructionSource: transport must implement `read_historic_variables` "
                "(HistoricVariableReadingTransport) — the human's instructions live ONLY in the engine "
                "history, and a transport that cannot read it could never resume anybody"
            )
        self._transport = transport

    async def fetch(self, event: ProcessCompletedEvent) -> str:
        historico = await self._transport.read_historic_variables(event.process_instance_id, _HISTORY_NAMES)
        if historico is None:
            raise MalformedResumeEventError(
                "process instance not found in engine history", {}, code=REASON_RESUME_HISTORY_MISMATCH
            )
        if historico.business_key != event.business_key:
            raise MalformedResumeEventError(
                "engine history business key differs from the event", {}, code=REASON_RESUME_HISTORY_MISMATCH
            )
        if historico.process_key and historico.process_key != PROCESS_KEY_ESCALATION:
            raise MalformedResumeEventError(
                "engine history instance is not SP-OP-ESCALATION-001", {}, code=REASON_RESUME_HISTORY_MISMATCH
            )
        if historico.variables.get("resultado") != RESULTADO_DEVOLVIDO_AGENTE:
            raise MalformedResumeEventError(
                "engine history does not record resultado=devolvido_agente",
                {},
                code=REASON_RESUME_HISTORY_MISMATCH,
            )
        notas = historico.variables.get("notas_resolucao")
        if not isinstance(notas, str) or not notas.strip():
            raise MalformedResumeEventError(
                "engine history has no notas_resolucao to relay", {}, code=REASON_RESUME_HISTORY_MISMATCH
            )
        return notas


class RecipientResolver(Protocol):
    """Numero + ultima mensagem recebida de uma conversa, lidos da custodia cifrada (ADR-0061).

    `None` = a custodia nao tem numero para esta conversa (vencido pelo TTL, apagado por pedido
    LGPD): e' deterministico, vai para a fila morta. Uma falha de infraestrutura deve LEVANTAR.
    """

    async def lookup(self, *, tenant_id: str, conversation_id: str) -> RecipientRecord | None: ...


class TeamAlerter(Protocol):
    """Avisa a equipe humana de que a retomada NAO pode sair pelo WhatsApp (fora da janela)."""

    async def alert_outside_window(self, event: ProcessCompletedEvent) -> None: ...


class KafkaPublisherLike(Protocol):
    async def publish(
        self, topic: str, value: dict[str, Any], *, best_effort: bool | None = None
    ) -> bool: ...


#: O topico interno que o inbox de escalonamento consome (`notifications_inbox.py`).
NOTIFICATIONS_INTERNAL_TOPIC: Final[str] = "operadora.notifications.internal"
#: Fila que recebe o aviso de retomada fora da janela. A mesma de `solicitacao_humano`.
DEFAULT_ALERT_GROUP: Final[str] = "atendimento-humano"


class NotifyTeamAlerter:
    """Publica `escalation.notify_team` — o MESMO formato do worker `ST_NotificarTime`.

    So' identificadores e rotulos: `business_key` (o caso), `grupo_atendimento` (a fila) e
    `motivo_categoria=outro`. Nada da nota, nada do telefone. `best_effort=False`: se o aviso nao
    sai, a excecao propaga e o offset nao anda — um aviso perdido deixaria o beneficiario sem
    retorno e sem ninguem sabendo.
    """

    def __init__(
        self, publisher: KafkaPublisherLike, *, grupo_atendimento: str = DEFAULT_ALERT_GROUP
    ) -> None:
        self._publisher = publisher
        self._grupo = grupo_atendimento

    async def alert_outside_window(self, event: ProcessCompletedEvent) -> None:
        from maezo.tools.workers.escalation import NOTIFY_TEAM_NOTIFICATION_TYPE

        await self._publisher.publish(
            NOTIFICATIONS_INTERNAL_TOPIC,
            {
                "type": NOTIFY_TEAM_NOTIFICATION_TYPE,
                "tenant_id": event.tenant_id,
                "business_key": event.business_key,
                "grupo_atendimento": self._grupo,
                "severidade": None,
                "motivo_categoria": "outro",
            },
            best_effort=False,
        )


@runtime_checkable
class AgentResumer(Protocol):
    """A porta de retomada de UM agente. `HelenaDispatcher.resume` satisfaz esta forma."""

    async def resume(
        self, *, conversation_id: str, instrucoes: str, raw_to: str, resume_ref: str
    ) -> Mapping[str, Any]: ...


class ResumeOutcome(StrEnum):
    """O que o handler FEZ com um evento — so' fatos observados."""

    RESUMED = "resumed"
    OUTSIDE_WINDOW = "outside_window"
    SKIPPED_RESULTADO = "skipped_resultado"
    SKIPPED_NO_RESUMER = "skipped_no_resumer"


@dataclass(frozen=True, slots=True)
class ResumeReceipt:
    outcome: ResumeOutcome
    agent_id: str
    resultado: str
    desfecho: str = ""


#: Literal duplicado de `agents/helena/graph.py::DESFECHO_RETOMADA_FORA_DA_JANELA` (este modulo
#: nao importa o grafo no topo); `test_agent_resume.py` impede a divergencia.
DESFECHO_RETOMADA_FORA_DA_JANELA: Final[str] = "retomada_fora_da_janela"


def _emit_fora_da_janela(agent_id: str) -> None:
    from maezo.runtime.turn_telemetry import emit_turn_desfecho

    emit_turn_desfecho(
        {},
        agent_id=agent_id,
        desfecho=DESFECHO_RETOMADA_FORA_DA_JANELA,
        route="retomada",
        motivo_categoria=None,
        enviada=False,
    )


@dataclass
class ResumeHandler:
    """Evento -> filtro -> historico -> destinatario -> agente. Sem Kafka: totalmente testavel."""

    tenant_id: str
    instructions: HumanInstructionSource
    recipients: RecipientResolver
    resumers: Mapping[str, AgentResumer]
    alerter: TeamAlerter

    async def handle(self, value: Any) -> ResumeReceipt:
        event = parse_process_completed(value, tenant_id=self.tenant_id)
        if event.resultado != RESULTADO_DEVOLVIDO_AGENTE:
            # `resolvido_humano` / `emergencia_acionada` ENCERRAM o caso: nao acordam agente.
            logger.info(
                "agent_resume.skipped_resultado",
                tenant_id=event.tenant_id,
                agent_id=event.agent_id,
                resultado=event.resultado,
            )
            return ResumeReceipt(ResumeOutcome.SKIPPED_RESULTADO, event.agent_id, event.resultado)
        resumer = self.resumers.get(event.agent_id)
        if resumer is None:
            logger.warning(
                "agent_resume.no_resumer_for_agent",
                tenant_id=event.tenant_id,
                agent_id=event.agent_id,
                detail="este agente ainda nao tem porta de retomada; o caso foi devolvido e NINGUEM "
                "retomou a conversa",
            )
            return ResumeReceipt(ResumeOutcome.SKIPPED_NO_RESUMER, event.agent_id, event.resultado)
        instrucoes = await self.instructions.fetch(event)
        contato = await self.recipients.lookup(
            tenant_id=event.tenant_id, conversation_id=event.conversation_id
        )
        if contato is None or not contato.phone:
            raise MalformedResumeEventError(
                "recipient custody has no number for this conversation",
                {},
                code=REASON_RESUME_RECIPIENT_UNAVAILABLE,
            )
        if not dentro_da_janela(contato.last_inbound_at):
            # Janela da Meta fechada: NADA sai pelo WhatsApp. Avisa a equipe (propaga se falhar)
            # e so' depois conta o desfecho — um aviso que nao saiu nao pode ser contado.
            await self.alerter.alert_outside_window(event)
            _emit_fora_da_janela(event.agent_id)
            logger.warning(
                "agent_resume.outside_meta_window",
                tenant_id=event.tenant_id,
                agent_id=event.agent_id,
                conversation_id=event.conversation_id,
            )
            return ResumeReceipt(
                ResumeOutcome.OUTSIDE_WINDOW,
                event.agent_id,
                event.resultado,
                DESFECHO_RETOMADA_FORA_DA_JANELA,
            )
        resultado = await resumer.resume(
            conversation_id=event.conversation_id,
            instrucoes=instrucoes,
            raw_to=contato.phone,
            resume_ref=event.process_instance_id,
        )
        desfecho = str(resultado.get("desfecho") or "")
        logger.info(
            "agent_resume.resumed",
            tenant_id=event.tenant_id,
            agent_id=event.agent_id,
            conversation_id=event.conversation_id,
            desfecho=desfecho,
        )
        return ResumeReceipt(ResumeOutcome.RESUMED, event.agent_id, event.resultado, desfecho)


# ---------------------------------------------------------------------------
# O laco — a mesma forma de `notifications_bridge.run_consumer_loop` / `run_inbox_loop`.
# ---------------------------------------------------------------------------


async def run_resume_loop(
    consumer: BridgeKafkaConsumer,
    handler: ResumeHandler,
    *,
    stop_event: asyncio.Event | None = None,
    dlq: BridgeDlqShunt | None = None,
) -> None:
    """Dirige `handler.handle` sobre cada mensagem. Commit SO' depois de um desfecho observado.

    A ORDEM E' A GARANTIA. `consumer.commit()` so' e' alcancado depois de: retomada concluida,
    evento filtrado/ignorado, ou (com `dlq`) malformacao CONFIRMADA na fila morta com fato de
    auditoria. O braco da fila morta e' estreito como nos irmaos: SO' `MalformedBridgeMessageError`
    (deterministico). Motor fora, envio falho, checkpoint falho — tudo o mais PROPAGA e o offset
    fica. Com `dlq=None` a malformacao tambem propaga e nada e' confirmado.
    """
    async for message in consumer:
        try:
            if message.parse_error:
                raise MalformedResumeEventError(
                    message.parse_error, message.raw, code=message.parse_code or REASON_NOT_A_JSON_OBJECT
                )
            receipt = await handler.handle(message.value)
        except MalformedBridgeMessageError as exc:
            if dlq is None:
                raise
            await dlq.shunt(message, exc)
        else:
            logger.info("agent_resume.dispatched", outcome=str(receipt.outcome), agent_id=receipt.agent_id)
        await consumer.commit()
        if stop_event is not None and stop_event.is_set():
            break


# ---------------------------------------------------------------------------
# Composicao
# ---------------------------------------------------------------------------


class AgentResumeSettings(BaseSettings):
    """Config do consumidor. Os deps da Helena vem de `WhatsAppWebhookSettings` (mesma raiz)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    tenant_id: str = Field(default="amh", validation_alias=AliasChoices("TENANT_ID", "tenant_id"))
    database_url: str | None = Field(
        default=None, validation_alias=AliasChoices("DATABASE_URL", "database_url")
    )
    kafka_bootstrap_servers: str = Field(
        default="localhost:9092",
        validation_alias=AliasChoices("KAFKA_BOOTSTRAP_SERVERS", "kafka_bootstrap_servers"),
    )
    kafka_topic: str = Field(
        default=PROCESS_COMPLETED_TOPIC,
        validation_alias=AliasChoices("AGENT_RESUME_KAFKA_TOPIC", "kafka_topic"),
    )
    kafka_group_id: str = Field(
        default=DEFAULT_RESUME_CONSUMER_GROUP_ID,
        validation_alias=AliasChoices("AGENT_RESUME_KAFKA_GROUP_ID", "kafka_group_id"),
    )
    #: A chave KMS da custodia (ADR-0061). Ausente = o daemon nao sobe.
    recipient_vault_kms_key_arn: str | None = Field(
        default=None,
        validation_alias=AliasChoices("RECIPIENT_VAULT_KMS_KEY_ARN", "recipient_vault_kms_key_arn"),
    )
    recipient_vault_ttl_days: int = Field(
        default=DEFAULT_TTL_DAYS,
        validation_alias=AliasChoices("RECIPIENT_VAULT_TTL_DAYS", "recipient_vault_ttl_days"),
    )


def build_recipient_resolver(settings: AgentResumeSettings) -> RecipientResolver:
    """A custodia cifrada de producao (ADR-0061). Recusa, alto, se nao estiver configurada."""
    if not settings.recipient_vault_kms_key_arn or not settings.database_url:
        raise RecipientCustodyUnavailableError(
            f"agent_resume[{settings.tenant_id}]: recipient custody is not configured "
            "(RECIPIENT_VAULT_KMS_KEY_ARN / DATABASE_URL) — the WhatsApp send needs the number and "
            "only the encrypted vault of ADR-0061 holds it. Refusing to start."
        )
    from maezo.gateway.recipient_custody import AwsKmsKeyWrapper, PostgresRecipientVault

    return PostgresRecipientVault(
        dsn=settings.database_url,
        tenant=settings.tenant_id,
        # Regiao: a do proprio runtime (o Fargate injeta AWS_REGION; o boto3 a le sozinho).
        wrapper=AwsKmsKeyWrapper(key_arn=settings.recipient_vault_kms_key_arn),
        ttl_days=settings.recipient_vault_ttl_days,
    )


async def main() -> None:  # pragma: no cover - composition root, exercised by its parts
    """Sobe o laco ate' SIGTERM/SIGINT. Espelha `notifications_bridge.main()`.

    A Helena e' montada pela MESMA raiz do receptor do webhook (`webhooks.service._build_dispatcher`
    + `_provision_dispatch_checkpointer`): mesmos seams cercados, mesmo checkpointer duravel,
    mesma chave de idempotencia de saida. Em producao, checkpointer indisponivel = nao sobe.
    """
    from maezo.gateway.audit_postgres import PostgresAuditSink
    from maezo.platform.integrations.events_kafka_producer import build_notifications_publisher
    from maezo.platform.webhooks.service import (
        WebhookState,
        _build_dispatcher,
        _provision_dispatch_checkpointer,
    )
    from maezo.platform.webhooks.whatsapp.settings import WhatsAppWebhookSettings

    settings = AgentResumeSettings()
    if not settings.database_url:
        raise RuntimeError(
            "agent_resume: DATABASE_URL is required — the dead-letter audit, the checkpoint and the "
            "outbound dedup all live there"
        )
    recipients = build_recipient_resolver(settings)  # fail-closed without the ADR-0061 vault

    webhook_settings = WhatsAppWebhookSettings()  # type: ignore[call-arg]  # env-required fields
    state = WebhookState(settings=webhook_settings)
    state.dispatcher, transport = _build_dispatcher(webhook_settings)
    await _provision_dispatch_checkpointer(state)
    if state.dispatcher is None:
        raise RuntimeError(f"agent_resume: Helena dispatcher unavailable: {state.dispatcher_error}")
    handler = ResumeHandler(
        tenant_id=settings.tenant_id,
        instructions=EngineHistoryInstructionSource(state.dispatcher.cibseven),
        recipients=recipients,
        resumers={"helena": state.dispatcher},
        alerter=NotifyTeamAlerter(build_notifications_publisher(settings.kafka_bootstrap_servers)),
    )
    # The DLQ publisher is a fenced effect class (§8.1): built ONLY through the bridge's own
    # sanctioned constructor (`build_dlq_shunt`), exactly as the bridge's own root does.
    dlq = build_dlq_shunt(
        NotificationsBridgeSettings(
            TENANT_ID=settings.tenant_id, KAFKA_BOOTSTRAP_SERVERS=settings.kafka_bootstrap_servers
        ),
        PostgresAuditSink(settings.database_url, settings.tenant_id),
    )
    consumer: BridgeKafkaConsumer = AioKafkaBridgeConsumer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        topic=settings.kafka_topic,
        group_id=settings.kafka_group_id,
    )
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop_event.set)
    await consumer.start()
    await dlq.publisher.start()
    logger.info(
        "agent_resume.started",
        topic=settings.kafka_topic,
        group_id=settings.kafka_group_id,
        tenant_id=settings.tenant_id,
        dlq_topic=dlq_topic_for(settings.kafka_topic),
    )
    try:
        await run_resume_loop(consumer, handler, stop_event=stop_event, dlq=dlq)
    finally:
        await consumer.stop()
        with contextlib.suppress(Exception):
            await dlq.publisher.stop()
        await transport.close()
        if state.checkpointer is not None:
            with contextlib.suppress(Exception):
                await state.checkpointer.aclose()
        logger.info("agent_resume.stopped")


if __name__ == "__main__":  # pragma: no cover - process entry point
    asyncio.run(main())
