"""Poller do historico do engine: decisoes humanas nas User Tasks L1 (R-085, F-090).

Decisao do dono 2026-10-31 (issue #1084, Rodada 2 "Observabilidade"; analise
`OBS-HITL-metric-and-alerts.md`, opcao 3): a "taxa de aprovacao humana de propostas L1" de ADR-0010
e medida por um poller do HISTORICO do engine — um campo de decisao por tarefa, rotulos fechados —
e o alerta de queda compara a taxa com o proprio baseline, em dias, severidade `warning`.

POR QUE O HISTORICO DO ENGINE E NAO O PORTAL/RELE: as tarefas L1 sao completadas hoje no Tasklist
do CIB Seven (`docs/runbooks/devops-stack.md`); o portal nao cobre nenhuma decisao L1 e o rele D6
nao existe (issue de rastreio aberta neste mesmo pacote). O historico ve TODO canal. Por isso esta
e a UNICA fonte de `maezo_l1_decision_total`: emitir tambem no portal/rele contaria duas vezes.

O QUE E LIDO, E SO ISSO (mesma disciplina do DL-0050, "exatamente N variaveis pelo nome"):
  1. `GET /history/task` — tarefas ENCERRADAS das chaves de `L1_DECISION_TASKS`, por cursor de
     `endTime`;
  2. para cada tarefa concluida, `GET /history/variable-instance?variableName=<campo>` da instancia
     e `GET /history/detail?variableInstanceId=..&activityInstanceId=<da tarefa>` — o valor que a
     PROPRIA tarefa gravou no seu campo de decisao (medido no motor local: o detalhe carrega o
     `activityInstanceId` da User Task, nao o `taskId`). Nenhuma justificativa e lida pelo nome;
     nada do payload e registrado em log.

ROTULOS (ADR-0006): `tenant` (do daemon), `agent_id`/`action_class` (tabela estatica abaixo — o
agente que instrui o dossie e a chave de `spec/policies/autonomy/L0-core.yaml` que a tarefa
autoriza), `decisao` (token BPMN declarado da tarefa | `sem_decisao` | `desconhecido`). A tabela
NAO decide nada: ela so nomeia o que o BPMN ja declara, e
`tests/unit/runtime/test_engine_metrics_poller.py` a confere contra o BPMN e o L0-core a cada run.

LIMITES DECLARADOS (dark, `MAEZO_L1_DECISION_POLLER` default off):
  * cursor EM MEMORIA, iniciado no boot: decisoes durante um restart nao sao contadas (sub-conta,
    nunca dupla conta) e nao ha backfill do historico — o baseline nasce do trafego apos ligar.
  * UMA replica por tenant: duas replicas do worker-daemon com o poller ligado contam duas vezes.
    Um cursor duravel/singleton (Postgres) e trabalho seguinte, nao feito aqui.
  * o nivel de historico do engine precisa registrar `HistoricDetail` (FULL; e o do motor local —
    medido). A limpeza de historico (TTL) abaixo do intervalo de poll apagaria tarefas antes da
    leitura.
  * `sem_decisao` junta timer interruptivo e cancelamento da instancia (`deleteReason !=
    completed`); a reentrada por coordenacao (prorrogar/seguir_analise) e uma NOVA tarefa e conta
    de novo — e o que o engine registrou.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Protocol

import structlog

from maezo.platform.observability import (
    L1_DECISAO_DESCONHECIDA,
    L1_DECISAO_SEM_DECISAO,
    preinit_l1_decisions,
    record_engine_metrics_poll,
    record_l1_decision,
)
from maezo.runtime.dependency_failures import EXTERNAL_DEPENDENCY_FAILURES, PROGRAMMING_ERRORS

logger = structlog.get_logger(__name__)

__all__ = [
    "DEFAULT_PROCESS_KEYS",
    "L1_DECISION_TASKS",
    "EngineHistoryReader",
    "EngineMetricsPoller",
    "L1DecisionTask",
]


class EngineHistoryShapeError(RuntimeError):
    """The engine answered, but not with the documented history shape (a failed round, retried)."""


class EngineHistoryReader(Protocol):
    """The one capability the poller needs: a read-only history GET.

    Satisfied by `tools.workers.harness.CibSevenWorkerTransport.read_history` (closed path set);
    the poller never builds an HTTP client of its own."""

    async def read_history(self, path: str, params: Mapping[str, str]) -> Any: ...


@dataclass(frozen=True)
class L1DecisionTask:
    """One L1 approval User Task and the ONE decision field it writes (declared in its BPMN)."""

    process_key: str
    task_key: str
    decision_variable: str
    agent_id: str
    action_class: str
    vocabulary: frozenset[str]
    #: A token whose decision belongs to a DIFFERENT L0-core class than the task's own (NIP
    #: `MANTER_NEGATIVA` is `nip_manter_negativa`, L0 hard, born only in this UT — L0-core.yaml).
    action_class_by_token: Mapping[str, str] = field(default_factory=dict)

    def action_class_for(self, decisao: str) -> str:
        return self.action_class_by_token.get(decisao, self.action_class)


#: The closed table. Each row traces to its BPMN UT documentation (`decisao_* in {...}`) and to the
#: L1 action of `spec/policies/autonomy/L0-core.yaml` the UT is the human gate for; the agent is the
#: one whose dossier instructs the UT ("instrui, nao decide"). LGPD `UT_RevisaoDpo` is deliberately
#: absent: its package is compiled by a worker, not proposed by an agent.
L1_DECISION_TASKS: Final[tuple[L1DecisionTask, ...]] = (
    L1DecisionTask(
        process_key="SP-OP-PAGTO-001",
        task_key="UT_AprovacaoAlcada",
        decision_variable="decisao_pagamento",
        agent_id="andre",
        action_class="high_value_payment",
        vocabulary=frozenset({"APROVAR", "RECUSAR", "SOLICITAR_INFO", "CANCELAR"}),
    ),
    L1DecisionTask(
        process_key="SP-OP-ANS-SUBMIT-001",
        task_key="UT_RevisarEnvio",
        decision_variable="decisao_envio",
        agent_id="gustavo",
        action_class="ans_official_submission",
        vocabulary=frozenset({"APROVAR_ENVIO", "CORRIGIR_PENDENCIA", "ADIAR_ENVIO"}),
    ),
    L1DecisionTask(
        process_key="SP-OP-CRED-001",
        task_key="UT_AnaliseDescredenciamento",
        decision_variable="decisao_cred",
        agent_id="carolina",
        action_class="provider_decredentialing",
        vocabulary=frozenset({"DESCREDENCIAR", "MANTER", "SOLICITAR_INFO"}),
    ),
    L1DecisionTask(
        process_key="SP-OP-NIP-001",
        task_key="UT_RevisaoJuridicaNip",
        decision_variable="decisao_nip",
        agent_id="gustavo",
        action_class="nip_response",
        vocabulary=frozenset({"MANTER_NEGATIVA", "CONCEDER", "RESPONDER_NAO_ASSISTENCIAL", "SOLICITAR_INFO"}),
        action_class_by_token={"MANTER_NEGATIVA": "nip_manter_negativa"},
    ),
    L1DecisionTask(
        process_key="SP-OP-ADEQUACAO-001",
        task_key="UT_DecisaoFallback",
        decision_variable="decisao_remediacao",
        agent_id="andre",
        action_class="adequacao_fallback_commitment",
        vocabulary=frozenset({"MONITORAR_OK", "ENCAMINHAR_CRED", "COMPROMISSO_FALLBACK", "SOLICITAR_INFO"}),
    ),
)

#: The process keys whose history this poller reads (derived, never hand-kept apart).
DEFAULT_PROCESS_KEYS: Final[frozenset[str]] = frozenset(t.process_key for t in L1_DECISION_TASKS)

#: Engine REST date format (`yyyy-MM-dd'T'HH:mm:ss.SSSZ`), e.g. `2026-10-31T01:38:12.975+0000`.
_ENGINE_DATE_FORMAT: Final[str] = "%Y-%m-%dT%H:%M:%S.%f%z"
_COMPLETED: Final[str] = "completed"
_ONE_MS: Final[timedelta] = timedelta(milliseconds=1)
#: What a failed round absorbs (and retries next round): the shared external-dependency vocabulary
#: plus `ValueError` (malformed JSON body or engine date). Programming errors always propagate.
_ROUND_FAILURES: Final[tuple[type[Exception], ...]] = (*EXTERNAL_DEPENDENCY_FAILURES, ValueError)


def format_engine_date(moment: datetime) -> str:
    utc = moment.astimezone(UTC)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}+0000"


def parse_engine_date(value: str) -> datetime:
    return datetime.strptime(value, _ENGINE_DATE_FORMAT).astimezone(UTC)


def label_sets(tasks: Iterable[L1DecisionTask] = L1_DECISION_TASKS) -> frozenset[tuple[str, str, str]]:
    """Every closed (agent_id, action_class, decisao) the poller can emit — for pre-initialisation."""
    out: set[tuple[str, str, str]] = set()
    for task in tasks:
        for decisao in (*task.vocabulary, L1_DECISAO_SEM_DECISAO, L1_DECISAO_DESCONHECIDA):
            out.add((task.agent_id, task.action_class_for(decisao), decisao))
    return frozenset(out)


class EngineMetricsPoller:
    """Counts each finished L1 User Task exactly once per process lifetime (see module limits)."""

    def __init__(
        self,
        reader: EngineHistoryReader,
        *,
        tenant: str,
        interval_s: float,
        tasks: Iterable[L1DecisionTask] = L1_DECISION_TASKS,
        page_size: int = 100,
        max_pages_per_round: int = 10,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if interval_s <= 0:
            raise ValueError(f"interval_s must be positive, got {interval_s}")
        self._reader = reader
        self._tenant = tenant
        self._interval_s = interval_s
        self._tasks = {(t.process_key, t.task_key): t for t in tasks}
        self._task_keys = ",".join(sorted({t.task_key for t in self._tasks.values()}))
        self._page_size = page_size
        self._max_pages = max_pages_per_round
        self._cursor = clock()
        #: Counted task id -> its endTime, kept only inside the 1 ms overlap window the next query
        #: re-reads (`finishedAfter = cursor - 1 ms`) — the de-duplication set, never unbounded.
        self._seen: dict[str, datetime] = {}
        preinit_l1_decisions(tenant=tenant, label_sets=label_sets(self._tasks.values()))

    @property
    def cursor(self) -> datetime:
        return self._cursor

    async def run(self) -> None:
        """Poll forever; cancelled by the daemon's drain. A failed round never stops the loop."""
        while True:
            await self.poll_once()
            await asyncio.sleep(self._interval_s)

    async def poll_once(self) -> int:
        """One round. Returns how many tasks were counted; the cursor only moves past a counted task."""
        counted = 0
        floor = self._cursor - _ONE_MS
        try:
            first = 0
            for _ in range(self._max_pages):
                page = await self._page(floor, first)
                for item in page:
                    if self._is_new(item, floor):
                        await self._count(item)
                        counted += 1
                if len(page) < self._page_size:
                    break
                first += len(page)
        except PROGRAMMING_ERRORS:
            raise
        except _ROUND_FAILURES as exc:
            # Engine down/HTTP error (`httpx.HTTPError`), malformed body or date (`ValueError`), or an
            # unexpected history shape (`EngineHistoryShapeError`). Never the payload: only the
            # exception class. The cursor stays at the last counted task; the next round retries.
            logger.warning("engine_metrics_poll_failed", error_type=type(exc).__name__, counted=counted)
            record_engine_metrics_poll(ok=False)
            return counted
        finally:
            self._prune()
        record_engine_metrics_poll(ok=True)
        return counted

    async def _page(self, floor: datetime, first: int) -> list[Mapping[str, Any]]:
        params = {
            "finished": "true",
            "taskDefinitionKeyIn": self._task_keys,
            # 1 ms before the cursor: inclusive whatever the engine's comparison; the overlap is
            # de-duplicated by `_seen`. Pages within a round share the same floor (`firstResult`).
            "finishedAfter": format_engine_date(floor),
            "sortBy": "endTime",
            "sortOrder": "asc",
            "firstResult": str(first),
            "maxResults": str(self._page_size),
        }
        body = await self._reader.read_history("/history/task", params)
        if not isinstance(body, list):
            raise EngineHistoryShapeError("history/task did not return a list")
        return [t for t in body if isinstance(t, Mapping)]

    def _is_new(self, item: Mapping[str, Any], floor: datetime) -> bool:
        end = item.get("endTime")
        if not isinstance(end, str) or parse_engine_date(end) < floor:
            return False
        return str(item.get("id")) not in self._seen

    async def _count(self, item: Mapping[str, Any]) -> None:
        spec = self._tasks.get((str(item.get("processDefinitionKey")), str(item.get("taskDefinitionKey"))))
        if spec is not None:
            if item.get("deleteReason") == _COMPLETED:
                decisao = await self._read_decision(spec, item)
            else:
                decisao = L1_DECISAO_SEM_DECISAO
            record_l1_decision(
                tenant=self._tenant,
                agent_id=spec.agent_id,
                action_class=spec.action_class_for(decisao),
                decisao=decisao,
            )
        ended = parse_engine_date(str(item["endTime"]))
        self._seen[str(item.get("id"))] = ended
        self._cursor = max(self._cursor, ended)

    def _prune(self) -> None:
        floor = self._cursor - _ONE_MS
        self._seen = {tid: end for tid, end in self._seen.items() if end >= floor}

    async def _read_decision(self, spec: L1DecisionTask, item: Mapping[str, Any]) -> str:
        instances = await self._reader.read_history(
            "/history/variable-instance",
            {
                "processInstanceId": str(item.get("processInstanceId")),
                "variableName": spec.decision_variable,
                "deserializeValues": "false",
            },
        )
        latest: tuple[str, Any] | None = None
        for instance in instances if isinstance(instances, list) else []:
            if not isinstance(instance, Mapping) or instance.get("name") != spec.decision_variable:
                continue
            details = await self._reader.read_history(
                "/history/detail",
                {
                    "variableInstanceId": str(instance.get("id")),
                    "activityInstanceId": str(item.get("activityInstanceId")),
                    "variableUpdates": "true",
                    "deserializeValues": "false",
                },
            )
            for detail in details if isinstance(details, list) else []:
                if not isinstance(detail, Mapping) or detail.get("variableName") != spec.decision_variable:
                    continue
                stamp = str(detail.get("time") or "")
                if latest is None or stamp >= latest[0]:
                    latest = (stamp, detail.get("value"))
        if latest is None:
            return L1_DECISAO_DESCONHECIDA
        value = latest[1]
        return value if isinstance(value, str) and value in spec.vocabulary else L1_DECISAO_DESCONHECIDA
