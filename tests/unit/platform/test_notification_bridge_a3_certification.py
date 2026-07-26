"""T3.3 A3 — NotificationBridge negative-certification (unit-level pins).

PURPOSE (task A3, chaos program T3.3): PIN the bridge's exact current, R1-verified state so it
can never be silently mistaken for more (or less) than it is. This module does NOT test new
behavior — it certifies the CURRENT behavior against the REAL production payload shapes the
source BPMNs actually emit (never invented shapes), so that:

  - if a future edit accidentally ARMS one of the 5 repointed rules against today's minimal
    event payloads (e.g. someone widens a predicate, or a payload gets silently enriched without
    a conscious A3 update), the dormancy pins below go RED loudly;
  - if a future edit accidentally BREAKS the predicate wiring itself (e.g. a typo turns
    `desfecho == "encaminhada_recurso"` into a tautology), the liveness pins below go RED too —
    so the dormancy pins can never pass "vacuously" (matching nothing, forever, for the wrong
    reason);
  - if a future edit adds an unfenced/raw engine-start path to the bridge or its 3 in-flow
    workers, the AST guard below goes RED;
  - if CONTAS Phase 3 lands the fraude branch (or someone else does it first), the CONTAS→FRAUDE
    deferred pin goes RED, forcing a conscious re-certification instead of a silent behavior
    change.

REAL PAYLOAD SHAPES CITED (never invented — read directly from spec/ BPMNs, T3.3-A3 task
requirement #1):

  spec/processes/bpmn/SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn
    ST_PublishSemGlosa            (:116-118) event_desfecho=sem_glosa
                                    event_payload_vars=tenant_id,numero_lote_tiss,prestador_id
    ST_PublishEncaminhadaRecurso  (:276-278) event_desfecho=encaminhada_recurso
                                    event_payload_vars=tenant_id,numero_lote_tiss,glosa_id
    ST_PublishGlosaAceitaHumano   (:299-301) event_desfecho=glosa_aceita_humano
                                    event_payload_vars=tenant_id,numero_lote_tiss,
                                    codigo_glosa_aceito,analista_id
    ST_PublishReenviada           (:322-324) event_desfecho=reenviada
                                    event_payload_vars=tenant_id,numero_lote_tiss,prestador_id
    -> NO service task in this BPMN ever sets event_desfecho=encaminhada_fraude, and NO
       `camunda:topic="operadora.contas.start_fraude"` (or any *.start_fraude) service task
       exists anywhere in this file (grep-verified: the only contas.* topics are
       identify_glosa/calculate_impact/analyze_reason/prepare_triage_dossier/notify_sla_risk/
       start_recurso/register_glosa_accept/reconcile_payment — :85,94,142,186,217,267,290,313).

  spec/processes/bpmn/SP-OP-FRAUDE-001_Investigacao_Fraude.bpmn
    ST_PublishFraudeConfirmada    (:327-329) event_desfecho=fraude_confirmada_humano
                                    event_payload_vars=tenant_id,numero_caso,investigator_id,tier
    ST_StartCredenciamento        (:340-352, topic :341 operadora.fraude.start_credenciamento)
                                    event_desfecho=encaminhado_credenciamento
                                    event_payload_vars=tenant_id,numero_caso,investigator_id,tier
    ST_StartContratual            (:363-375, topic :364 operadora.fraude.start_contratual)
                                    event_desfecho=encaminhado_contratual
                                    event_payload_vars=tenant_id,numero_caso,investigator_id,tier
    ST_PublishEncaminhadoJuridico (:396-398) event_desfecho=encaminhado_juridico
    ST_PublishArquivado           (:413-415) event_desfecho=arquivado_sem_indicio
    ST_PublishMonitorar           (:430-432) event_desfecho=monitorar

Neither FRAUDE-completed shape (:352, :375) carries `prestador_id`, `numero_contrato`, or
`entidade_tipo` — exactly the anchors the bridge's CRED/CANCEL/INADIMPLENCIA rules fail-closed
require (notification_bridge.py :551,572,595-597).
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from maezo.platform.notification_bridge import (
    CONTAS_COMPLETED_EVENT,
    FRAUDE_COMPLETED_EVENT,
    NotificationBridge,
    build_cibseven_process_starter,
)
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from tests.support.audit_fakes import FakeStartAuditSink

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CONTAS_BPMN = _REPO_ROOT / "spec/processes/bpmn/SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn"
_FRAUDE_BPMN = _REPO_ROOT / "spec/processes/bpmn/SP-OP-FRAUDE-001_Investigacao_Fraude.bpmn"

# NOTE: this module deliberately mixes async (bridge behavior) and sync (AST/spec-text) tests.
# `pyproject.toml`'s `asyncio_mode = "auto"` treats every `async def test_*` here as an asyncio
# test automatically — no per-test/module marker needed (and a blanket module-level
# `pytest.mark.asyncio` would incorrectly tag the sync tests too).


class _RecordingCibSevenTransport(FakeCibSevenTransport):
    """Records every start_process_instance call — the "was an engine start attempted at all"
    surface every dormancy/liveness pin below asserts on."""

    def __init__(self) -> None:
        super().__init__()
        self.start_calls: list[tuple[str, str, dict[str, Any]]] = []

    async def start_process_instance(
        self, process_key: str, business_key: str, variables: dict[str, Any]
    ) -> Any:
        self.start_calls.append((process_key, business_key, dict(variables)))
        return await super().start_process_instance(process_key, business_key, variables)


def _fenced_bridge() -> tuple[NotificationBridge, _RecordingCibSevenTransport, FakeStartAuditSink]:
    """A bridge wired through the REAL fenced starter (`build_cibseven_process_starter`) — the
    ONLY sanctioned production path — with a recording transport + audit sink so every pin below
    observes the two effects that matter: an engine start, and a durable audit row."""
    transport = _RecordingCibSevenTransport()
    audit_sink = FakeStartAuditSink()
    starter = build_cibseven_process_starter(transport, audit_sink)
    bridge = NotificationBridge(cibseven_starter=starter)
    return bridge, transport, audit_sink


# =================================================================================================
# PIN 1 — Dormancy: REAL production payload shapes never arm the 5 repointed rules today.
#
# Each assertion message says exactly what must happen if this ever goes RED: "rule armed —
# update A3 certification" (per task instruction #1) — a future BPMN payload enrichment is a
# CONSCIOUS event, never a silent behavior change.
# =================================================================================================

_UPDATE_A3_MSG = (
    "rule armed — update A3 certification (spec/ payload was enriched with the missing anchor; "
    "this is expected to eventually happen per the module's own reconciliation note, but must be "
    "a CONSCIOUS re-certification, not a silent behavior change)"
)


async def test_pin_contas_recurso_dormant_on_real_encaminhada_recurso_payload() -> None:
    """CONTAS bpmn :276-278 ST_PublishEncaminhadaRecurso really emits ONLY
    tenant_id,numero_lote_tiss,glosa_id — NOT numero_guia_tiss. The RECURSO rule's fail-closed
    anchor requirement (numero_guia_tiss present) means it stays dormant against this exact,
    real payload shape."""
    bridge, transport, audit_sink = _fenced_bridge()
    real_payload = {
        "tenant_id": "amh",
        "numero_lote_tiss": "LOTE-REAL-001",
        "glosa_id": "GLOSA-REAL-001",
        # numero_guia_tiss deliberately ABSENT — matches the real BPMN payload_vars literal.
    }
    event_payload = dict(real_payload, desfecho="encaminhada_recurso")
    results = await bridge.on_event(event_type=CONTAS_COMPLETED_EVENT, payload=event_payload)

    assert all(r.target_process != "SP-OP-RECURSO-001" for r in results), _UPDATE_A3_MSG
    assert transport.start_calls == [], _UPDATE_A3_MSG
    assert audit_sink.calls == [], _UPDATE_A3_MSG


@pytest.mark.parametrize(
    "real_desfecho_payload",
    [
        {"desfecho": "sem_glosa", "tenant_id": "amh", "numero_lote_tiss": "L1", "prestador_id": "P1"},
        {
            "desfecho": "encaminhada_recurso",
            "tenant_id": "amh",
            "numero_lote_tiss": "L1",
            "glosa_id": "G1",
        },
        {
            "desfecho": "glosa_aceita_humano",
            "tenant_id": "amh",
            "numero_lote_tiss": "L1",
            "codigo_glosa_aceito": "COD1",
            "analista_id": "A1",
        },
        {"desfecho": "reenviada", "tenant_id": "amh", "numero_lote_tiss": "L1", "prestador_id": "P1"},
    ],
    ids=["sem_glosa", "encaminhada_recurso", "glosa_aceita_humano", "reenviada"],
)
async def test_pin_contas_fraude_dormant_across_every_real_contas_desfecho(
    real_desfecho_payload: dict[str, Any],
) -> None:
    """The CONTAS→FRAUDE rule keys on desfecho=='encaminhada_fraude' — a value the real CONTAS
    BPMN NEVER emits (its only 4 completed-event desfechos are sem_glosa/encaminhada_recurso/
    glosa_aceita_humano/reenviada, :118/:277/:300/:323). Feeding EVERY real desfecho the source
    process actually produces must never trigger a FRAUDE-targeted start — the strongest possible
    non-invented proof of dormancy for this rule (see also PIN 5, the source-level twin of this
    behavioral pin)."""
    bridge, transport, audit_sink = _fenced_bridge()
    results = await bridge.on_event(event_type=CONTAS_COMPLETED_EVENT, payload=real_desfecho_payload)

    assert all(r.target_process != "SP-OP-FRAUDE-001" for r in results), _UPDATE_A3_MSG
    assert all((start[0] != "SP-OP-FRAUDE-001") for start in transport.start_calls), _UPDATE_A3_MSG


async def test_pin_fraude_cred_dormant_on_real_encaminhado_credenciamento_payload() -> None:
    """FRAUDE bpmn :350-352 ST_StartCredenciamento's OWN publish task really emits ONLY
    tenant_id,numero_caso,investigator_id,tier — NOT prestador_id. The CRED rule's fail-closed
    anchor requirement (prestador_id present) means it stays dormant against this exact, real
    payload shape."""
    bridge, transport, audit_sink = _fenced_bridge()
    real_payload = {
        "tenant_id": "amh",
        "numero_caso": "CASO-REAL-001",
        "investigator_id": "INV-001",
        "tier": "alto",
        # prestador_id deliberately ABSENT — matches the real BPMN payload_vars literal.
    }
    results = await bridge.on_event(
        event_type=FRAUDE_COMPLETED_EVENT, payload=dict(real_payload, desfecho="encaminhado_credenciamento")
    )

    assert all(r.target_process != "SP-OP-CRED-001" for r in results), _UPDATE_A3_MSG
    assert transport.start_calls == [], _UPDATE_A3_MSG
    assert audit_sink.calls == [], _UPDATE_A3_MSG


async def test_pin_fraude_cancel_dormant_on_real_encaminhado_contratual_payload() -> None:
    """FRAUDE bpmn :373-375 ST_StartContratual's OWN publish task really emits ONLY
    tenant_id,numero_caso,investigator_id,tier — NOT numero_contrato. The CANCEL rule's
    fail-closed anchor requirement (numero_contrato present) means it stays dormant against this
    exact, real payload shape."""
    bridge, transport, audit_sink = _fenced_bridge()
    real_payload = {
        "tenant_id": "amh",
        "numero_caso": "CASO-REAL-002",
        "investigator_id": "INV-001",
        "tier": "alto",
        # numero_contrato deliberately ABSENT — matches the real BPMN payload_vars literal.
    }
    results = await bridge.on_event(
        event_type=FRAUDE_COMPLETED_EVENT, payload=dict(real_payload, desfecho="encaminhado_contratual")
    )

    assert all(r.target_process != "SP-OP-CANCEL-001" for r in results), _UPDATE_A3_MSG
    assert transport.start_calls == [], _UPDATE_A3_MSG
    assert audit_sink.calls == [], _UPDATE_A3_MSG


async def test_pin_fraude_inadimplencia_dormant_on_real_encaminhado_contratual_payload() -> None:
    """Same real payload as the CANCEL pin above: FRAUDE bpmn's real completed-event shape omits
    BOTH numero_contrato AND entidade_tipo — the INADIMPLENCIA rule requires BOTH (numero_contrato
    non-blank AND entidade_tipo=='contrato'), so it is doubly dormant against today's real
    payload."""
    bridge, transport, audit_sink = _fenced_bridge()
    real_payload = {
        "tenant_id": "amh",
        "numero_caso": "CASO-REAL-003",
        "investigator_id": "INV-001",
        "tier": "alto",
        # numero_contrato AND entidade_tipo deliberately ABSENT.
    }
    results = await bridge.on_event(
        event_type=FRAUDE_COMPLETED_EVENT, payload=dict(real_payload, desfecho="encaminhado_contratual")
    )

    assert all(r.target_process != "SP-OP-INADIMPLENCIA-001" for r in results), _UPDATE_A3_MSG
    assert transport.start_calls == [], _UPDATE_A3_MSG
    assert audit_sink.calls == [], _UPDATE_A3_MSG


# =================================================================================================
# PIN 2 — Liveness: one positive control per event-source family, so the dormancy pins above
# cannot be passing vacuously (e.g. because the event_type itself stopped matching, or predicate
# wiring silently broke into an always-False tautology).
# =================================================================================================


async def test_pin_liveness_contas_family_fires_when_anchor_present() -> None:
    """Positive control for the CONTAS-sourced family: the SAME event_type/desfecho as the
    dormancy pin above, but WITH the anchor the real payload today omits — proves the predicate
    wiring itself is alive (a broken/tautological predicate would fail this, not just the
    dormancy pins)."""
    bridge, transport, audit_sink = _fenced_bridge()
    enriched_payload = {
        "tenant_id": "amh",
        "desfecho": "encaminhada_recurso",
        "numero_lote_tiss": "LOTE-1",
        "glosa_id": "GLOSA-1",
        "numero_guia_tiss": "GUIA-1",  # the anchor a future spec/ enrichment would add
    }
    results = await bridge.on_event(event_type=CONTAS_COMPLETED_EVENT, payload=enriched_payload)

    matched = [r for r in results if r.target_process == "SP-OP-RECURSO-001"]
    assert len(matched) == 1 and matched[0].handoff_triggered is True
    assert len(transport.start_calls) == 1
    assert transport.start_calls[0][0] == "SP-OP-RECURSO-001"
    assert len(audit_sink.calls) == 1


async def test_pin_liveness_fraude_family_fires_when_anchor_present() -> None:
    """Positive control for the FRAUDE-sourced family (CRED rule): the SAME event_type/desfecho
    as the dormancy pin above, but WITH prestador_id present."""
    bridge, transport, audit_sink = _fenced_bridge()
    enriched_payload = {
        "tenant_id": "amh",
        "desfecho": "encaminhado_credenciamento",
        "numero_caso": "CASO-1",
        "investigator_id": "INV-1",
        "tier": "alto",
        "prestador_id": "PREST-1",  # the anchor a future spec/ enrichment would add
    }
    results = await bridge.on_event(event_type=FRAUDE_COMPLETED_EVENT, payload=enriched_payload)

    matched = [r for r in results if r.target_process == "SP-OP-CRED-001"]
    assert len(matched) == 1 and matched[0].handoff_triggered is True
    assert len(transport.start_calls) == 1
    assert transport.start_calls[0][0] == "SP-OP-CRED-001"
    assert len(audit_sink.calls) == 1


# =================================================================================================
# PIN 3 — Structural fence: no raw/unfenced engine-start path anywhere in the bridge module or
# the 3 in-flow handoff workers (mirrors the PR #104 / t1.10-te AST-guard lineage:
# tests/unit/tools/workers/test_harness_audited_refusal.py's `_handle_except_handlers` /
# `_calls_audit_guard_refusal` pattern, applied here to process-START instead of guard-refusal).
# =================================================================================================

_GUARDED_MODULES = {
    "notification_bridge": _REPO_ROOT / "src/maezo/platform/notification_bridge.py",
    "contas": _REPO_ROOT / "src/maezo/tools/workers/contas.py",
    "fraude": _REPO_ROOT / "src/maezo/tools/workers/fraude.py",
}

#: The ONLY sanctioned start-effect call in these modules. A future edit adding a second,
#: unfenced start path (a raw `transport.start_process_instance(...)` call, or a hand-rolled
#: `/process-definition/key/{key}/start` POST) must fail this guard.
_FENCED_CHOKEPOINT_CALL = "start_process_idempotent"

#: Forbidden raw-start call/attribute names — if any of these appear as a Call's function name
#: (or attribute) ANYWHERE in a guarded module, the fence is bypassed.
_FORBIDDEN_CALL_NAMES = {"start_process_instance"}

#: Forbidden literal substring — a hand-rolled raw REST path never appears as a string literal in
#: these modules (the only legitimate caller of this path is `transport.py`'s own
#: `CibSevenHttpTransport.start_process_instance`, which none of these 3 files re-implement).
_FORBIDDEN_PATH_SUBSTRING = "process-definition/key"


def _call_func_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _module_call_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_func_name(node)
            if name:
                names.add(name)
    return names


def _module_string_literals(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    literals: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            literals.add(node.value)
    return literals


@pytest.mark.parametrize("module_label", sorted(_GUARDED_MODULES))
def test_pin_no_raw_unfenced_start_call_in_guarded_module(module_label: str) -> None:
    """Static AST scan: none of `_FORBIDDEN_CALL_NAMES` appear as a called function/attribute
    name anywhere in the module — the ONLY way any of these 3 files can start a CIB Seven
    process is through `start_process_idempotent` (ADR-0007/T-C2 chokepoint)."""
    path = _GUARDED_MODULES[module_label]
    call_names = _module_call_names(path)
    forbidden_found = call_names & _FORBIDDEN_CALL_NAMES
    assert not forbidden_found, (
        f"{module_label} ({path}) calls {forbidden_found} directly — a RAW, unfenced engine-start "
        "path bypassing start_process_idempotent has been reintroduced (ADR-0007/T-C2 violation); "
        "update A3 certification only if this is a deliberate, reviewed exception"
    )


@pytest.mark.parametrize("module_label", sorted(_GUARDED_MODULES))
def test_pin_no_raw_rest_path_literal_in_guarded_module(module_label: str) -> None:
    """Static scan: no string literal in the module contains the raw REST start path — a
    hand-rolled `/process-definition/key/{key}/start` POST would bypass the fenced chokepoint
    entirely (this exact class of dead path was eliminated tree-wide in PR #104)."""
    path = _GUARDED_MODULES[module_label]
    literals = _module_string_literals(path)
    offending = {lit for lit in literals if _FORBIDDEN_PATH_SUBSTRING in lit}
    assert not offending, (
        f"{module_label} ({path}) contains a raw REST start path literal {offending} — bypasses "
        "the fenced start_process_idempotent chokepoint"
    )


@pytest.mark.parametrize("module_label", sorted(_GUARDED_MODULES))
def test_pin_guard_is_non_vacuous_fenced_call_actually_present(module_label: str) -> None:
    """Sanity: `start_process_idempotent` (or, for notification_bridge, its own import + call)
    IS actually called in the module — proves the two guards above are not passing merely
    because the module calls nothing at all (an empty/dead file would trivially pass them)."""
    path = _GUARDED_MODULES[module_label]
    call_names = _module_call_names(path)
    assert _FENCED_CHOKEPOINT_CALL in call_names, (
        f"{module_label} ({path}) does not call {_FENCED_CHOKEPOINT_CALL} at all — the "
        "no-unfenced-start guard above would be checking an empty surface"
    )


# =================================================================================================
# Spec-drift guard: the exact `event_payload_vars` literals the dormancy pins above hard-code as
# "the real production payload shape" must still match the spec/ BPMN text verbatim — if the
# spec/ author later touches these lines (e.g. reformats, or genuinely enriches the payload), this
# fails LOUDLY instead of letting the dormancy pins above silently test a shape that no longer
# reflects production.
# =================================================================================================


_EP = '<camunda:inputParameter name="event_payload_vars">{vars}</camunda:inputParameter>'
_ED = '<camunda:inputParameter name="event_desfecho">{desfecho}</camunda:inputParameter>'


@pytest.mark.parametrize(
    "expected_line",
    [
        _EP.format(vars="tenant_id,numero_lote_tiss,prestador_id"),
        _EP.format(vars="tenant_id,numero_lote_tiss,glosa_id"),
        _EP.format(vars="tenant_id,numero_lote_tiss,codigo_glosa_aceito,analista_id"),
    ],
    ids=["sem_glosa/reenviada", "encaminhada_recurso", "glosa_aceita_humano"],
)
def test_pin_contas_payload_var_literals_match_spec_verbatim(expected_line: str) -> None:
    """The exact `event_payload_vars` literal every CONTAS dormancy pin above assumes must be
    present verbatim in the spec/ BPMN — never invented, per task requirement #1."""
    xml = _CONTAS_BPMN.read_text(encoding="utf-8")
    assert expected_line in xml, (
        f"CONTAS bpmn no longer contains the literal {expected_line!r} — the real payload shape "
        "this A3 certification hard-codes has drifted from spec/; update A3 certification"
    )


@pytest.mark.parametrize(
    "expected_line",
    [
        _EP.format(vars="tenant_id,numero_caso,investigator_id,tier"),
        _ED.format(desfecho="encaminhado_credenciamento"),
        _ED.format(desfecho="encaminhado_contratual"),
    ],
    ids=["payload_vars_shared_shape", "cred_desfecho", "contratual_desfecho"],
)
def test_pin_fraude_payload_var_literals_match_spec_verbatim(expected_line: str) -> None:
    """The exact `event_payload_vars`/`event_desfecho` literals every FRAUDE dormancy pin above
    assumes must be present verbatim in the spec/ BPMN — never invented, per task requirement #1."""
    xml = _FRAUDE_BPMN.read_text(encoding="utf-8")
    assert expected_line in xml, (
        f"FRAUDE bpmn no longer contains the literal {expected_line!r} — the real payload shape "
        "this A3 certification hard-codes has drifted from spec/; update A3 certification"
    )


# =================================================================================================
# PIN 5 — CONTAS→FRAUDE handoff is Phase-3-deferred: no `encaminhada_fraude` desfecho, no
# in-flow `start_fraude` worker topic, anywhere in the CONTAS spec today.
# =================================================================================================


def test_pin_contas_never_emits_encaminhada_fraude_desfecho() -> None:
    """Source-level twin of the behavioral dormancy pin above: the CONTAS BPMN's own XML must
    never contain an `event_desfecho` of `encaminhada_fraude` — if it ever does, CONTAS Phase 3
    has landed the fraude branch and this MUST fail loudly (Phase-3 deferral, per the bridge
    module's own reconciliation note, notification_bridge.py :518-524)."""
    xml = _CONTAS_BPMN.read_text(encoding="utf-8")
    assert "encaminhada_fraude" not in xml, (
        "CONTAS bpmn now emits an 'encaminhada_fraude' desfecho — Phase-3 CONTAS→FRAUDE handoff "
        "has landed; update A3 certification (the bridge's CONTAS→FRAUDE rule and this pin's "
        "dormancy assumption are now stale)"
    )


def test_pin_no_in_flow_start_fraude_worker_topic_exists_in_contas_spec() -> None:
    """No `camunda:topic="operadora.contas.start_fraude"` (or any *.start_fraude in-flow worker)
    exists anywhere in the CONTAS spec today — the CONTAS→FRAUDE handoff has no in-flow
    counterpart the way RECURSO/CRED/CANCEL/INADIMPLENCIA do (Phase-3-deferred, per the bridge
    module's own reconciliation note)."""
    xml = _CONTAS_BPMN.read_text(encoding="utf-8")
    assert "start_fraude" not in xml, (
        "an in-flow start_fraude worker topic now exists in the CONTAS spec — Phase-3 "
        "CONTAS->FRAUDE handoff has landed; update A3 certification"
    )
