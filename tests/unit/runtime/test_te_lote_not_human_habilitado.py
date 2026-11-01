"""T-E lote unico (ADR-0030 §4 / Tier-3; R-050, R-059 CRED, R-119): os quatro `*_NOT_HUMAN`.

Decisao do dono 2026-10-31 ("T-E lote unico: preparar o PR do lote agora"), executando o registro
R-075 de `docs/review-queue.md` (`FILA-T-E-LOTE-UNICO`): os 4 codigos saem de `te_deferred` e entram
em `TE_ENABLED_CODES` (gate do CI) + `_TE_ENABLED_CODES` (service.py) NO MESMO PR, usando os
boundaries JA modelados para os terminais neutros — nenhuma mudanca de BPMN, nenhuma user task nova:

    ERR_CANCEL_MANTER_NOT_HUMAN        BE_ManterNaoConfirmado -> End_ManterNaoConfirmado
    ERR_DECRED_NOT_HUMAN               BE_DecredNaoHumano     -> End_DecredBloqueadoNaoHumano
    ERR_CRED_DENIAL_NOT_HUMAN          BE_CredDenialNaoHumano -> End_CredGuardBloqueadoNaoHumano
    ERR_CONTRACT_SUSPENSION_NOT_HUMAN  BE_SuspensaoNaoHumano  -> End_SuspensaoBloqueadaNaoHumano

O que estes testes travam (e o que cada mutacao quebra):

1. Os 4 codigos estao em `PRODUCTION_BPMN_ERROR_ALLOWLIST` e continuam reconhecidos pelo detector de
   recusa auditada do harness (`is_guard_refusal_code`) — habilitar SEM auditoria seria trocar um
   incident visivel por um fim limpo silencioso, o desfecho que o gate existia para impedir.
2. As duas listas de habilitacao (gate e runtime) sao IDENTICAS.
3. Nenhum OUTRO `*_NOT_HUMAN` entra em producao por carona (negativo da guarda: a habilitacao e' por
   lista explicita, nao por sufixo).
4. Ponta-a-ponta no harness REAL (sem engine): cada worker, ao recusar, grava a linha `REFUSED`
   ANTES de reportar o `bpmnError` ao boundary, nao completa a task (o efeito adverso NAO acontece)
   e nao vira incident.
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Callable
from typing import Any

import pytest
from scripts.ci.check_bpmn_error_allowlist import TE_ENABLED_CODES, is_te_gated

from maezo.gateway.audit import AuditRecord
from maezo.runtime.worker_runtime.service import (
    _GATE_PROVEN_BPMN_ERROR_CODES,
    _TE_ENABLED_CODES,
    PRODUCTION_BPMN_ERROR_ALLOWLIST,
    _is_te_gated,
)
from maezo.tools.workers.base import FunctionWorker
from maezo.tools.workers.cancel import CANCEL_BPMN_ERROR_ALLOWLIST, confirm_maintained_decision_entry
from maezo.tools.workers.credenciamento import (
    CRED_BPMN_ERROR_ALLOWLIST,
    ERR_CRED_DENIAL_NOT_HUMAN,
    ERR_CRED_INVALID_PRESTADOR,
    ERR_DECRED_NOT_HUMAN,
    register_cred_denial,
    register_descredenciamento,
)
from maezo.tools.workers.harness import (
    AUDIT_DECISION_REFUSED,
    ExternalTask,
    FakeAuditSink,
    FakeWorkerTransport,
    WorkerHarness,
    is_guard_refusal_code,
)
from maezo.tools.workers.inadimplencia import (
    ERR_CONTRACT_SUSPENSION_NOT_HUMAN,
    INADIMPLENCIA_BPMN_ERROR_ALLOWLIST,
    register_contract_suspension,
)

_ERR_CANCEL_MANTER_NOT_HUMAN = "ERR_CANCEL_MANTER_NOT_HUMAN"

#: O lote, exatamente — nem um a mais, nem um a menos.
_LOTE_T_E = frozenset(
    {
        _ERR_CANCEL_MANTER_NOT_HUMAN,
        ERR_DECRED_NOT_HUMAN,
        ERR_CRED_DENIAL_NOT_HUMAN,
        ERR_CONTRACT_SUSPENSION_NOT_HUMAN,
    }
)


# ---------------------------------------------------------------------------
# 1-3. Postura das listas
# ---------------------------------------------------------------------------


def test_as_duas_listas_de_habilitacao_sao_identicas() -> None:
    """O runtime nao importa o script do CI; o espelho so' se sustenta com este teste."""
    assert frozenset(_TE_ENABLED_CODES) == frozenset(TE_ENABLED_CODES)


