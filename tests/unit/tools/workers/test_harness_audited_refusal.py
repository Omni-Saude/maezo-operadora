"""Unit + architecture tests for the T-E AUDITED-REFUSAL chokepoint (T1.10; ADR-0007 / ADR-0030 F4).

Fast, no engine, no Postgres — `FakeWorkerTransport` + `FakeDmnTransport` + `FakeAuditSink`.

T-E records the *refusal decision* for every `*_NOT_HUMAN` / denial-class guard refusal (ADR-0030
finding F4: "no silent guard-refusals"; the hard co-requisite for enabling any such code as a modeled
BPMN error). It is BEST-EFFORT emit-before-refuse (design §2.1 row 2 / §7 "additive / already
fail-closed to a human via incident"), NOT the fail-closed GATE the T-C success path uses — a refusal
BLOCKS the adverse action (never an ADR-0007 world-effect, ADR-0030 §4), so a sink outage is logged
loudly and the refusal still fails closed to its incident (never fail-open, never a guard retry
forbidden by ADR-0008).

Coverage:
  1. each of the 3 exception shapes a guard refusal reaches the ladder in emits once, PHI-safe, before
     the report: PermissionError (`*NotHumanError`), FunctionWorker-reclassified `ValueError`, and
     `WorkerBpmnError` (demoted-incident AND allowlisted-boundary);
  2. sink-unavailable (missing sink + DB fault): refusal STILL reported (retries=0), never fail-open;
  3. non-guard failures (bad-input ValueError, fail-safe bpmnError, transient RuntimeError) NOT audited;
  4. exactly-once dedup on re-delivery + a distinct `refuse:` dedup namespace;
  5. dmn_versions captured on the refusal path;
  6. ARCH-TEST FENCE (cannot-bypass): the detector recognizes every guard/denial code the codebase
     raises, stays pinned equal to the CI gate's `is_te_gated`, and every guard except-branch of
     `_handle` invokes the refusal-audit chokepoint (a future guard refusal cannot silently bypass it).
"""

from __future__ import annotations

import ast
import functools
from pathlib import Path
from typing import Any

import pytest

import maezo.tools.workers as workers_pkg
from maezo.gateway.audit import AuditRecord
from maezo.gateway.audit_postgres import AuditPersistenceError
from maezo.tools.workers.base import FunctionWorker
from maezo.tools.workers.dmn_transport import FakeDmnTransport, evaluate_sync, first_row
from maezo.tools.workers.harness import (
    _DENIAL_BLOCK_CODES,
    AUDIT_AGENT_ID,
    AUDIT_DECISION_REFUSED,
    ExternalTask,
    FakeAuditSink,
    FakeWorkerTransport,
    WorkerBpmnError,
    WorkerHarness,
    extract_refusal_code,
    is_guard_refusal_code,
)

# `asyncio_mode = "auto"` (pyproject.toml) collects async def tests automatically.


def _task(
    *,
    task_id: str = "task-1",
    topic: str = "operadora.test.topic",
    variables: dict[str, Any] | None = None,
    retries: int | None = None,
) -> ExternalTask:
    return ExternalTask(
        task_id=task_id,
        topic=topic,
        process_instance_id="proc-1",
        business_key="bk-1",
        worker_id="w-1",
        variables=variables or {},
        retries=retries,
    )


class _OrderRecordingTransport(FakeWorkerTransport):
    """Shares a call-order log with `_OrderRecordingSink` to prove emit precedes the report."""

    def __init__(self, calls: list[str]) -> None:
        super().__init__()
        self._calls = calls

    async def handle_failure(self, task_id: str, worker_id: str, **kwargs: Any) -> None:
        self._calls.append("failure")
        await super().handle_failure(task_id, worker_id, **kwargs)

    async def handle_bpmn_error(self, task_id: str, worker_id: str, **kwargs: Any) -> None:
        self._calls.append("bpmn_error")
        await super().handle_bpmn_error(task_id, worker_id, **kwargs)


