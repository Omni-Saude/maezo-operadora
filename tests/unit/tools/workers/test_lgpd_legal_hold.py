"""R-044 (F-062, ADR-0020 item 6): o predicado de legal-hold na eliminacao LGPD (`execute_request`).

Bandeira DARK `MAEZO_LGPD_LEGAL_HOLD_PREDICATE`. Prova: (1) desligada nada muda e o predicado nem e
consultado; (2) ligada, hold ativo OU duvida difere a eliminacao com recusa `*_NOT_HUMAN` que o harness
grava na cadeia ADR-0007 antes do incidente retries=0; (3) LIVRE nunca completa a tarefa (o proximo
gate continua recusando); (4) a fiacao do seam; (5) guarda negativa de PHI. Fixtures sinteticas.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs

from maezo.platform.lifecycle.legal_hold import (
    REGISTRY_PATH_ENV,
    LegalHoldDecision,
    LegalHoldOutcome,
)
from maezo.tools.workers.harness import (
    AUDIT_DECISION_REFUSED,
    ExternalTask,
    FakeAuditSink,
    FakeWorkerTransport,
    WorkerFailureError,
    WorkerHarness,
)
from maezo.tools.workers.lgpd import (
    ERR_DSR_LEGAL_HOLD_NOT_HUMAN,
    ExecuteRequestWorker,
    LegalHoldErasureDeferredError,
    register_lgpd_workers,
)

_TOPIC = "operadora.lgpd.execute_request"
_PSEUDO = "pseudo-sintetico-4b2e81"


def _vars(tipo: str = "eliminacao", **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "tenant_id": "tenant-a",
        "titular_pseudo_id": _PSEUDO,
        "tipo_requisicao": tipo,
        "decisao_dsr": "EXECUTAR_E_ENVIAR",
        "human_approved": True,
        "fundamentacao_legal": "texto livre sintetico",
    }
    base.update(extra)
    return base


class _Spy:
    def __init__(self, outcome: LegalHoldOutcome, motivo: str = "x", hold_ids: tuple[str, ...] = ()) -> None:
        self.decision = LegalHoldDecision(outcome, motivo, hold_ids=hold_ids, registro_sha256="a" * 64)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> LegalHoldDecision:
        self.calls.append(kwargs)
        return self.decision


def _registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, com_hold: bool) -> None:
    texto = (
        "schema_version: 1\nholds:\n  - hold_id: LH-SINT-1\n    tenant_id: tenant-a\n"
        f"    titular_pseudo_id: {_PSEUDO}\n    colocado_em: 2026-09-01\n"
        if com_hold
        else "schema_version: 1\nholds: []\n"
    )
    path = tmp_path / "holds.yaml"
    path.write_text(texto, encoding="utf-8")
    monkeypatch.setenv(REGISTRY_PATH_ENV, str(path))


# ---------------------------------------------------------------------------------------------
# 1. Desligada (padrao): byte-a-byte o comportamento de hoje
# ---------------------------------------------------------------------------------------------


def test_padrao_e_desligada() -> None:
    assert ExecuteRequestWorker()._legal_hold_predicate is False


def test_desligada_nao_consulta_o_predicado_e_recusa_como_hoje() -> None:
    spy = _Spy(LegalHoldOutcome.RETIDO)
    worker = ExecuteRequestWorker(legal_hold_evaluator=spy)
    with pytest.raises(WorkerFailureError) as exc:
        worker.execute(_vars())
    assert spy.calls == []
    assert exc.value.retries_left == 0
    assert not isinstance(exc.value, PermissionError)


@pytest.mark.parametrize("lixo", ["true", 1, "1", [True], {"on": True}])
def test_valor_lixo_nao_liga_a_bandeira(lixo: object) -> None:
    spy = _Spy(LegalHoldOutcome.RETIDO)
    worker = ExecuteRequestWorker(legal_hold_predicate=lixo, legal_hold_evaluator=spy)  # type: ignore[arg-type]
    with pytest.raises(WorkerFailureError):
        worker.execute(_vars())
    assert spy.calls == []


# ---------------------------------------------------------------------------------------------
# 2. Ligada: hold ativo ou duvida => eliminacao DIFERIDA
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("outcome", [LegalHoldOutcome.RETIDO, LegalHoldOutcome.INDETERMINADO])
def test_ligada_bloqueia_hold_ativo_e_duvida(outcome: LegalHoldOutcome) -> None:
    spy = _Spy(outcome, "hold_ativo", ("LH-SINT-1",))
    worker = ExecuteRequestWorker(legal_hold_predicate=True, legal_hold_evaluator=spy)
    with pytest.raises(LegalHoldErasureDeferredError) as exc:
        worker.execute(_vars())
    assert isinstance(exc.value, PermissionError)
    assert str(exc.value).startswith(f"{ERR_DSR_LEGAL_HOLD_NOT_HUMAN}: ")
    assert f"desfecho={outcome.value}" in str(exc.value)
    assert exc.value.decision is spy.decision
    assert spy.calls == [{"tenant_id": "tenant-a", "titular_pseudo_id": _PSEUDO}]


def test_ligada_livre_segue_para_o_proximo_gate_que_ainda_recusa() -> None:
    spy = _Spy(LegalHoldOutcome.LIVRE, "sem_hold_ativo")
    worker = ExecuteRequestWorker(legal_hold_predicate=True, legal_hold_evaluator=spy)
    with pytest.raises(WorkerFailureError) as exc:
        worker.execute(_vars())
    assert len(spy.calls) == 1
    assert exc.value.retries_left == 0
    assert "nao existe seam de execucao" in str(exc.value)


def test_ligada_com_registro_real_e_hold_ativo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _registry(tmp_path, monkeypatch, com_hold=True)
    worker = ExecuteRequestWorker(legal_hold_predicate=True)
    with pytest.raises(LegalHoldErasureDeferredError) as exc:
        worker.execute(_vars())
    assert exc.value.decision.outcome is LegalHoldOutcome.RETIDO
    assert exc.value.decision.hold_ids == ("LH-SINT-1",)


def test_ligada_sem_registro_configurado_bloqueia(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(REGISTRY_PATH_ENV, raising=False)
    worker = ExecuteRequestWorker(legal_hold_predicate=True)
    with pytest.raises(LegalHoldErasureDeferredError) as exc:
        worker.execute(_vars())
    assert exc.value.decision.outcome is LegalHoldOutcome.INDETERMINADO


def test_ligada_sem_titular_bloqueia(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _registry(tmp_path, monkeypatch, com_hold=False)
    worker = ExecuteRequestWorker(legal_hold_predicate=True)
    with pytest.raises(LegalHoldErasureDeferredError) as exc:
        worker.execute(_vars(titular_pseudo_id=None))
    assert exc.value.decision.motivo == "titular_nao_identificado"


@pytest.mark.parametrize("tipo", ["correcao", "portabilidade", "confirmacao_acesso"])
def test_ligada_so_a_eliminacao_consulta_o_predicado(tipo: str) -> None:
    spy = _Spy(LegalHoldOutcome.RETIDO)
    worker = ExecuteRequestWorker(legal_hold_predicate=True, legal_hold_evaluator=spy)
    with pytest.raises(WorkerFailureError):
        worker.execute(_vars(tipo))
    assert spy.calls == []


def test_ligada_guardas_humanas_continuam_antes_do_predicado() -> None:
    spy = _Spy(LegalHoldOutcome.LIVRE)
    worker = ExecuteRequestWorker(legal_hold_predicate=True, legal_hold_evaluator=spy)
    with pytest.raises(WorkerFailureError):
        worker.execute(_vars(human_approved=False))
    assert spy.calls == []


async def test_harness_grava_a_recusa_na_cadeia_antes_do_incidente(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _registry(tmp_path, monkeypatch, com_hold=True)
    transport = FakeWorkerTransport()
    sink = FakeAuditSink()
    harness = WorkerHarness(transport, worker_id="w", tenant="tenant-a", audit_sink=sink)
    register_lgpd_workers(harness, lgpd_legal_hold_predicate=True)
    task = ExternalTask(
        task_id="t-1",
        topic=_TOPIC,
        process_instance_id="p-1",
        business_key="bk-1",
        worker_id="w",
        variables=_vars(),
        retries=None,
    )
    await harness._handle(task)
    assert len(sink.emitted) == 1
    record, _ = sink.emitted[0]
    assert record.decision == AUDIT_DECISION_REFUSED
    assert record.details["guard_code"] == ERR_DSR_LEGAL_HOLD_NOT_HUMAN
    assert _PSEUDO not in repr(record.details)
    assert transport.completed == []
    assert transport.failures[0][2] == 0  # incidente, nunca retentado


# ---------------------------------------------------------------------------------------------
# 3. Fiacao do seam em `register_lgpd_workers`
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seams", "esperado"),
    [
        ({}, False),
        ({"lgpd_legal_hold_predicate": False}, False),
        ({"lgpd_legal_hold_predicate": True}, True),
        ({"lgpd_legal_hold_predicate": "true"}, False),
    ],
)
def test_register_lgpd_workers_repassa_o_seam(seams: dict[str, Any], esperado: bool) -> None:
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="probe")
    register_lgpd_workers(harness, **seams)
    worker = harness.registry.get(_TOPIC)
    assert isinstance(worker, ExecuteRequestWorker)
    assert worker._legal_hold_predicate is esperado


# ---------------------------------------------------------------------------------------------
# 4. Guarda negativa de PHI (ADR-0006)
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("com_hold", [True, False])
def test_pseudonimo_e_texto_livre_nunca_saem_em_log_ou_mensagem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, com_hold: bool
) -> None:
    _registry(tmp_path, monkeypatch, com_hold=com_hold)
    worker = ExecuteRequestWorker(legal_hold_predicate=True)
    with capture_logs() as logs, pytest.raises((WorkerFailureError, PermissionError)) as exc:
        worker.run(_vars())
    blob = repr(logs) + str(exc.value)
    assert _PSEUDO not in blob
    assert "texto livre sintetico" not in blob