def test_lote_inteiro_habilitado_junto_com_o_codigo_de_auth() -> None:
    assert frozenset(TE_ENABLED_CODES) == _LOTE_T_E | {"ERR_AUTH_DENIAL_INCOMPLETE"}


@pytest.mark.parametrize("code", sorted(_LOTE_T_E))
def test_codigo_do_lote_em_producao_e_ainda_auditado(code: str) -> None:
    assert code in _GATE_PROVEN_BPMN_ERROR_CODES, f"{code} nao e' gate-proven no runtime"
    assert code in PRODUCTION_BPMN_ERROR_ALLOWLIST, f"{code} fora do allowlist de producao"
    assert not _is_te_gated(code)
    assert not is_te_gated(code)
    assert is_guard_refusal_code(code), (
        f"{code} habilitado E sem auditoria de recusa — a habilitacao virou recusa silenciosa"
    )


def test_nenhum_outro_not_human_entra_em_producao_por_carona() -> None:
    """Negativo da guarda: a habilitacao e' por lista explicita. Os `*_NOT_HUMAN` em producao sao
    EXATAMENTE os do lote; qualquer outro guard (pagamento, recurso, reembolso, a rescisao de
    `send_cancellation_notice`...) continua no caminho de incident."""
    not_human_em_producao = {c for c in PRODUCTION_BPMN_ERROR_ALLOWLIST if c.endswith("_NOT_HUMAN")}
    assert not_human_em_producao == _LOTE_T_E
    for hipotetico in (
        "ERR_CANCELLATION_NOT_HUMAN",
        "ERR_PAYMENT_RELEASE_NOT_HUMAN",
        "ERR_REEMBOLSO_DENIAL_NOT_HUMAN",
        "ERR_OUTRO_GUARD_HIPOTETICO_NOT_HUMAN",
    ):
        assert _is_te_gated(hipotetico), hipotetico
        assert is_te_gated(hipotetico), hipotetico
        assert hipotetico not in PRODUCTION_BPMN_ERROR_ALLOWLIST, hipotetico


def test_constantes_por_worker_carregam_o_codigo_do_lote() -> None:
    assert frozenset({_ERR_CANCEL_MANTER_NOT_HUMAN}) == CANCEL_BPMN_ERROR_ALLOWLIST
    assert frozenset({ERR_CONTRACT_SUSPENSION_NOT_HUMAN}) == INADIMPLENCIA_BPMN_ERROR_ALLOWLIST
    assert (
        frozenset({ERR_CRED_INVALID_PRESTADOR, ERR_DECRED_NOT_HUMAN, ERR_CRED_DENIAL_NOT_HUMAN})
        == CRED_BPMN_ERROR_ALLOWLIST
    )


# ---------------------------------------------------------------------------
# 4. Harness real: REFUSED antes do bpmnError, sem complete, sem incident
# ---------------------------------------------------------------------------