class _OrderRecordingSink(FakeAuditSink):
    def __init__(self, calls: list[str]) -> None:
        super().__init__()
        self._calls = calls

    async def emit_once(self, record: AuditRecord, *, dedup_key: str) -> str:
        self._calls.append("emit")
        return await super().emit_once(record, dedup_key=dedup_key)


# ---------------------------------------------------------------------------
# 1a. PermissionError (*NotHumanError) guard refusal — always audited
# ---------------------------------------------------------------------------


async def test_permission_error_guard_refusal_emits_once_phi_safe() -> None:
    transport = FakeWorkerTransport()
    sink = FakeAuditSink()
    harness = WorkerHarness(transport, worker_id="pod-xyz", tenant="amh", audit_sink=sink)

    async def handler(task: ExternalTask) -> dict[str, Any]:
        # The real *NotHumanError message convention: "ERR_<CODE>: <detail>".
        raise PermissionError("ERR_DESISTENCIA_NOT_HUMAN: desistencia requires human decision")

    harness.register("operadora.recurso.register_desistencia", handler)
    phi_vars = {"cpf": "12345678900", "numero_guia_tiss": "G-777", "nome": "João da Silva"}
    await harness._handle(_task(topic="operadora.recurso.register_desistencia", variables=phi_vars))

    # exactly one refusal record.
    assert len(sink.emitted) == 1
    record, dedup_key = sink.emitted[0]
    assert record.agent_id == AUDIT_AGENT_ID != "pod-xyz"  # stable service identity, not the pod
    assert record.tenant_id == "amh"
    assert record.action == "operadora.recurso.register_desistencia"
    assert record.decision == AUDIT_DECISION_REFUSED
    assert record.model_id is None and record.prompt_version is None
    assert dedup_key == "amh:refuse:task-1"  # distinct refuse: namespace
    # guard code recorded; raw PHI inputs hashed, NEVER stored.
    basis = record.details
    assert basis["guard_code"] == "ERR_DESISTENCIA_NOT_HUMAN"
    assert len(basis["input_sha256"]) == 64
    blob = str(basis)
    for leak in ("12345678900", "G-777", "João", "numero_guia_tiss", "cpf"):
        assert leak not in blob, f"PHI/identifier leaked into refusal decision_basis: {leak!r}"
    # the incident is still reported, retries=0 (ADR-0008 guard: never retried).
    assert transport.completed == []
    assert transport.failures[0][2] == 0


async def test_permission_error_guard_refusal_emits_before_incident() -> None:
    calls: list[str] = []
    transport = _OrderRecordingTransport(calls)
    sink = _OrderRecordingSink(calls)
    harness = WorkerHarness(transport, worker_id="w", tenant="amh", audit_sink=sink)

    async def handler(task: ExternalTask) -> dict[str, Any]:
        raise PermissionError("ERR_GLOSA_ACCEPT_NOT_HUMAN: glosa acceptance requires human decision")

    harness.register("t", handler)
    await harness._handle(_task(topic="t"))

    assert calls == ["emit", "failure"]  # durable refusal record precedes the incident report


async def test_permission_error_without_err_prefix_still_audited_with_fallback_code() -> None:
    """A `PermissionError` is ALWAYS the guard family here — audited even if the message carries no
    parseable ERR_* code (the specific code is best-effort; the fallback marker is recorded)."""
    transport = FakeWorkerTransport()
    sink = FakeAuditSink()
    harness = WorkerHarness(transport, worker_id="w", tenant="amh", audit_sink=sink)

    async def handler(task: ExternalTask) -> dict[str, Any]:
        raise PermissionError("blocked — human required")  # no ERR_* prefix

    harness.register("t", handler)
    await harness._handle(_task(topic="t"))

    assert len(sink.emitted) == 1
    assert sink.emitted[0][0].details["guard_code"] == "ERR_GUARD_NOT_HUMAN"


# ---------------------------------------------------------------------------
# 1b. Coded exception -> FunctionWorker reclassify -> ValueError guard refusal
# ---------------------------------------------------------------------------


