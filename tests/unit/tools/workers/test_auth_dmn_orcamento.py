"""DL-0189 — o orcamento de motor DMN por tarefa do portao de criterios do AUTH.

Duas partes:

MEDICAO (como era antes da DL-0189, `orcamento_dmn_s=None`). `ValidateAutoCriteriaWorker` faz de 2 a 4
avaliacoes DMN em sequencia por tarefa, cada uma limitada so pelo timeout do cliente HTTP (40 s em
producao, `WORKER_CLIENT_TIMEOUT_S`), maior que o bloqueio da tarefa (30 s, `WORKER_LOCK_DURATION_MS`).
As provas usam um motor de mentira que segue o comportamento do Camunda 7 / CIB Seven para tarefa
externa (`_MotorCamunda7`, abaixo) e o harness real (`WorkerHarness`), com a escala de tempo reduzida
(bloqueio de 1 s) para o teste rodar em segundos; o que importa e a relacao bloqueio < soma das
avaliacoes, nao os numeros absolutos.

CORRECAO. Com `orcamento_dmn_s` (producao: `WorkerRuntimeSettings.auth_dmn_budget_s`, o bloqueio menos
`WORKER_DMN_COMPLETE_MARGIN_MS`), a soma do tempo de motor da tarefa fica dentro do orcamento; o que
nao coube vira `*_TABELA_INDISPONIVEL` (analise humana), com a MESMA saida de hoje para tabela
indisponivel, e o motor normal produz a mesma saida de sempre.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

import httpx
import pytest
import structlog.testing
from pydantic import ValidationError

from maezo.runtime.worker_runtime.settings import WorkerRuntimeSettings
from maezo.tools.workers import harness as harness_mod
from maezo.tools.workers.auth import ValidateAutoCriteriaWorker, register_auth_workers
from maezo.tools.workers.auth_criteria import CriteriaSources
from maezo.tools.workers.dmn_transport import DmnEvaluationError, DmnVersion, FakeDmnTransport
from maezo.tools.workers.harness import (
    AUDIT_DECISION_COMPLETE,
    ExternalTask,
    FakeAuditSink,
    TopicSubscription,
    WorkerHarness,
)

_TOPICO = "operadora.auth.validate_auto_criteria"
_TAREFA = "ext-task-1"
_TENANT = "amh"
_FONTES = (
    "dut_rol_coverage",
    "dut_criteria_bariatrica",
    "carencia_check",
    "auth_criteria_contratual",
)
#: Escala do teste: o bloqueio de 30 s de producao vira 1 s aqui.
_BLOQUEIO_S = 1.0


# ---------------------------------------------------------------------------------------------
# O motor de mentira: semantica de tarefa externa do Camunda 7 / CIB Seven
# ---------------------------------------------------------------------------------------------


def _erro_http(status: int, operacao: str, mensagem: str) -> httpx.HTTPStatusError:
    """O erro que `CibSevenWorkerTransport` levanta (`resp.raise_for_status()`) para a resposta do motor."""
    pedido = httpx.Request("POST", f"http://motor/engine-rest/external-task/{_TAREFA}/{operacao}")
    resposta = httpx.Response(status, request=pedido, json={"type": "RestException", "message": mensagem})
    return httpx.HTTPStatusError(mensagem, request=pedido, response=resposta)


class _MotorCamunda7:
    """UMA tarefa externa com a semantica de bloqueio do Camunda 7 (a que o CIB Seven herda).

    - `fetchAndLock` devolve a tarefa se ela nao esta bloqueada OU se o bloqueio venceu (qualquer
      `workerId`, inclusive o mesmo), e a bloqueia por `lockDuration` para quem buscou.
    - `complete`/`failure` (`HandleExternalTaskCmd`): tarefa que nao existe mais (ja completada) ->
      404 `NotFoundException` ("Cannot find external task with id ..."); tarefa cujo bloqueio mais
      recente e de OUTRO `workerId` -> 400 `BadUserRequestException` ("External Task <id> cannot be
      completed by worker '<a>'. It is locked by worker '<b>'."). O motor compara so o `workerId`, nao o
      vencimento: quem completa depois de vencer, sem ninguem ter buscado de novo, e aceito.
      Referencia: Camunda 7 REST, `POST /external-task/{id}/complete` (400: "the task's most recent lock
      was not acquired by the provided worker"; 404: "the task does not exist").
    - completar apaga a tarefa (so UMA conclusao e aceita por tarefa).
    """

    def __init__(self, variaveis: dict[str, Any], *, bloqueio_s: float = _BLOQUEIO_S) -> None:
        self._variaveis = dict(variaveis)
        self._bloqueio_s = bloqueio_s
        self._t0 = time.monotonic()
        self.existe = True
        self.dono: str | None = None
        self.expira_em = 0.0
        self.buscas: list[tuple[float, str]] = []
        self.concluidas: list[tuple[str, dict[str, Any]]] = []
        self.recusas: list[tuple[str, str, int]] = []

    def agora(self) -> float:
        return time.monotonic() - self._t0

    def bloqueio_vencido(self) -> bool:
        return self.dono is not None and self.agora() >= self.expira_em

    def travar(self, worker_id: str) -> ExternalTask | None:
        """O lado do motor de um `fetchAndLock` (sincrono: o gancho do DMN lento o chama na thread)."""
        if not self.existe or (self.dono is not None and self.agora() < self.expira_em):
            return None
        self.dono, self.expira_em = worker_id, self.agora() + self._bloqueio_s
        self.buscas.append((self.agora(), worker_id))
        return ExternalTask(
            task_id=_TAREFA,
            topic=_TOPICO,
            process_instance_id="pi-1",
            business_key="AUTH-amh-1",
            worker_id=worker_id,
            variables=dict(self._variaveis),
        )

    def _validar(self, operacao: str, worker_id: str) -> None:
        if not self.existe:
            self.recusas.append((operacao, worker_id, 404))
            raise _erro_http(404, operacao, f"Cannot find external task with id {_TAREFA}")
        if self.dono != worker_id:
            self.recusas.append((operacao, worker_id, 400))
            raise _erro_http(
                400,
                operacao,
                f"External Task {_TAREFA} cannot be completed by worker '{worker_id}'. "
                f"It is locked by worker '{self.dono}'.",
            )

    # -- `WorkerTransport` ------------------------------------------------------------------

    async def fetch_and_lock(
        self,
        worker_id: str,
        topics: list[TopicSubscription],
        *,
        max_tasks: int,
        async_response_timeout_ms: int,
    ) -> list[ExternalTask]:
        del topics, max_tasks, async_response_timeout_ms
        tarefa = self.travar(worker_id)
        return [tarefa] if tarefa is not None else []

    async def complete(self, task_id: str, worker_id: str, variables: dict[str, Any]) -> None:
        del task_id
        self._validar("complete", worker_id)
        self.existe = False
        self.concluidas.append((worker_id, dict(variables)))

    async def handle_failure(
        self,
        task_id: str,
        worker_id: str,
        *,
        error_message: str,
        error_details: str = "",
        retries: int,
        retry_timeout_ms: int,
    ) -> None:
        del task_id, error_message, error_details, retries, retry_timeout_ms
        self._validar("failure", worker_id)
        self.dono = None

    async def handle_bpmn_error(self, task_id: str, worker_id: str, **_: Any) -> None:
        del task_id
        self._validar("bpmnError", worker_id)

    async def extend_lock(self, task_id: str, worker_id: str, *, new_duration_ms: int) -> None:
        raise AssertionError("o harness nao estende bloqueio")

    async def unlock(self, task_id: str) -> None:
        del task_id
        self.dono = None

    async def close(self) -> None:
        return None


class _DmnLento(FakeDmnTransport):
    """`FakeDmnTransport` com tempo de motor REAL por tabela (`asyncio.sleep`, cancelavel pelo `wait_for`
    de `evaluate_sync`) e um gancho chamado depois de cada avaliacao (para o motor re-entregar a tarefa
    enquanto o worker ainda espera). `timeout_cliente` modela o transporte real: a chamada que passa dele
    levanta `DmnEvaluationError`, como `CibSevenDmnTransport` faz com o `httpx.ReadTimeout`."""

    def __init__(
        self,
        atrasos: dict[str, float],
        *,
        timeout_cliente: float | None = None,
        gancho: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self.atrasos = atrasos
        self._timeout_cliente = timeout_cliente
        self._gancho = gancho
        self.chamadas_ao_motor: list[str] = []

    async def evaluate(
        self, decision_key: str, variables: dict[str, Any], *, tenant: str | None = None
    ) -> tuple[list[dict[str, Any]], DmnVersion]:
        self.chamadas_ao_motor.append(decision_key)
        atraso = self.atrasos.get(decision_key, 0.0)
        try:
            if self._timeout_cliente is not None and atraso >= self._timeout_cliente:
                await asyncio.sleep(self._timeout_cliente)
                raise DmnEvaluationError(f"CIB Seven unreachable evaluating `{decision_key}`: ReadTimeout")
            await asyncio.sleep(atraso)
            return await super().evaluate(decision_key, variables, tenant=tenant)
        finally:
            if self._gancho is not None:
                self._gancho()


def _registrar_tabelas[D: FakeDmnTransport](dmn: D) -> D:
    dmn.register("dut_rol_coverage", [{"no_rol": False, "requer_dut": True, "dut_ref": "DUT-BARIATRICA-001"}])
    dmn.register("dut_criteria_bariatrica", [{"dut_atendida": True, "motivo": "x"}])
    dmn.register("carencia_check", [{"carencia_cumprida": True, "prazo_restante_dias": 0}])
    dmn.register(
        "auth_criteria_contratual", [{"criterio_contratual_ok": False, "motivo": "SEM_REGRA_RATIFICADA"}]
    )
    return dmn


class _Teto:
    def within_l2_ceiling(self, **_: Any) -> bool:
        return True


def _fontes() -> CriteriaSources:
    return CriteriaSources(
        ratified=frozenset(_FONTES), dut_criteria_by_ref={"DUT-BARIATRICA-001": "dut_criteria_bariatrica"}
    )


def _variaveis(**extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "tenant_id": _TENANT,
        "valor_estimado_brl": 180.0,
        "codigo_procedimento_tuss": "30101018",
        "categoria_procedimento": "cirurgia",
        "tipo_procedimento": "eletivo",
        "dias_desde_adesao": 400,
        "cpt_declarada": False,
        "imc": 41,
    }
    base.update(extra)
    return base


def _worker(dmn: FakeDmnTransport, orcamento_dmn_s: float | None = None) -> ValidateAutoCriteriaWorker:
    return ValidateAutoCriteriaWorker(
        resolver=_Teto(),  # type: ignore[arg-type]
        dmn=dmn,
        sources=_fontes(),
        orcamento_dmn_s=orcamento_dmn_s,
    )


def _harness(
    motor: _MotorCamunda7, worker_id: str, sink: FakeAuditSink, worker: ValidateAutoCriteriaWorker
) -> WorkerHarness:
    h = WorkerHarness(
        motor,
        worker_id=worker_id,
        tenant=_TENANT,
        audit_sink=sink,
        lock_duration_ms=int(_BLOQUEIO_S * 1000),
    )
    h.register_worker(worker)
    return h


@pytest.fixture
def desfechos(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Cada desfecho contado em `maezo_worker_task_*` pelo harness (`_emit_worker_task_outcome`)."""
    vistos: list[str] = []

    def _gravar(*, tenant: str, topic: str, outcome: str, duration_seconds: float | None = None) -> None:
        del tenant, topic, duration_seconds
        vistos.append(outcome)

    monkeypatch.setattr(harness_mod, "_emit_worker_task_outcome", _gravar)
    return vistos


# ---------------------------------------------------------------------------------------------
# MEDICAO — sem orcamento (o comportamento anterior a DL-0189)
# ---------------------------------------------------------------------------------------------


async def test_medicao_bloqueio_vence_outro_worker_refaz_e_o_complete_tardio_e_recusado(
    desfechos: list[str],
) -> None:
    """Outra replica (`workerId` diferente) busca a tarefa quando o bloqueio vence; a conclusao tardia do
    primeiro worker e recusada (400) e a falha que o harness tenta reportar tambem (400): o harness so loga
    `worker_handle_task_crashed`, sem desfecho contado. O motor aceita UMA conclusao (a da replica B): nao
    ha decisao duplicada, ha trabalho duplicado. A auditoria tem UMA linha COMPLETE (dedup
    `{tenant}:{task_id}`), mas e a do worker que escreveu primeiro (A), cuja saida o motor RECUSOU: a
    cadeia atesta `criterio_tecnico_ok=false`/`TECNICO_TABELA_INDISPONIVEL` e o motor guardou
    `criterio_tecnico_ok=true`."""
    motor = _MotorCamunda7(_variaveis())
    sink = FakeAuditSink()
    tarefas_b: list[ExternalTask] = []

    def b_busca_quando_vence() -> None:
        if motor.bloqueio_vencido() and not tarefas_b:
            tarefa = motor.travar("pod-b")
            if tarefa is not None:
                tarefas_b.append(tarefa)

    # A: a primeira tabela espera alem do bloqueio e estoura o timeout do cliente (40 s > 30 s em producao).
    dmn_a = _registrar_tabelas(
        _DmnLento({"dut_rol_coverage": 1.3}, timeout_cliente=1.2, gancho=b_busca_quando_vence)
    )
    dmn_b = _registrar_tabelas(_DmnLento({}))
    harness_a = _harness(motor, "pod-a", sink, _worker(dmn_a))
    harness_b = _harness(motor, "pod-b", sink, _worker(dmn_b))

    [tarefa_a] = await motor.fetch_and_lock("pod-a", [], max_tasks=1, async_response_timeout_ms=0)
    with structlog.testing.capture_logs() as logs:
        await harness_a._run_handle(tarefa_a)
        assert len(tarefas_b) == 1, "o motor re-entregou a tarefa a outra replica enquanto A esperava"
        await harness_b._run_handle(tarefas_b[0])

    # Re-entregue e refeita por outro worker.
    assert [quem for _t, quem in motor.buscas] == ["pod-a", "pod-b"]
    assert motor.buscas[1][0] >= _BLOQUEIO_S
    # A: a tecnica parou na primeira tabela (indisponivel), entao a tabela de DUT nem foi consultada.
    assert dmn_a.chamadas_ao_motor == ["dut_rol_coverage", "carencia_check", "auth_criteria_contratual"]
    assert dmn_b.chamadas_ao_motor == list(_FONTES)
    # O complete tardio de A e recusado, e a falha que o harness tenta reportar tambem.
    assert motor.recusas == [("complete", "pod-a", 400), ("failure", "pod-a", 400)]
    eventos = [entrada["event"] for entrada in logs]
    assert eventos.count("worker_handle_task_crashed") == 1
    assert eventos.count("worker_task_completed") == 1  # so o de B
    assert desfechos == ["completed"]  # A nao conta desfecho nenhum
    # UMA decisao aceita pelo motor: a de B.
    assert len(motor.concluidas) == 1
    quem, aceitas = motor.concluidas[0]
    assert quem == "pod-b"
    assert aceitas["criterio_tecnico_ok"] is True
    # UMA linha COMPLETE na cadeia, mas e a de A (escrita antes do complete recusado).
    assert len(sink.emitted) == 1
    registro, chave = sink.emitted[0]
    assert chave == f"{_TENANT}:{_TAREFA}"
    assert registro.decision == AUDIT_DECISION_COMPLETE
    assert registro.details["criterio_tecnico_ok"] is False
    assert registro.details["motivo_bloqueio_criterios"] == "TECNICO_TABELA_INDISPONIVEL"
    assert registro.details["criterio_tecnico_ok"] != aceitas["criterio_tecnico_ok"]


async def test_medicao_a_mesma_replica_rebusca_e_a_segunda_conclusao_e_recusada(desfechos: list[str]) -> None:
    """O laco de fetch do PROPRIO daemon rebusca a tarefa vencida (o motor nao exclui o mesmo `workerId` e
    o harness nao exclui tarefas em voo). Com o mesmo `workerId`, a primeira conclusao e aceita (o motor
    compara so o `workerId`) e a segunda recebe 404 (a tarefa ja nao existe); a falha tambem 404. So
    trabalho duplicado: UMA conclusao, UMA linha de auditoria (a segunda foi dedup)."""
    motor = _MotorCamunda7(_variaveis())
    sink = FakeAuditSink()
    segunda: list[ExternalTask] = []

    def rebusca() -> None:
        if motor.bloqueio_vencido() and not segunda:
            tarefa = motor.travar("pod-a")
            if tarefa is not None:
                segunda.append(tarefa)

    dmn = _registrar_tabelas(_DmnLento(dict.fromkeys(_FONTES, 0.35), gancho=rebusca))
    harness_a = _harness(motor, "pod-a", sink, _worker(dmn))
    [primeira] = await motor.fetch_and_lock("pod-a", [], max_tasks=1, async_response_timeout_ms=0)
    with structlog.testing.capture_logs() as logs:
        await harness_a._run_handle(primeira)
        assert len(segunda) == 1
        dmn.atrasos = {}
        await harness_a._run_handle(segunda[0])

    assert [quem for _t, quem in motor.buscas] == ["pod-a", "pod-a"]
    assert dmn.chamadas_ao_motor == list(_FONTES) * 2  # trabalho duplicado
    assert [quem for quem, _v in motor.concluidas] == ["pod-a"]
    assert motor.recusas == [("complete", "pod-a", 404), ("failure", "pod-a", 404)]
    assert [entrada["event"] for entrada in logs].count("worker_handle_task_crashed") == 1
    assert desfechos == ["completed"]
    assert len(sink.emitted) == 1  # a segunda emissao caiu no dedup `{tenant}:{task_id}`
    assert sink.emitted[0][0].details["criterio_tecnico_ok"] == motor.concluidas[0][1]["criterio_tecnico_ok"]


# ---------------------------------------------------------------------------------------------
# CORRECAO — com orcamento
# ---------------------------------------------------------------------------------------------


async def test_com_orcamento_a_tarefa_termina_dentro_do_bloqueio_e_ninguem_rebusca(
    desfechos: list[str],
) -> None:
    """O mesmo motor lento da medicao (soma das tabelas acima do bloqueio), agora com o orcamento derivado
    da escala do teste (bloqueio de 1 s menos 0,3 s de margem): a tarefa completa pelo dono do bloqueio,
    sem re-entrega, e o que nao coube no orcamento vira tabela indisponivel (analise humana)."""
    motor = _MotorCamunda7(_variaveis())
    sink = FakeAuditSink()
    rebuscas: list[ExternalTask] = []

    def outra_replica() -> None:
        if motor.bloqueio_vencido():
            tarefa = motor.travar("pod-b")
            if tarefa is not None:
                rebuscas.append(tarefa)

    dmn = _registrar_tabelas(_DmnLento(dict.fromkeys(_FONTES, 0.3), gancho=outra_replica))
    orcamento_s = _BLOQUEIO_S - 0.3
    harness_a = _harness(motor, "pod-a", sink, _worker(dmn, orcamento_dmn_s=orcamento_s))
    [tarefa] = await motor.fetch_and_lock("pod-a", [], max_tasks=1, async_response_timeout_ms=0)
    inicio = time.monotonic()
    await harness_a._run_handle(tarefa)
    duracao = time.monotonic() - inicio

    assert rebuscas == [] and motor.recusas == []
    assert duracao < _BLOQUEIO_S
    assert desfechos == ["completed"]
    [(quem, saida)] = motor.concluidas
    assert quem == "pod-a"
    # 0,3 s + 0,3 s cabem em 0,7 s; a terceira avaliacao e cortada no meio; a quarta nem chega ao motor.
    assert dmn.chamadas_ao_motor == ["dut_rol_coverage", "dut_criteria_bariatrica", "carencia_check"]
    assert saida["criterio_tecnico_ok"] is True
    assert saida["criterio_regulatorio_ok"] is False
    assert saida["criterio_contratual_ok"] is False
    assert saida["auto_criteria_falhas"] == [
        "REGULATORIO_TABELA_INDISPONIVEL",
        "CONTRATUAL_TABELA_INDISPONIVEL",
    ]
    assert sink.emitted[0][0].details["motivo_bloqueio_criterios"] == "REGULATORIO_TABELA_INDISPONIVEL"


def test_motor_lento_respeita_o_orcamento_e_sai_igual_a_tabela_indisponivel_de_hoje() -> None:
    """(a) Motor lento: o `execute` inteiro fica no orcamento (mais a folga local), so a primeira tabela
    chega ao motor e a saida e IDENTICA a de hoje com as tabelas indisponiveis (motor que falha toda
    avaliacao): todos os criterios de motor falsos, `*_TABELA_INDISPONIVEL`, `auto_criteria_verificado`
    verdadeiro -> catch-all `ANALISE_HUMANA` de `auth_auto_approval`. Nunca negativa."""
    orcamento_s = 0.3
    lento = _registrar_tabelas(_DmnLento(dict.fromkeys(_FONTES, 5.0)))
    inicio = time.monotonic()
    saida = _worker(lento, orcamento_dmn_s=orcamento_s).execute(_variaveis())
    duracao = time.monotonic() - inicio

    assert duracao < orcamento_s + 0.3
    assert lento.chamadas_ao_motor == ["dut_rol_coverage"]
    # Nada registrado: toda avaliacao falha, como uma tabela indisponivel hoje.
    hoje_indisponivel = _worker(FakeDmnTransport()).execute(_variaveis())
    assert saida == hoje_indisponivel
    assert saida["auto_criteria_verificado"] is True
    assert saida["status"] == "criteria_validated"
    assert saida["auto_criteria_falhas"] == [
        "TECNICO_TABELA_INDISPONIVEL",
        "REGULATORIO_TABELA_INDISPONIVEL",
        "CONTRATUAL_TABELA_INDISPONIVEL",
    ]
    for criterio in ("criterio_tecnico_ok", "criterio_regulatorio_ok", "criterio_contratual_ok"):
        assert saida[criterio] is False


def test_orcamento_esgotado_e_registrado_com_o_tipo_do_erro_sem_dado_do_caso() -> None:
    lento = _registrar_tabelas(_DmnLento(dict.fromkeys(_FONTES, 5.0)))
    with structlog.testing.capture_logs() as logs:
        _worker(lento, orcamento_dmn_s=0.1).execute(_variaveis())
    falhas = [e for e in logs if e["event"] == "auth_auto_criteria_dmn_failed"]
    assert [e["error_type"] for e in falhas] == [
        "TimeoutError",
        "OrcamentoDmnEsgotadoError",
        "OrcamentoDmnEsgotadoError",
    ]
    for entrada in falhas:
        assert set(entrada) == {"event", "log_level", "decision", "error", "error_type"}


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"codigo_procedimento_tuss": None},
        {"dias_desde_adesao": None},
        {"valor_estimado_brl": 0},
        {"tenant_id": ""},
    ],
)
def test_motor_normal_sai_identico_com_e_sem_orcamento(extra: dict[str, Any]) -> None:
    """(b) Motor normal: a saida e as chamadas ao motor sao as mesmas com e sem o orcamento de producao."""
    variaveis = _variaveis(**extra)
    sem, com = _registrar_tabelas(_DmnLento({})), _registrar_tabelas(_DmnLento({}))
    padrao = WorkerRuntimeSettings().auth_dmn_budget_s
    assert _worker(com, orcamento_dmn_s=padrao).execute(variaveis) == _worker(sem).execute(variaveis)
    assert com.chamadas_ao_motor == sem.chamadas_ao_motor


