"""Unit tests for SP-OP-LGPD-DSR-001 workers — TDD London School.

Tests LGPD DSR workers: validate_identity, execute_export,
execute_rectification, execute_erasure.

CRITICAL: Workers must NEVER make adverse decisions (accusation of fraud, denial).

NOTE (#55 R-E, T2.8): `assess_request` (`AssessRequestWorker`) was RETIRED — routing
(`BRT_RotearDsr`) is a native engine-side DMN decision (`lgpd_dsr_routing`), never an external
task; the worker was unreachable by construction. Its tests were removed with it (see
`docs/compliance/lgpd-topic-reconciliation.md` R-E). `send_response`/`notify_sla_risk` (#55 R-F/
R-G raw handlers) are covered in `test_lgpd_send_response.py`/`test_lgpd_notify_sla_risk.py`.

NOTE (R-H, gap `LGPD-PUBLISH-COMPLETED-ORPHAN-TOPIC`): `publish_completed`
(`PublishCompletedWorker`) was RETIRED for the same reason, and THREE tests went with it —
`test_publish_completed_topic`, `test_publish_completed_publishes_event` and
`test_publish_completed_includes_desfecho`. They were not neutral coverage, but only ONE of the
three asserted the fabricated fact: `test_publish_completed_publishes_event` asserted
`status == "published"` / `event == "agents.events.lgpd_dsr.completed"` out of a SYNCHRONOUS
`WorkerBase.execute` with no publisher seam at all. The other two pinned the worker's topic
(`test_publish_completed_topic`) and its `desfecho` echo (`test_publish_completed_includes_desfecho`)
— a real reason to delete them WITH the worker, but not the same false-fact claim. All three would
have broken had the worker been made to return `{}` instead of being removed. The DSR's completion
is published by the BPMN's shared generic publisher (`ST_PublishCompleted` ->
`operadora.events.publish`, `bpmn:286-297`); that path is exercised by
`tests/unit/tools/workers/test_events.py`, not here. Deleting the tests WITH the worker is the
point — keeping them green against a rewritten worker would have preserved the fabrication.
"""

from __future__ import annotations

import pytest

from maezo.tools.workers import lgpd as lgpd_module
from maezo.tools.workers.base import ERR_DENIAL_NOT_HUMAN, ERR_FRAUD_ACCUSATION_NOT_HUMAN
from maezo.tools.workers.harness import (
    FakeKafkaPublisher,
    FakeWorkerTransport,
    WorkerBpmnError,
    WorkerFailureError,
    WorkerHarness,
)
from maezo.tools.workers.lgpd import (
    ExecuteErasureWorker,
    ExecuteExportWorker,
    ExecuteRectificationWorker,
    ValidateIdentityWorker,
    register_lgpd_workers,
)

# T3.1 (mirrors this file's own ADR-0031 `identidade_verificada` fail-closed matrix above):
# `human_approved` is now pinned to the explicit boolean `True` — NOT bare truthiness — in
# execute_export/execute_rectification/execute_erasure. Absent / False / None / any truthy junk
# (string incl. whitespace-only, int, list, dict) must NEVER be read as human approval. Shared
# vectors reused across each worker's own parametrized fail-closed test below.
_HUMAN_APPROVED_NON_TRUE_VECTORS: list[dict[str, object]] = [
    {},  # human_approved absent
    {"human_approved": False},  # explicit False
    {"human_approved": None},  # explicit None
    {"human_approved": "true"},  # garbage: truthy string, not the bool True
    {"human_approved": " "},  # garbage: whitespace-only truthy string
    {"human_approved": 1},  # garbage: truthy int, not the bool True
    {"human_approved": [1]},  # garbage: truthy list, not the bool True
    {"human_approved": {"ok": True}},  # garbage: truthy dict, not the bool True
]

# ---------------------------------------------------------------------------
# validate_identity
# ---------------------------------------------------------------------------


def test_validate_identity_topic() -> None:
    """validate_identity worker must have topic 'operadora.lgpd.verify_identity'."""
    worker = ValidateIdentityWorker()
    assert worker.topic == "operadora.lgpd.verify_identity"


def test_validate_identity_fail_closed_requires_explicit_verified_signal() -> None:
    """FAIL-CLOSED (T2.8): identity is confirmed ONLY on an explicit `identidade_verificada is
    True`. Pseudo_id present but no verified signal -> NOT confirmed (routes to the challenge)."""
    worker = ValidateIdentityWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "titular_pseudo_id": "pseudo-abc123",
            "canal": "portal",
            "identidade_verificada": True,
        }
    )

    assert result["identidade_confirmada"] is True
    assert result["status"] == "verified"