async def test_coded_guard_exception_reclassified_to_valueerror_is_audited() -> None:
    """A real `CredError(ERR_DECRED_NOT_HUMAN, ...)` — reclassified by FunctionWorker to
    `ValueError("ERR_DECRED_NOT_HUMAN: ...")` (base.py:293) — is detected as a guard refusal and
    audited before the incident (the Shape-B path, end-to-end via `asyncio.to_thread`)."""
    from maezo.tools.workers.credenciamento import ERR_DECRED_NOT_HUMAN, CredError

    def entry(variables: dict[str, Any]) -> dict[str, Any]:
        raise CredError(ERR_DECRED_NOT_HUMAN, "descredenciamento requires human decision")

    transport = FakeWorkerTransport()
    sink = FakeAuditSink()
    harness = WorkerHarness(transport, worker_id="w", tenant="amh", audit_sink=sink)
    harness.register_worker(FunctionWorker("operadora.cred.register_descredenciamento", entry))

    await harness._handle(_task(topic="operadora.cred.register_descredenciamento"))

    assert len(sink.emitted) == 1
    record, dedup_key = sink.emitted[0]
    assert record.decision == AUDIT_DECISION_REFUSED
    assert record.details["guard_code"] == "ERR_DECRED_NOT_HUMAN"
    assert dedup_key == "amh:refuse:task-1"
    assert transport.failures[0][2] == 0  # incident, never retried


# ---------------------------------------------------------------------------
# 1c. WorkerBpmnError guard code — demoted-incident AND allowlisted-boundary
# ---------------------------------------------------------------------------


async def test_bpmn_error_guard_code_demoted_incident_is_audited() -> None:
    """A guard WorkerBpmnError with an EMPTY allowlist (production today) demotes to an incident —
    and T-E records the refusal before the demotion."""
    calls: list[str] = []
    transport = _OrderRecordingTransport(calls)
    sink = _OrderRecordingSink(calls)
    harness = WorkerHarness(transport, worker_id="w", tenant="amh", audit_sink=sink)  # empty allowlist

    async def handler(task: ExternalTask) -> dict[str, Any]:
        raise WorkerBpmnError("ERR_CANCEL_MANTER_NOT_HUMAN", "MANTER requires human justification")

    harness.register("operadora.cancel.confirm_maintained_decision", handler)
    await harness._handle(_task(topic="operadora.cancel.confirm_maintained_decision"))

    assert calls == ["emit", "failure"]
    record, _ = sink.emitted[0]
    assert record.decision == AUDIT_DECISION_REFUSED
    assert record.details["guard_code"] == "ERR_CANCEL_MANTER_NOT_HUMAN"
    assert transport.failures[0][2] == 0


async def test_bpmn_error_guard_code_allowlisted_boundary_is_audited() -> None:
    """The same guard code, when gate-proven (allowlisted), routes to a real bpmnError boundary — and
    T-E still records the refusal before the bpmnError. (This branch is what makes T-E the hard
    co-requisite of enabling the code — ADR-0030 §4.)"""
    calls: list[str] = []
    transport = _OrderRecordingTransport(calls)
    sink = _OrderRecordingSink(calls)
    harness = WorkerHarness(
        transport,
        worker_id="w",
        tenant="amh",
        audit_sink=sink,
        bpmn_error_allowlist=frozenset({"ERR_CANCEL_MANTER_NOT_HUMAN"}),
    )

    async def handler(task: ExternalTask) -> dict[str, Any]:
        raise WorkerBpmnError("ERR_CANCEL_MANTER_NOT_HUMAN")

    harness.register("t", handler)
    await harness._handle(_task(topic="t"))

    assert calls == ["emit", "bpmn_error"]  # refusal recorded before the modeled boundary fires
    assert len(sink.emitted) == 1
    assert transport.bpmn_errors and transport.bpmn_errors[0][1] == "ERR_CANCEL_MANTER_NOT_HUMAN"