# ---------------------------------------------------------------------------------------------
# (c) Configuracao: derivado, validado na partida e ligado ate o worker
# ---------------------------------------------------------------------------------------------


def test_orcamento_e_o_bloqueio_menos_a_margem_do_complete() -> None:
    assert WorkerRuntimeSettings().auth_dmn_budget_s == 20.0
    assert WorkerRuntimeSettings(WORKER_LOCK_DURATION_MS=60_000).auth_dmn_budget_s == 50.0
    assert WorkerRuntimeSettings(WORKER_DMN_COMPLETE_MARGIN_MS=12_000).auth_dmn_budget_s == 18.0


@pytest.mark.parametrize("margem_ms", [30_000, 45_000, 0, -1])
def test_partida_recusa_margem_que_nao_deixa_orcamento(margem_ms: int) -> None:
    with pytest.raises(ValidationError):
        WorkerRuntimeSettings(WORKER_DMN_COMPLETE_MARGIN_MS=margem_ms)


@pytest.mark.parametrize("valor", [0, -1.0, float("nan"), float("inf"), True, "20"])
def test_worker_recusa_orcamento_invalido(valor: Any) -> None:
    with pytest.raises(ValueError, match="orcamento_dmn_s"):
        ValidateAutoCriteriaWorker(dmn=FakeDmnTransport(), orcamento_dmn_s=valor)


def test_registro_do_auth_leva_o_orcamento_ao_worker() -> None:
    def _validador(orcamento: float | None) -> ValidateAutoCriteriaWorker:
        h = WorkerHarness(_MotorCamunda7({}), worker_id="w")
        kwargs: dict[str, Any] = {"dmn": FakeDmnTransport()}
        if orcamento is not None:
            kwargs["auth_dmn_orcamento_s"] = orcamento
        register_auth_workers(h, **kwargs)
        worker = h.registry.get(_TOPICO)
        assert isinstance(worker, ValidateAutoCriteriaWorker)
        return worker

    assert _validador(20.0)._orcamento_dmn_s == 20.0
    assert _validador(None)._orcamento_dmn_s is None