@pytest.mark.parametrize(
    "vars_extra",
    [
        {},  # identidade_verificada absent
        {"identidade_verificada": False},  # explicit False
        {"identidade_verificada": "true"},  # garbage (truthy string, not the bool True)
        {"identidade_verificada": 1},  # garbage (truthy int, not the bool True)
    ],
)
def test_validate_identity_fail_closed_rejects_non_true_signal(vars_extra: dict[str, object]) -> None:
    """The mere presence of the (obligatory) titular_pseudo_id NEVER confirms identity, and only
    the bool `True` confirms — absent/False/garbage -> NOT confirmed (fail-closed, anti eng.
    social). This is the fail-OPEN defect this change closes."""
    worker = ValidateIdentityWorker()

    result = worker.run(
        {"tenant_id": "amh", "titular_pseudo_id": "pseudo-abc123", "canal": "portal", **vars_extra}
    )

    assert result["identidade_confirmada"] is False
    assert result["status"] == "pending_proof"


def test_validate_identity_rejects_empty_pseudo_id() -> None:
    """validate_identity RAISES a modeled BPMN error when pseudo_id is empty/missing.

    Per GAP-LGPD-6: absent/empty titular_pseudo_id RAISES WorkerBpmnError(ERR_DSR_IDENTITY_
    UNVERIFIED) so the BE_IdentidadeInverificavel boundary can fire -> End_IdentidadeInverificavel.
    This is a TECHNICAL guard (impossibilidade mecanica), NEVER an accusation.
    """
    worker = ValidateIdentityWorker()

    with pytest.raises(WorkerBpmnError) as exc_info:
        worker.run(
            {
                "tenant_id": "amh",
                "titular_pseudo_id": "",
                "canal": "whatsapp",
            }
        )

    assert exc_info.value.error_code == "ERR_DSR_IDENTITY_UNVERIFIED"


def test_validate_identity_never_accuses_fraud() -> None:
    """validate_identity must NEVER automatically accuse fraud.

    L0 hard: fraud_accusation is intocavel. Even with missing data, the worker only reports
    inability to verify MECHANICALLY (a technical BPMN error), never a fraud accusation.
    """
    worker = ValidateIdentityWorker()

    with pytest.raises(WorkerBpmnError) as exc_info:
        worker.run(
            {
                "tenant_id": "amh",
                "titular_pseudo_id": None,  # missing
            }
        )

    exc = exc_info.value
    # Must be a technical, fail-safe outcome — never fraud-accusation language/code.
    assert exc.error_code == "ERR_DSR_IDENTITY_UNVERIFIED"
    assert exc.error_code != ERR_FRAUD_ACCUSATION_NOT_HUMAN
    assert "fraud" not in str(exc).lower()
    assert "acusacao" not in str(exc).lower()


# ---------------------------------------------------------------------------
# execute_erasure (LGPD art. 18)
# ---------------------------------------------------------------------------


def test_execute_erasure_topic() -> None:
    """execute_erasure worker must have topic 'operadora.lgpd.execute_erasure'."""
    worker = ExecuteErasureWorker()
    assert worker.topic == "operadora.lgpd.execute_erasure"


def test_execute_erasure_blocks_without_human_approval() -> None:
    """execute_erasure MUST block without human approval (DPO/juridico).

    L0 hard: data erasure is an adverse action — requires human authorization.
    """
    worker = ExecuteErasureWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "titular_pseudo_id": "pseudo-abc",
            "decisao_dsr": "EXECUTAR_E_ENVIAR",
            # no human_approved
        }
    )

    assert result["status"] == "blocked_by_guard"
    assert result["error_code"] == ERR_DENIAL_NOT_HUMAN


@pytest.mark.parametrize("vars_extra", _HUMAN_APPROVED_NON_TRUE_VECTORS)
def test_execute_erasure_fail_closed_rejects_non_true_human_approved(
    vars_extra: dict[str, object],
) -> None:
    """FAIL-CLOSED (T3.1): Guard 1 only accepts the literal `human_approved is True`.

    Absent/False/None/truthy-junk (string incl. whitespace-only, int, list, dict) must NEVER be
    read as human approval — closes the fail-OPEN class this change fixes.
    """
    worker = ExecuteErasureWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "titular_pseudo_id": "pseudo-abc",
            "decisao_dsr": "EXECUTAR_E_ENVIAR",
            **vars_extra,
        }
    )

    assert result["status"] == "blocked_by_guard"
    assert result["error_code"] == ERR_DENIAL_NOT_HUMAN