# ---------------------------------------------------------------------------
# 2. Sink-unavailable: BEST-EFFORT — refusal still refuses, never fail-open
# ---------------------------------------------------------------------------


async def test_missing_sink_refusal_still_reported_never_fail_open() -> None:
    """No sink wired: the guard refusal is NOT blocked (that would fail-open or force a forbidden
    guard retry). It fails closed to its incident, retries=0, and nothing is (or can be) recorded."""
    transport = FakeWorkerTransport()
    harness = WorkerHarness(transport, worker_id="w", tenant="amh", max_retry_attempts=5)  # NO sink

    async def handler(task: ExternalTask) -> dict[str, Any]:
        raise PermissionError("ERR_CONTRACT_SUSPENSION_NOT_HUMAN: suspension requires human decision")

    harness.register("t", handler)
    await harness._handle(_task(topic="t", retries=4))

    assert transport.completed == []
    assert len(transport.failures) == 1
    assert transport.failures[0][2] == 0  # STILL retries=0 — guard never retried (ADR-0008 preserved)


async def test_sink_db_failure_refusal_still_reported_best_effort() -> None:
    """A DB fault (`AuditPersistenceError`) during the refusal emit is logged loudly and the refusal
    still proceeds to its incident — best-effort, not a gate (the refusal is not a world-effect)."""
    transport = FakeWorkerTransport()
    sink = FakeAuditSink()
    sink.always_fail = AuditPersistenceError("db down")
    harness = WorkerHarness(transport, worker_id="w", tenant="amh", audit_sink=sink)

    async def handler(task: ExternalTask) -> dict[str, Any]:
        raise PermissionError("ERR_REEMBOLSO_DENIAL_NOT_HUMAN: denial requires human decision")

    harness.register("t", handler)
    await harness._handle(_task(topic="t", retries=3))

    assert transport.completed == []
    assert transport.failures[0][2] == 0  # incident fires despite the audit failure (never fail-open)


# ---------------------------------------------------------------------------
# 3. Non-guard failures are NOT audited (T-E scopes to guard/denial only)
# ---------------------------------------------------------------------------


async def test_bad_input_value_error_not_audited() -> None:
    transport = FakeWorkerTransport()
    sink = FakeAuditSink()
    harness = WorkerHarness(transport, worker_id="w", tenant="amh", audit_sink=sink)

    async def handler(task: ExternalTask) -> dict[str, Any]:
        raise ValueError("ERR_PAGTO_ORDEM_INVALIDA: bad order")  # coded, but NOT a guard code

    harness.register("t", handler)
    await harness._handle(_task(topic="t"))

    assert sink.emitted == []  # origin-validation error, not a *_NOT_HUMAN/denial refusal
    assert transport.failures[0][2] == 0


async def test_fail_safe_bpmn_error_not_audited() -> None:
    transport = FakeWorkerTransport()
    sink = FakeAuditSink()
    harness = WorkerHarness(
        transport,
        worker_id="w",
        tenant="amh",
        audit_sink=sink,
        bpmn_error_allowlist=frozenset({"ERR_EVENT_PUBLISH_FAILED"}),
    )

    async def handler(task: ExternalTask) -> dict[str, Any]:
        raise WorkerBpmnError("ERR_EVENT_PUBLISH_FAILED")  # technical fail-safe, not a guard

    harness.register("t", handler)
    await harness._handle(_task(topic="t"))

    assert sink.emitted == []  # ADR-0030 §4: fail-safe codes are not T-E-hard-gated


async def test_transient_error_not_audited() -> None:
    transport = FakeWorkerTransport()
    sink = FakeAuditSink()
    harness = WorkerHarness(transport, worker_id="w", tenant="amh", audit_sink=sink, max_retry_attempts=3)

    async def handler(task: ExternalTask) -> dict[str, Any]:
        raise ConnectionError("engine flaky")  # transient infra fault, not a refusal

    harness.register("t", handler)
    await harness._handle(_task(topic="t", retries=3))

    assert sink.emitted == []
    assert transport.failures[0][2] == 2  # engine-computed retry (3-1), unchanged


