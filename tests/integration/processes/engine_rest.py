"""Cliente REST minimo do CIB Seven para os testes de processo (engine REAL, ADR-0011).

Ported verbatim from the v1 donor (Maezo-Healthcare-Plan
`tests/integration/processes/engine_rest.py`) — T3.1 phase 1 (V2-COMPLETION-PLAN §3). No
maezo import in this module, so nothing here needed adaptation: it is a thin, self-contained
httpx wrapper over the CIB Seven `engine-rest` API and every method/dataclass name is preserved
byte-identical (design §16.1's donor fixture-surface preservation).

Nao mocka o engine (AGENTS.md regra 3 / CONTRIBUTING §3). Encapsula apenas as operacoes
que um teste de integracao precisa contra o `engine-rest` do dev-stack:

- deploy de BPMN/DMN da arvore;
- start de instancia com business key (espelha `mcp-cibseven.start_process`, mas direto no
  engine — o teste e o "agente de origem");
- consulta de User Tasks (id, candidate group) e **completar User Task como humano sintetico**
  (esse e o caminho HITL — permitido: o teste age como o humano do grupo roteado);
- **execucao de jobs de timer** (`/job/{id}/execute`) — nunca `sleep`. O test-spec exige
  disparar o timer via job execution, e os SLAs vem da DMN, entao injetamos duracoes ISO
  minusculas via input do start (a DMN devolve as duracoes; o BPMN usa `${roteamento.sla_*}`).

  IMPORTANTE sobre timers: o BPMN le `${roteamento.sla_ack}`/`${roteamento.sla_resolucao}`
  da SAIDA da DMN. Para nao depender de relogio, o teste NAO espera o timer: ele localiza o
  job do timer pendente da instancia e o executa imediatamente via API de gerenciamento. A
  duracao ISO so importa para o engine agendar; ao executar o job na marra, o ramo dispara
  independentemente do valor. Mantemos as duracoes reais da DMN (sem override) porque a
  tecnica de job-execution torna o override desnecessario — documentado aqui para o revisor.

  `TimerJob.due_date` (campo `dueDate` do DTO de Job) expoe o instante agendado do timer SEM
  disparar o job — usado por testes que precisam PROVAR a ancora do deadline antes de forcar
  a execucao via `execute_job`.

Tudo assincrono (httpx). Sem dependencia de PR pendente: fala REST cru com o engine.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


@dataclass(frozen=True, slots=True)
class UserTask:
    """User Task aberta no engine (subset relevante para asserts HITL)."""

    id: str
    task_definition_key: str
    name: str
    process_instance_id: str
    candidate_groups: frozenset[str]


@dataclass(frozen=True, slots=True)
class TimerJob:
    """Job de timer pendente (boundary timer de uma User Task).

    `due_date` (campo `dueDate` do DTO de Job) expoe o instante agendado do timer SEM
    dispara-lo — usado por testes que precisam PROVAR a ancora do deadline antes de forcar a
    execucao via `execute_job`.
    """

    id: str
    activity_id: str  # ex.: BT_SlaAck / BT_SlaResolucao
    due_date: str | None = None  # ISO 8601 (campo `dueDate` do DTO de Job do CIB Seven)


class EngineRestError(RuntimeError):
    """Falha na comunicacao com o engine REST."""


class EngineRest:
    """Wrapper REST fino sobre o CIB Seven `engine-rest` (engine real)."""

    def __init__(self, base_url: str, *, timeout: float = 20.0) -> None:
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> EngineRest:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # --- disponibilidade ---------------------------------------------------------------

    async def is_available(self) -> bool:
        try:
            resp = await self._client.get("/version")
            return resp.status_code < 500
        except httpx.RequestError:
            return False

    # --- deploy ------------------------------------------------------------------------

    async def deploy(self, *paths: Path, name: str) -> str:
        """Deploya um ou mais artefatos (BPMN/DMN) num unico deployment."""
        files = []
        handles = []
        try:
            for p in paths:
                fh = p.open("rb")
                handles.append(fh)
                files.append((p.name, (p.name, fh, "application/xml")))
            data = {"deployment-name": name, "enable-duplicate-filtering": "true"}
            resp = await self._client.post("/deployment/create", files=files, data=data)
        finally:
            for fh in handles:
                fh.close()
        if resp.status_code not in (200, 201):
            raise EngineRestError(f"deploy `{name}` falhou [{resp.status_code}]: {resp.text[:300]}")
        return str(resp.json()["id"])

    # --- start -------------------------------------------------------------------------

    # Limites do int32 do Java (java.lang.Integer). Centavos de BRL podem exceder este teto em
    # pagamentos de alto valor (R$ 21.474.836,47 = 2.147.483.647 centavos e o maximo do Integer); um
    # pagamento de R$ 50MM = 5.000.000.000 centavos estoura o int32. Inteiros fora desta faixa DEVEM
    # ser enviados como Camunda `Long` (int64) — caso contrario o engine recusa a instanciacao com
    # "Cannot convert value '<n>' of type 'Integer' to java type java.lang.Integer" (ADR-0018 parte 2,
    # landmine money-in-cents). Mesma defesa aplicada no harness dos workers (_to_camunda_var).
    _JAVA_INT32_MIN = -(2**31)
    _JAVA_INT32_MAX = 2**31 - 1

    @classmethod
    def _to_camunda_vars(cls, variables: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in variables.items():
            # Valor ja no formato de variavel Camunda ({"value": ..., "type": ...}) -> passa direto
            # (permite ao chamador forcar um tipo explicito, ex.: {"value": n, "type": "Long"}).
            if isinstance(v, dict) and "value" in v:
                out[k] = v
            elif isinstance(v, bool):
                out[k] = {"value": v, "type": "Boolean"}
            elif isinstance(v, int):
                # Inteiro que cabe no int32 -> Integer; alem disso -> Long (int64). Centavos de alto
                # valor (> ~2.1e9) DEVEM ser Long para nao estourar java.lang.Integer.
                fits_int32 = cls._JAVA_INT32_MIN <= v <= cls._JAVA_INT32_MAX
                out[k] = {"value": v, "type": "Integer" if fits_int32 else "Long"}
            elif isinstance(v, float):
                # Espelha harness.py::_to_camunda_var (mapper canonico, T1.1): float -> Double,
                # NUNCA String. Um guard numerico `<= 0` da DMN/BPMN recebe uma str e quebra
                # (TypeError) se o float for tipado como String — o defeito que este ramo corrige.
                out[k] = {"value": v, "type": "Double"}
            elif isinstance(v, dict | list):
                # Espelha harness.py::_to_camunda_var: dict/list "cru" (sem chave "value") vira
                # `Json` via json.dumps identico byte-a-byte ao harness (ensure_ascii=False,
                # default=str), nunca Python repr(). Um dict COM "value" ja foi tratado no
                # passthrough acima; so um dict/list sem essa chave cai aqui.
                out[k] = {"value": json.dumps(v, ensure_ascii=False, default=str), "type": "Json"}
            else:
                out[k] = {"value": v if v is None else str(v), "type": "String"}
        return out

    async def start_by_key(
        self, process_key: str, business_key: str, variables: dict[str, Any]
    ) -> dict[str, Any]:
        """Start direto no engine (o teste age como o agente de origem)."""
        payload = {"businessKey": business_key, "variables": self._to_camunda_vars(variables)}
        resp = await self._client.post(f"/process-definition/key/{process_key}/start", json=payload)
        if resp.status_code not in (200, 201):
            raise EngineRestError(f"start `{process_key}` falhou [{resp.status_code}]: {resp.text[:300]}")
        return dict(resp.json())

    async def find_active_instances(self, business_key: str) -> list[dict[str, Any]]:
        resp = await self._client.get(
            "/process-instance", params={"businessKey": business_key, "active": "true"}
        )
        resp.raise_for_status()
        return list(resp.json())

    async def instance_is_active(self, instance_id: str) -> bool:
        resp = await self._client.get(f"/process-instance/{instance_id}")
        return resp.status_code == 200

    async def history_state(self, instance_id: str) -> str:
        resp = await self._client.get(f"/history/process-instance/{instance_id}")
        resp.raise_for_status()
        return str(resp.json().get("state", "UNKNOWN"))

    async def activity_instances_ended(self, instance_id: str) -> set[str]:
        """Atividades historicas concluidas (para asserir qual End event foi atingido)."""
        resp = await self._client.get(
            "/history/activity-instance",
            params={"processInstanceId": instance_id},
        )
        resp.raise_for_status()
        return {str(a["activityId"]) for a in resp.json()}

    async def incidents(self, instance_id: str) -> list[dict[str, Any]]:
        """Incidentes ABERTOS da instancia (`GET /incident?processInstanceId=...`).

        Um BPMN error lancado por um worker (bpmnError) que NAO tem catch correspondente
        vira um incidente travado; com o boundary error certo, a lista fica vazia. Usado
        para asserir que erros L0-guard sao CAPTURADOS (nenhum incidente pendente) — ou,
        onde o v2 harness reporta o guard como `failure`/incidente diretamente (design
        §9), para observar esse incidente real.
        """
        resp = await self._client.get("/incident", params={"processInstanceId": instance_id})
        resp.raise_for_status()
        return list(resp.json())

    # --- User Tasks (HITL) -------------------------------------------------------------

    async def _candidate_groups(self, task_id: str) -> frozenset[str]:
        resp = await self._client.get(f"/task/{task_id}/identity-links", params={"type": "candidate"})
        resp.raise_for_status()
        return frozenset(str(link["groupId"]) for link in resp.json() if link.get("groupId"))

    async def list_user_tasks(self, instance_id: str) -> list[UserTask]:
        resp = await self._client.get("/task", params={"processInstanceId": instance_id})
        resp.raise_for_status()
        tasks: list[UserTask] = []
        for t in resp.json():
            tasks.append(
                UserTask(
                    id=str(t["id"]),
                    task_definition_key=str(t.get("taskDefinitionKey", "")),
                    name=str(t.get("name", "")),
                    process_instance_id=str(t.get("processInstanceId", "")),
                    candidate_groups=await self._candidate_groups(str(t["id"])),
                )
            )
        return tasks

    async def await_user_task(
        self, instance_id: str, task_definition_key: str, *, attempts: int = 40, delay: float = 0.25
    ) -> UserTask:
        """Espera (poll curto, nao sleep cego) a User Task surgir apos service tasks async."""
        for _ in range(attempts):
            for task in await self.list_user_tasks(instance_id):
                if task.task_definition_key == task_definition_key:
                    return task
            await asyncio.sleep(delay)
        raise EngineRestError(f"User Task `{task_definition_key}` nao apareceu na instancia {instance_id}")

    async def complete_task_as_human(self, task_id: str, variables: dict[str, Any]) -> None:
        """Completa a User Task como o humano sintetico do grupo roteado (caminho HITL)."""
        payload = {"variables": self._to_camunda_vars(variables)}
        resp = await self._client.post(f"/task/{task_id}/complete", json=payload)
        if resp.status_code not in (200, 204):
            raise EngineRestError(f"complete task {task_id} falhou [{resp.status_code}]: {resp.text[:300]}")

    # --- variaveis de processo ---------------------------------------------------------

    async def get_variable(self, instance_id: str, name: str, *, deserialize: bool = True) -> Any:
        """Le o VALOR atual de uma variavel de processo de uma instancia ATIVA.

        `deserialize=True` (default, byte-identical ao comportamento anterior desta assinatura —
        nenhum call site existente muda) pede ao engine `deserializeValue=true` (o default do
        Camunda/CIB Seven REST). Para uma variavel `Json` SPIN-tipada, isso serializa o BEAN
        `SpinJsonNode` por introspecao Jackson (`{'array': True, 'nodeType': 'ARRAY',
        'dataFormatName': 'application/json', ...}`), NAO o valor deserializado — os dados reais
        nao sobrevivem nesse shape (nao ha campo recuperavel com a lista/dict original; e um
        defeito conhecido do REST do Camunda para SpinJsonNode via GET runtime variable). Passe
        `deserialize=False` para pedir `deserializeValue=false`: o engine devolve a string JSON
        CRUA (a mesma serializacao que `_to_camunda_vars` produziu ao enviar), decodificavel via
        `json.loads`.
        """
        params = {} if deserialize else {"deserializeValue": "false"}
        resp = await self._client.get(f"/process-instance/{instance_id}/variables/{name}", params=params)
        if resp.status_code != 200:
            raise EngineRestError(
                f"get variable `{name}` da instancia {instance_id} falhou "
                f"[{resp.status_code}]: {resp.text[:300]}"
            )
        return resp.json().get("value")

    async def get_history_variable(self, instance_id: str, name: str) -> Any:
        """Le o VALOR historico de uma variavel de processo (instancia ATIVA ou JA CONCLUIDA).

        `get_variable` acima usa o endpoint de RUNTIME (`/process-instance/{id}/variables/{name}`),
        que devolve HTTP 500 "execution is null" assim que a instancia atinge um end event — a
        execucao raiz deixa de existir (t3.1-test-hygiene-batch finding: `_CANCEL_COMPLETED_
        RUNTIME_VAR_GAP`). O endpoint de HISTORICO (`GET /history/variable-instance`) permanece
        consultavel indefinidamente apos o fim da instancia (mesma garantia de `history_state`/
        `activity_instances_ended` acima) — use este metodo para ler uma variavel de processo de
        uma instancia que ja pode ter terminado. `processInstanceIdIn` (lista) e o parametro real
        da API de historico do Camunda/CIB Seven (nao `processInstanceId`, que essa API nao aceita).
        Levanta `EngineRestError` se a variavel nao aparecer no historico da instancia (nome
        errado / nunca setada) — sem "variavel ausente" silenciosa.
        """
        resp = await self._client.get(
            "/history/variable-instance",
            params={"processInstanceIdIn": instance_id, "variableName": name},
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            raise EngineRestError(f"history variable `{name}` nao encontrada para a instancia {instance_id}")
        return rows[0].get("value")

    # --- jobs de timer (sem sleep) -----------------------------------------------------

    async def _job_definition_activities(self, instance_id: str) -> dict[str, str]:
        """Mapa jobDefinitionId -> activityId para a definicao da instancia.

        O DTO de Job do CIB Seven (GET /job) NAO expoe `activityId` (so `jobDefinitionId`
        e `failedActivityId`, este ultimo nulo enquanto o job nao falha). A atividade do
        boundary timer so e obtida via job-definition. Consultamos por processDefinitionId
        (estavel para a instancia) e indexamos jobDefinitionId -> activityId.
        """
        inst = await self._client.get(f"/process-instance/{instance_id}")
        inst.raise_for_status()
        proc_def_id = str(inst.json()["definitionId"])
        resp = await self._client.get("/job-definition", params={"processDefinitionId": proc_def_id})
        resp.raise_for_status()
        return {str(jd["id"]): str(jd.get("activityId", "")) for jd in resp.json()}

    async def await_timer_job(
        self, instance_id: str, activity_id: str, *, attempts: int = 40, delay: float = 0.25
    ) -> TimerJob:
        """Localiza o job do timer (boundary) pendente da instancia, por activityId.

        Como o Job REST nao carrega `activityId`, resolvemos a atividade do job via
        `jobDefinitionId` -> job-definition.activityId.
        """
        jobdef_activities: dict[str, str] = {}
        for _ in range(attempts):
            if not jobdef_activities:
                jobdef_activities = await self._job_definition_activities(instance_id)
            resp = await self._client.get("/job", params={"processInstanceId": instance_id})
            resp.raise_for_status()
            for j in resp.json():
                jd_id = str(j.get("jobDefinitionId") or "")
                if jobdef_activities.get(jd_id) == activity_id:
                    return TimerJob(id=str(j["id"]), activity_id=activity_id, due_date=j.get("dueDate"))
            await asyncio.sleep(delay)
        raise EngineRestError(f"job de timer `{activity_id}` nao encontrado na instancia {instance_id}")

    async def list_timer_jobs(self, instance_id: str) -> list[TimerJob]:
        """Todos os jobs pendentes da instancia mapeados a activityId (snapshot, sem espera)."""
        jobdef_activities = await self._job_definition_activities(instance_id)
        resp = await self._client.get("/job", params={"processInstanceId": instance_id})
        resp.raise_for_status()
        jobs: list[TimerJob] = []
        for j in resp.json():
            jd_id = str(j.get("jobDefinitionId") or "")
            activity = jobdef_activities.get(jd_id, "")
            if activity:
                jobs.append(TimerJob(id=str(j["id"]), activity_id=activity, due_date=j.get("dueDate")))
        return jobs

    async def execute_job(self, job_id: str) -> None:
        """Executa um job imediatamente (dispara o timer sem esperar o relogio)."""
        resp = await self._client.post(f"/job/{job_id}/execute")
        if resp.status_code not in (200, 204):
            raise EngineRestError(f"execute job {job_id} falhou [{resp.status_code}]: {resp.text[:300]}")

    # --- timer-START jobs (SP-OP-ANS-CRON-001 dispatch por fato) ------------------------

    async def start_timer_job_id(self, activity_id: str) -> str:
        """Job-id do TimerStartEvent pendente (job ligado a process definition, sem instancia)."""
        resp = await self._client.get("/job", params={"activityId": activity_id})
        resp.raise_for_status()
        jobs = resp.json()
        if not jobs:
            raise EngineRestError(f"job de timer-start `{activity_id}` nao encontrado")
        return str(jobs[0]["id"])

    async def instance_ids_of_definition(self, process_definition_key: str) -> set[str]:
        """Ids das instancias ATIVAS de uma process-definition-key (para diff antes/depois)."""
        resp = await self._client.get(
            "/process-instance", params={"processDefinitionKey": process_definition_key}
        )
        resp.raise_for_status()
        return {str(i["id"]) for i in resp.json()}

    # --- decision-definition / decision-instance (ADR-0012: decision_version real) -----

    async def decision_definition_by_key(self, decision_key: str) -> dict[str, Any]:
        """Metadados canonicos da decision-definition deployada (id, version, deploymentId)."""
        resp = await self._client.get(f"/decision-definition/key/{decision_key}")
        if resp.status_code != 200:
            raise EngineRestError(
                f"decision-definition key `{decision_key}` falhou [{resp.status_code}]: {resp.text[:300]}"
            )
        return dict(resp.json())

    async def evaluate_decision_raw(
        self, decision_key: str, variables: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], dict[str, str], int]:
        """POSTa em `/decision-definition/key/{key}/evaluate` e devolve (rows, headers, status)."""
        payload = {"variables": self._to_camunda_vars(variables)}
        resp = await self._client.post(f"/decision-definition/key/{decision_key}/evaluate", json=payload)
        if resp.status_code not in (200, 201):
            raise EngineRestError(f"evaluate `{decision_key}` falhou [{resp.status_code}]: {resp.text[:300]}")
        rows: list[dict[str, Any]] = []
        for row in resp.json():
            rows.append({k: (v.get("value") if isinstance(v, dict) else v) for k, v in row.items()})
        return rows, dict(resp.headers), resp.status_code

    async def history_decision_instances(
        self, *, instance_id: str | None = None, decision_key: str | None = None
    ) -> list[dict[str, Any]]:
        """Historico de avaliacoes DMN (`GET /history/decision-instance`)."""
        params: dict[str, str] = {}
        if instance_id is not None:
            params["processInstanceId"] = instance_id
        if decision_key is not None:
            params["decisionDefinitionKey"] = decision_key
        resp = await self._client.get("/history/decision-instance", params=params)
        resp.raise_for_status()
        return list(resp.json())