def test_execute_erasure_approved_fails_closed_not_success() -> None:
    """FAIL-CLOSED (T3.4-F3): even with BOTH guards satisfied (human_approved is True +
    decisao_dsr == EXECUTAR_E_ENVIAR), execute_erasure MUST NOT report success — real deletion
    is unimplemented, so it raises a NON-RETRIED incident (WorkerFailureError, retries_left=0)
    instead of the old fabricated ``status="erasure_completed"``.

    CONSCIOUS-FLIP GUARD: a future implementor wiring real deletion MUST rewrite this test — it
    cannot silently start returning ``erasure_completed``. Reporting a completed erasure without
    executing any SQL is a silent LGPD art. 18, VI violation (the titular is told their data is
    gone while it remains).
    """
    worker = ExecuteErasureWorker()

    with pytest.raises(WorkerFailureError) as exc_info:
        worker.run(
            {
                "tenant_id": "amh",
                "titular_pseudo_id": "pseudo-abc",
                "decisao_dsr": "EXECUTAR_E_ENVIAR",
                "human_approved": True,
                "fundamentacao_legal": "Art. 18, LGPD",
            }
        )

    # retries_left=0 => an IMMEDIATE engine incident, never a transient retry. Retrying a
    # not-implemented deletion accomplishes nothing; a human must be paged.
    assert exc_info.value.retries_left == 0
    assert "NAO esta implementada" in str(exc_info.value)


def test_execute_erasure_never_erases_without_decision() -> None:
    """execute_erasure must NEVER proceed when decisao_dsr is missing/invalid."""
    worker = ExecuteErasureWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "titular_pseudo_id": "pseudo-abc",
            # no decisao_dsr
            "human_approved": True,
        }
    )

    assert result["status"] != "erasure_completed"


def test_execute_erasure_respects_negativa_fundamentada() -> None:
    """execute_erasure must NOT erase when decisao_dsr == NEGAR_FUNDAMENTADO."""
    worker = ExecuteErasureWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "titular_pseudo_id": "pseudo-abc",
            "decisao_dsr": "NEGAR_FUNDAMENTADO",
            "fundamentacao_legal": "Retencao legal obrigatoria — Lei 13.787/2018",
            "human_approved": True,
        }
    )

    # Should NOT erase — this is a human decision to deny
    assert result["status"] == "erasure_blocked"
    assert result["reason"] == "negada_fundamentada"


# ---------------------------------------------------------------------------
# execute_export
# ---------------------------------------------------------------------------


def test_execute_export_topic() -> None:
    """execute_export worker topic."""
    worker = ExecuteExportWorker()
    assert worker.topic == "operadora.lgpd.execute_export"


def test_execute_export_compiles_data() -> None:
    """execute_export compiles a data package for the titular."""
    worker = ExecuteExportWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "titular_pseudo_id": "pseudo-abc",
            "decisao_dsr": "APROVAR_ENVIO",
            "human_approved": True,
        }
    )

    assert result["status"] == "export_compiled"
    assert "package_ref" in result


def test_execute_export_guard_blocks_without_human() -> None:
    """execute_export must block without human approval."""
    worker = ExecuteExportWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "titular_pseudo_id": "pseudo-abc",
            "decisao_dsr": "APROVAR_ENVIO",
        }
    )

    assert result["status"] == "blocked_by_guard"


@pytest.mark.parametrize("vars_extra", _HUMAN_APPROVED_NON_TRUE_VECTORS)
def test_execute_export_fail_closed_rejects_non_true_human_approved(
    vars_extra: dict[str, object],
) -> None:
    """FAIL-CLOSED (T3.1): execute_export only accepts the literal `human_approved is True`.

    Absent/False/None/truthy-junk (string incl. whitespace-only, int, list, dict) must NEVER be
    read as human approval — closes the fail-OPEN class this change fixes.
    """
    worker = ExecuteExportWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "titular_pseudo_id": "pseudo-abc",
            "decisao_dsr": "APROVAR_ENVIO",
            **vars_extra,
        }
    )

    assert result["status"] == "blocked_by_guard"
    assert result["error_code"] == ERR_DENIAL_NOT_HUMAN


# ---------------------------------------------------------------------------
# execute_rectification
# ---------------------------------------------------------------------------


def test_execute_rectification_topic() -> None:
    """execute_rectification worker topic."""
    worker = ExecuteRectificationWorker()
    assert worker.topic == "operadora.lgpd.execute_rectification"


def test_execute_rectification_applies_correction() -> None:
    """execute_rectification applies the requested data correction."""
    worker = ExecuteRectificationWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "titular_pseudo_id": "pseudo-abc",
            "decisao_dsr": "EXECUTAR_E_ENVIAR",
            "human_approved": True,
        }
    )

    assert result["status"] == "rectification_completed"
    assert result["data_type"] == "rectification"