# ---------------------------------------------------------------------------
# 4. Exactly-once dedup on re-delivery + distinct refuse: namespace
# ---------------------------------------------------------------------------


async def test_redelivered_guard_refusal_dedups_no_second_row() -> None:
    transport = FakeWorkerTransport()
    sink = FakeAuditSink()
    harness = WorkerHarness(transport, worker_id="w", tenant="amh", audit_sink=sink)

    async def handler(task: ExternalTask) -> dict[str, Any]:
        raise PermissionError("ERR_ANS_SUBMIT_NOT_HUMAN: ANS submission requires human approval")

    harness.register("t", handler)
    task = _task(topic="t", task_id="task-77")

    await harness._handle(task)
    await harness._handle(task)  # engine re-delivers the same task_id

    assert len(sink.emitted) == 1  # exactly-once: the re-delivery deduped (no 2nd refusal row)
    assert sink.emitted[0][1] == "amh:refuse:task-77"


async def test_refusal_namespace_distinct_from_completion() -> None:
    """A completion and a refusal on tasks sharing an id use DIFFERENT dedup keys (no cross-collision)
    — the refuse: namespace mirrors the T-C2 start: namespace."""
    transport = FakeWorkerTransport()
    sink = FakeAuditSink()
    harness = WorkerHarness(transport, worker_id="w", tenant="amh", audit_sink=sink)

    async def completes(task: ExternalTask) -> dict[str, Any]:
        return {"desfecho": "liberado_automatico"}

    async def refuses(task: ExternalTask) -> dict[str, Any]:
        raise PermissionError("ERR_DENIAL_NOT_HUMAN: requires human")

    harness.register("c", completes)
    harness.register("r", refuses)
    await harness._handle(_task(topic="c", task_id="x"))
    await harness._handle(_task(topic="r", task_id="x"))

    keys = {k for _, k in sink.emitted}
    assert keys == {"amh:x", "amh:refuse:x"}


# ---------------------------------------------------------------------------
# 5. dmn_versions captured on the refusal path
# ---------------------------------------------------------------------------


def _consult_dmn_then_refuse(variables: dict[str, Any], *, dmn: FakeDmnTransport) -> dict[str, Any]:
    rows, _version = evaluate_sync(dmn, "pagto_alcada", {"valor_pagamento_cents": 5000})
    first_row(rows, "pagto_alcada", variables)  # consulted before the guard fires
    from maezo.tools.workers.pagto import ERR_PAYMENT_RELEASE_NOT_HUMAN, PagtoError

    raise PagtoError(ERR_PAYMENT_RELEASE_NOT_HUMAN, "payment release requires human approval")


async def test_dmn_versions_captured_on_refusal() -> None:
    dmn = FakeDmnTransport()
    dmn.register(
        "pagto_alcada",
        [{"faixa_valor": "ALCADA_L1", "grupo_aprovador": "GRP_L1"}],
        version=7,
        definition_id="pagto_alcada:7:abc",
        deployment_id="dep-123",
    )
    transport = FakeWorkerTransport()
    sink = FakeAuditSink()
    harness = WorkerHarness(transport, worker_id="w", tenant="amh", audit_sink=sink)
    harness.register_worker(
        FunctionWorker(
            "operadora.pagto.release_payment", functools.partial(_consult_dmn_then_refuse, dmn=dmn)
        )
    )

    await harness._handle(_task(topic="operadora.pagto.release_payment"))

    assert len(sink.emitted) == 1
    record, _ = sink.emitted[0]
    assert record.decision == AUDIT_DECISION_REFUSED
    assert record.details["guard_code"] == "ERR_PAYMENT_RELEASE_NOT_HUMAN"
    # the DMN table consulted BEFORE the guard fired is captured in the refusal's provenance.
    assert record.dmn_versions == {
        "pagto_alcada": {"version": 7, "id": "pagto_alcada:7:abc", "deploymentId": "dep-123"}
    }