class _Ordem(FakeWorkerTransport):
    def __init__(self, calls: list[str]) -> None:
        super().__init__()
        self._calls = calls

    async def complete(self, task_id: str, worker_id: str, variables: dict[str, Any]) -> None:
        self._calls.append("complete")
        await super().complete(task_id, worker_id, variables)

    async def handle_failure(self, task_id: str, worker_id: str, **kwargs: Any) -> None:
        self._calls.append("failure")
        await super().handle_failure(task_id, worker_id, **kwargs)

    async def handle_bpmn_error(self, task_id: str, worker_id: str, **kwargs: Any) -> None:
        self._calls.append("bpmn_error")
        await super().handle_bpmn_error(task_id, worker_id, **kwargs)


class _SinkOrdem(FakeAuditSink):
    def __init__(self, calls: list[str]) -> None:
        super().__init__()
        self._calls = calls

    async def emit_once(self, record: AuditRecord, *, dedup_key: str) -> str:
        self._calls.append("emit")
        return await super().emit_once(record, dedup_key=dedup_key)


_CASOS: list[tuple[str, str, Callable[..., Any], dict[str, Any]]] = [
    (
        _ERR_CANCEL_MANTER_NOT_HUMAN,
        "operadora.cancel.confirm_maintained_decision",
        functools.partial(confirm_maintained_decision_entry, kafka=None),
        # gateway caiu no default sem MANTER humano
        {"decisao_cancelamento": "RESCINDIR", "fundamentacao_contratual": "x"},
    ),
    (
        ERR_DECRED_NOT_HUMAN,
        "operadora.cred.register_descredenciamento",
        register_descredenciamento,
        {
            "decisao_cred": "MANTER",
            "responsavel_id": "x",
            "fundamentacao": "x",
            "referencia_regulatoria": "x",
            "comprovacao_notificacao_previa": "x",
        },
    ),
    (
        ERR_CRED_DENIAL_NOT_HUMAN,
        "operadora.cred.register_cred_denial",
        register_cred_denial,
        {
            "decisao_cred": "APROVAR_CREDENCIAMENTO",
            "responsavel_id": "x",
            "fundamentacao": "x",
            "referencia_regulatoria": "x",
        },
    ),
    (
        ERR_CONTRACT_SUSPENSION_NOT_HUMAN,
        "operadora.inadimplencia.register_contract_suspension",
        register_contract_suspension,
        {
            "decisao_inadimplencia": "MANTER",
            "responsavel_id": "j-001",
            "fundamentacao_contratual": "x",
            "referencia_regulatoria": "x",
            "comprovacao_notificacao_previa": "x",
            "comprovacao_periodo_minimo": "x",
            "ja_em_rescisao_cancel": False,
        },
    ),
]


@pytest.mark.parametrize(("code", "topic", "fn", "variables"), _CASOS, ids=[c[0] for c in _CASOS])
def test_recusa_com_allowlist_de_producao_audita_e_vai_ao_boundary(
    code: str, topic: str, fn: Callable[..., Any], variables: dict[str, Any]
) -> None:
    calls: list[str] = []
    transport = _Ordem(calls)
    sink = _SinkOrdem(calls)
    harness = WorkerHarness(
        transport,
        worker_id="te-lote",
        tenant="amh",
        audit_sink=sink,
        bpmn_error_allowlist=PRODUCTION_BPMN_ERROR_ALLOWLIST,
    )
    harness.register_worker(FunctionWorker(topic, fn))
    task = ExternalTask(
        task_id="task-te",
        topic=topic,
        process_instance_id="proc-te",
        business_key="bk-te",
        worker_id="te-lote",
        variables=variables,
    )
    asyncio.run(harness._handle(task))

    # emit-before-refuse, e o boundary modelado (nao incident, nao complete).
    assert calls == ["emit", "bpmn_error"]
    assert [(t, c) for (t, c, _m) in transport.bpmn_errors] == [("task-te", code)]
    assert transport.failures == []
    assert transport.completed == []  # o efeito adverso NAO aconteceu
    record, dedup_key = sink.emitted[0]
    assert record.decision == AUDIT_DECISION_REFUSED
    assert record.details["guard_code"] == code
    assert record.action == topic
    assert dedup_key == "amh:refuse:task-te"