def test_execute_rectification_guard_blocks_without_human() -> None:
    """execute_rectification must block without human approval (DPO/juridico)."""
    worker = ExecuteRectificationWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "titular_pseudo_id": "pseudo-abc",
            "decisao_dsr": "EXECUTAR_E_ENVIAR",
            # no human_approved
        }
    )

    assert result["status"] == "blocked_by_guard"
    assert result["error_code"] == ERR_DENIAL_NOT_HUMAN


@pytest.mark.parametrize("vars_extra", _HUMAN_APPROVED_NON_TRUE_VECTORS)
def test_execute_rectification_fail_closed_rejects_non_true_human_approved(
    vars_extra: dict[str, object],
) -> None:
    """FAIL-CLOSED (T3.1): execute_rectification only accepts the literal `human_approved is True`.

    Absent/False/None/truthy-junk (string incl. whitespace-only, int, list, dict) must NEVER be
    read as human approval — closes the fail-OPEN class this change fixes.
    """
    worker = ExecuteRectificationWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "titular_pseudo_id": "pseudo-abc",
            "decisao_dsr": "EXECUTAR_E_ENVIAR",
            **vars_extra,
        }
    )

    assert result["status"] == "blocked_by_guard"
    assert result["error_code"] == ERR_DENIAL_NOT_HUMAN


# ---------------------------------------------------------------------------
# R-H — a aposentadoria de `publish_completed` e uma CERCA, nao so uma delecao
# ---------------------------------------------------------------------------
#
# Espelha `test_cancel.py::test_register_cancel_workers_does_not_register_orphan_topics`, a mesma
# especie de defeito (topico de codigo orfao registrado sem service task de BPMN). Sem estas
# provas, apagar o worker seria uma mudanca que nada impede de voltar; com elas, o retorno do
# `PublishCompletedWorker` ou do seu registro fica VERMELHO aqui.

#: Os topicos `operadora.lgpd.*` que `register_lgpd_workers` DEVE registrar hoje — exatamente.
#: Os tres `execute_*` continuam sendo ORPHAN CODE TOPICS (linhas O2-O4 de
#: `docs/compliance/lgpd-topic-reconciliation.md`) e seguem aqui de proposito: o destino deles e
#: R-D (a recomposicao de `execute_request`), que e DPO/SME-sign-off-gated e NAO foi tocado por
#: R-H. Declara-los explicitamente e o que impede esta cerca de virar uma afirmacao vaga.
_LGPD_TOPICOS_REGISTRADOS = frozenset(
    {
        "operadora.lgpd.verify_identity",  # T1 — ALINHADO ao BPMN (ST_VerificarIdentidade)
        "operadora.lgpd.request_additional_proof",  # T2 — #55 R-B, handler cru
        "operadora.lgpd.send_response",  # T5 — #55 R-F, handler cru
        "operadora.lgpd.notify_sla_risk",  # T6 — #55 R-G, handler cru
        "operadora.lgpd.execute_export",  # O2 — orfao, gated em R-D (DPO/SME)
        "operadora.lgpd.execute_rectification",  # O3 — orfao, gated em R-D (DPO/SME)
        "operadora.lgpd.execute_erasure",  # O4 — orfao, gated em R-D (DPO/SME)
    }
)


def _lgpd_harness() -> WorkerHarness:
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="test-worker")
    register_lgpd_workers(harness, FakeKafkaPublisher())
    return harness


def test_register_lgpd_workers_nao_registra_o_topico_orfao_publish_completed() -> None:
    """R-H: `operadora.lgpd.publish_completed` NAO corresponde a `camunda:topic` algum do BPMN.

    `grep -rn "operadora.lgpd.publish_completed" spec/` -> 0 ocorrencias. Quem publica a conclusao
    do DSR e `ST_PublishCompleted` pelo topico generico `operadora.events.publish`.
    """
    assert "operadora.lgpd.publish_completed" not in _lgpd_harness().registered_topics


def test_o_modulo_lgpd_nao_expoe_mais_a_classe_publishcompletedworker() -> None:
    """A classe foi APAGADA, nao neutralizada: reintroduzi-la (mesmo sem registrar) e vermelho.

    Um worker sincrono sem seam de publisher que devolve `{"status": "published"}` afirma um fato
    falso — em terreno LGPD/DSR sujeito a auditoria — mesmo que o motor nunca o chame.
    """
    assert not hasattr(lgpd_module, "PublishCompletedWorker")


def test_o_conjunto_de_topicos_lgpd_registrados_e_exatamente_o_declarado() -> None:
    """Paridade EXATA: nem topico a menos (lacuna) nem a mais (novo orfao entrando de fininho)."""
    registrados = {t for t in _lgpd_harness().registered_topics if t.startswith("operadora.lgpd.")}
    assert registrados == _LGPD_TOPICOS_REGISTRADOS