# ---------------------------------------------------------------------------
# 6. ARCH-TEST FENCE — a guard refusal CANNOT bypass the audit emit
# ---------------------------------------------------------------------------

_HARNESS_SRC = Path(__import__("maezo.tools.workers.harness", fromlist=["__file__"]).__file__)
_GUARD_EXCEPT_TYPES: frozenset[str] = frozenset({"PermissionError", "ValueError", "WorkerBpmnError"})


def _worker_source_guard_codes() -> set[str]:
    """Every `ERR_*_NOT_HUMAN` / denial-block code token appearing in any worker module source — the
    full guard/denial census, independent of which exception type raises it."""
    import re

    pkg_dir = Path(workers_pkg.__file__).parent
    codes: set[str] = set()
    pattern = re.compile(r"\bERR_[A-Z0-9_]+\b")
    for path in pkg_dir.glob("*.py"):
        for token in pattern.findall(path.read_text(encoding="utf-8")):
            if token.endswith("_NOT_HUMAN") or token in _DENIAL_BLOCK_CODES:
                codes.add(token)
    return codes


def test_detector_recognizes_every_guard_code_in_the_tree() -> None:
    """Census: the chokepoint's `is_guard_refusal_code` recognizes EVERY guard/denial code the worker
    modules carry — so no present guard refusal is missed, and (by the `_NOT_HUMAN` suffix rule) no
    FUTURE one can be either without also failing this census."""
    codes = _worker_source_guard_codes()
    # Sanity: the census is not empty (would silently pass an empty set otherwise).
    assert len(codes) >= 15, f"guard-code census unexpectedly small: {sorted(codes)}"
    assert "ERR_AUTH_DENIAL_INCOMPLETE" in codes  # the non-suffixed denial-block is present
    missed = {c for c in codes if not is_guard_refusal_code(c)}
    assert not missed, f"guard/denial codes the T-E detector does NOT recognize: {sorted(missed)}"


def test_detector_contem_o_gate() -> None:
    """A relacao entre os dois predicados e' CONTENCAO, e a direcao importa mais que a igualdade.

    ANTES (ate' 24/08/2026) este teste exigia IGUALDADE, e estava certo enquanto nenhum codigo
    tinha sido habilitado: "gated em T-E" e "precisa de recusa auditada" eram o mesmo conjunto.

    A partir da primeira habilitacao eles DIVERGEM, e a igualdade se torna perigosa. Habilitar
    `ERR_AUTH_DENIAL_INCOMPLETE` (25/08/2026) o tira do gate — e ele PRECISA continuar auditado,
    porque a auditoria da recusa e' justamente o que permitiu habilitar. Com o teste exigindo
    igualdade, alguem "consertaria" o vermelho tirando o codigo do detector tambem, e a
    habilitacao DESLIGARIA a auditoria em silencio, com a suite verde.

    A invariante que sobrevive a habilitacao:

        {ainda gated em T-E}  SUBSET-DE  {precisa de recusa auditada}

    Ou seja: todo codigo gated e' auditado, e um codigo habilitado continua auditado. O que o
    teste proibe e' o inverso — um codigo gated que NAO seja auditado.
    """
    from scripts.ci.check_bpmn_error_allowlist import TE_ENABLED_CODES
    from scripts.ci.check_bpmn_error_allowlist import _DENIAL_BLOCK_CODES as GATE_DENIAL_BLOCK
    from scripts.ci.check_bpmn_error_allowlist import is_te_gated

    assert _DENIAL_BLOCK_CODES == GATE_DENIAL_BLOCK  # the two denial-block sets cannot drift

    probe = _worker_source_guard_codes() | {
        "ERR_PAGTO_ORDEM_INVALIDA",  # origin-validation — NOT gated
        "ERR_EVENT_PUBLISH_FAILED",  # fail-safe — NOT gated
        "ERR_NIP_PROTOCOLO_INVALIDO",  # origin-validation — NOT gated
        "NOT_A_CODE",
    }
    for code in probe:
        if is_te_gated(code):
            assert is_guard_refusal_code(code), (
                f"{code!r} esta' gated em T-E e NAO seria auditado — e' exatamente a recusa "
                "silenciosa que o gate existe para impedir"
            )

    # E a metade que a habilitacao criou: codigo habilitado sai do gate e FICA na auditoria.
    for code in TE_ENABLED_CODES:
        assert not is_te_gated(code), f"{code!r} esta' em TE_ENABLED_CODES e ainda aparece gated"
        assert is_guard_refusal_code(code), (
            f"{code!r} foi habilitado e deixou de ser auditado — a habilitacao virou uma recusa "
            "silenciosa, que e' o pior desfecho possivel desta mudanca"
        )


def _handle_except_handlers() -> dict[str, ast.ExceptHandler]:
    """The `except <Type>:` handlers of `WorkerHarness._handle`, keyed by their exception-type name."""
    tree = ast.parse(_HARNESS_SRC.read_text(encoding="utf-8"), filename=str(_HARNESS_SRC))
    handle_fn: ast.AsyncFunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_handle":
            handle_fn = node
            break
    assert handle_fn is not None, "could not locate WorkerHarness._handle in the harness source"
    handlers: dict[str, ast.ExceptHandler] = {}
    for node in ast.walk(handle_fn):
        if isinstance(node, ast.ExceptHandler) and isinstance(node.type, ast.Name):
            handlers[node.type.id] = node
    return handlers


def _calls_audit_guard_refusal(handler: ast.ExceptHandler) -> bool:
    return any(
        isinstance(node, ast.Attribute) and node.attr == "_audit_guard_refusal" for node in ast.walk(handler)
    )


def test_every_guard_except_branch_invokes_the_refusal_chokepoint() -> None:
    """Structural fence: each guard-classified except branch of `_handle` (PermissionError,
    ValueError, WorkerBpmnError) MUST invoke `_audit_guard_refusal`. A future edit that drops the
    audit from any branch — reintroducing a silent guard-refusal — fails HERE (mirrors the P1
    import-fence / emit-before-complete arch-test precedents)."""
    handlers = _handle_except_handlers()
    for exc_type in _GUARD_EXCEPT_TYPES:
        assert exc_type in handlers, f"_handle no longer has an `except {exc_type}` branch"
        assert _calls_audit_guard_refusal(handlers[exc_type]), (
            f"`except {exc_type}` in _handle does NOT call `_audit_guard_refusal` — a guard refusal "
            "could bypass the T-E audit emit (ADR-0030 F4 regression)"
        )


@pytest.mark.parametrize(
    ("exc", "expected_code"),
    [
        (PermissionError("ERR_DESISTENCIA_NOT_HUMAN: x"), "ERR_DESISTENCIA_NOT_HUMAN"),
        (ValueError("ERR_DECRED_NOT_HUMAN: y"), "ERR_DECRED_NOT_HUMAN"),
        (WorkerBpmnError("ERR_CANCEL_MANTER_NOT_HUMAN"), "ERR_CANCEL_MANTER_NOT_HUMAN"),
        (ValueError("bad input"), None),  # non-guard
        (PermissionError("blocked"), None),  # no ERR_ prefix
    ],
)
def test_extract_refusal_code(exc: BaseException, expected_code: str | None) -> None:
    assert extract_refusal_code(exc) == expected_code


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("ERR_DECRED_NOT_HUMAN", True),
        ("ERR_CANCEL_MANTER_NOT_HUMAN", True),
        ("ERR_AUTH_DENIAL_INCOMPLETE", True),  # non-suffixed denial-block
        ("ERR_PAGTO_ORDEM_INVALIDA", False),
        ("ERR_EVENT_PUBLISH_FAILED", False),
        (None, False),
    ],
)
def test_is_guard_refusal_code(code: str | None, expected: bool) -> None:
    assert is_guard_refusal_code(code) is expected
