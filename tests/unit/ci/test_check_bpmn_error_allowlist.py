"""Unit tests for the boundary-proof allowlist gate (ADR-0030 §2, Tier-0).

Two layers:

1. **Pure-core** tests drive ``evaluate`` against synthetic ``SpecModel``/``WorkerModel`` fixtures
   — no filesystem — proving each clause: (b) catches a NEW unfenced raise / an uncovered raise,
   the b1/b2 dispatch-filter escape passes and its drift/leak variants fail, (c) is warn-only
   unless ``--strict-dead-models``, and ambiguity (an unresolvable raise) fails closed.
2. **Real-tree** tests run the full parser against ``spec/**`` + ``src/maezo/tools/workers`` and
   assert the gate PASSES and independently reproduces the ADR-0030 census (5 consumption-covered
   codes after T3.1 P2b; the three non-adverse technical fail-safes ``ERR_EVENT_PUBLISH_FAILED`` +
   ``ERR_DSR_IDENTITY_UNVERIFIED`` + ``ERR_RECURSO_INVALID_GLOSA`` Tier-0/Tier-2-enabled; the two
   guard/denial codes T-E-deferred; 10 dead-model warnings → 15 distinct spec codes).

SHARED FILE (T3.1 P2b flag for merge-time reconciliation): the census counts below
(``_G1_COVERED``, ``dead_models`` counts) move every time a branch wires a NEW ``WorkerBpmnError``
raise into a previously-dead-model spec code — reconcile against sibling in-flight branches at
merge time.
"""

from __future__ import annotations

from pathlib import Path

from scripts.ci.check_bpmn_error_allowlist import (
    BoundaryDecl,
    SpecModel,
    WorkerModel,
    WorkerRaise,
    build_spec_model,
    build_worker_model,
    evaluate,
    is_te_gated,
    run_gate,
)

# tests/unit/ci/<file> -> parents[3] == repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_BPMN_DIR = _REPO_ROOT / "spec" / "processes" / "bpmn"
_WORKERS_DIR = _REPO_ROOT / "src" / "maezo" / "tools" / "workers"

# Consumption-covered codes: G1's 3 + T2.8's ERR_DSR_IDENTITY_UNVERIFIED (lgpd verify_identity) +
# T3.1 P2b's ERR_RECURSO_INVALID_GLOSA (recurso request_documents/analyze_request guards) +
# t5-workers-f2's ERR_DECRED_NOT_HUMAN / ERR_CRED_DENIAL_NOT_HUMAN (cred adverse guards now raise
# WorkerBpmnError, mirroring ERR_CANCEL_MANTER_NOT_HUMAN — both T-E-deferred, so tier0 is unchanged) +
# t8-escalation-boundary's ERR_ESC_NOTIFY_FAILED (escalation notify_team/notify_supervisor handlers now
# raise it on publish failure; G2-fs technical fail-safe, NOT T-E-gated, so it ALSO lands in tier0) +
# item-9 bucket-3's ERR_NIP_PROTOCOLO_INVALIDO (nip handoff_ans_submit) / ERR_PROGRAMA_NO_CONSENT
# (programa check_consent) — both G2-val origin/consent guards now raise WorkerBpmnError, NOT
# T-E-gated, so they ALSO land in tier0 + item-9 bucket-3 Class-C's ERR_CRED_INVALID_PRESTADOR
# (cred validate_cred / verify_credentials origin guard, previously a DEAD MODEL: declared +
# boundary-modeled but zero raise-sites) — G2-val, NOT T-E-gated, also lands in tier0 +
# t2-ans-submit's ERR_ANS_PROTOCOLO_NACK (ans_submit transmit_to_ans, previously a DEAD MODEL:
# BE_SubmitNack was modeled but no worker could produce a NACK) and ERR_ANS_DATASET_INCOMPLETO
# (ans_submit prepare_submission, previously declared at definitions level with NO boundary at all —
# that branch adds BE_AssembleDatasetIncompleto on ST_AssembleDataset). Both are non-adverse
# technical/origin fail-safes routed to human remediation, NOT T-E-gated, so both land in tier0.
_G1_COVERED = frozenset(
    {
        "ERR_AUTH_DENIAL_INCOMPLETE",
        "ERR_CANCEL_MANTER_NOT_HUMAN",
        "ERR_EVENT_PUBLISH_FAILED",
        "ERR_DSR_IDENTITY_UNVERIFIED",
        "ERR_RECURSO_INVALID_GLOSA",
        "ERR_DECRED_NOT_HUMAN",
        "ERR_CRED_DENIAL_NOT_HUMAN",
        "ERR_ESC_NOTIFY_FAILED",
        "ERR_NIP_PROTOCOLO_INVALIDO",
        "ERR_PROGRAMA_NO_CONSENT",
        "ERR_CRED_INVALID_PRESTADOR",
        "ERR_ANS_PROTOCOLO_NACK",
        "ERR_ANS_DATASET_INCOMPLETO",
    }
)


# ---------------------------------------------------------------------------
# Real-tree: the gate passes and reproduces the ADR-0030 census
# ---------------------------------------------------------------------------


def test_real_tree_passes_and_reproduces_adr_census() -> None:
    result = run_gate(_BPMN_DIR, _WORKERS_DIR)
    assert result.ok, result.render()
    assert result.consumption_covered == _G1_COVERED
    assert result.tier0_enabled == frozenset(
        {
            "ERR_EVENT_PUBLISH_FAILED",
            "ERR_DSR_IDENTITY_UNVERIFIED",
            "ERR_RECURSO_INVALID_GLOSA",
            "ERR_ESC_NOTIFY_FAILED",
            "ERR_NIP_PROTOCOLO_INVALIDO",
            "ERR_PROGRAMA_NO_CONSENT",
            "ERR_CRED_INVALID_PRESTADOR",
            "ERR_ANS_PROTOCOLO_NACK",
            "ERR_ANS_DATASET_INCOMPLETO",
            # Habilitado em 25/08/2026: o unico codigo ADVERSO no tier0, e o unico que exigiu
            # o T-E aterrissar antes. Continua auditado pelo harness — ver TE_ENABLED_CODES.
            "ERR_AUTH_DENIAL_INCOMPLETE",
        }
    )
    # `ERR_AUTH_DENIAL_INCOMPLETE` SAIU do deferido em 25/08/2026 — habilitado, e por isso
    # aparece no tier0 acima. Os tres que restam sao da familia `*_NOT_HUMAN`, cuja habilitacao
    # e' follow-up por familia e nao aconteceu.
    assert result.te_deferred == frozenset(
        {
            "ERR_CANCEL_MANTER_NOT_HUMAN",
            "ERR_DECRED_NOT_HUMAN",
            "ERR_CRED_DENIAL_NOT_HUMAN",
        }
    )
    # ADR-0030 census: 16 distinct external-task boundary codes = 13 covered + 3 dead-model.
    # t2-ans-submit moved the needle twice: ERR_ANS_PROTOCOLO_NACK went dead-model -> covered (the
    # worker can now produce a NACK), and ERR_ANS_DATASET_INCOMPLETO is a NEW boundary (+1 to the
    # census total) that arrives already covered — so 15 -> 16 total and 4 -> 3 dead models.
    assert len(result.dead_models) == 3
    assert len(result.consumption_covered | result.dead_models) == 16


def test_real_tree_dead_models_are_warn_only_at_tier0() -> None:
    result = run_gate(_BPMN_DIR, _WORKERS_DIR)
    assert result.ok
    assert not result.violations
    assert result.warnings  # the 3 dead models are reported, but non-blocking (F5)


def test_real_tree_strict_mode_hardens_dead_models_to_failure() -> None:
    result = run_gate(_BPMN_DIR, _WORKERS_DIR, strict_dead_models=True)
    assert not result.ok  # Tier-3 posture: a dead model is a hard failure
    assert len(result.violations) == 3
    assert not result.warnings


# ---------------------------------------------------------------------------
# Clause (b): a raise must be consumption-covered — pure-core mutate tests
# ---------------------------------------------------------------------------


def _empty_spec() -> SpecModel:
    return SpecModel(boundary_decls=(), topic_consumers={}, process_event_topics={})


def test_gate_catches_new_unfenced_raise_with_no_boundary() -> None:
    # The regression this gate exists to catch: a worker adds `raise WorkerBpmnError("ERR_NEW")`
    # for a code with no spec boundary anywhere (the ERR_PUBLISH_MISSING_TOPIC shape).
    workers = WorkerModel(raises=(WorkerRaise("ERR_TOTALLY_NEW", "pagto", 70),), dispatch_filters={})
    result = evaluate(_empty_spec(), workers)
    assert not result.ok
    assert any("ERR_TOTALLY_NEW" in v and "uncatalogued" in v for v in result.violations)
    assert "ERR_TOTALLY_NEW" not in result.consumption_covered
    assert "ERR_TOTALLY_NEW" not in result.tier0_enabled


def test_gate_catches_raise_uncovered_on_multi_consumer_topic() -> None:
    # topicT is consumed by P1 AND P2 but the boundary is declared only in P1; no dispatch filter
    # proves coverage -> not consumption-covered (simple rule fails).
    spec = SpecModel(
        boundary_decls=(BoundaryDecl("P1", "topicT", "ERR_X", "ST_1", None),),
        topic_consumers={"topicT": frozenset({"P1", "P2"})},
        process_event_topics={},
    )
    workers = WorkerModel(raises=(WorkerRaise("ERR_X", "mod", 1),), dispatch_filters={})
    result = evaluate(spec, workers)
    assert not result.ok
    assert any("P2" in v and "clause (b)" in v for v in result.violations)
    assert "ERR_X" not in result.consumption_covered


def test_simple_rule_covers_single_consumer_declared_boundary() -> None:
    spec = SpecModel(
        boundary_decls=(BoundaryDecl("AUTH", "op.auth", "ERR_A", "ST", None),),
        topic_consumers={"op.auth": frozenset({"AUTH"})},
        process_event_topics={},
    )
    workers = WorkerModel(raises=(WorkerRaise("ERR_A", "auth", 1),), dispatch_filters={})
    result = evaluate(spec, workers)
    assert result.ok, result.render()
    assert result.consumption_covered == frozenset({"ERR_A"})


# ---------------------------------------------------------------------------
# Clause (b) dispatch-filter escape (b1/b2) — the events.publish pattern
# ---------------------------------------------------------------------------

_PUBLISH_TOPIC = "operadora.events.publish"
_CODE = "ERR_EVENT_PUBLISH_FAILED"


def _events_spec(*, other_reuses_domain_topic: bool = False) -> SpecModel:
    """A publish topic consumed by ESC + OTHER; boundary declared only in ESC on 2 activities."""
    other_topics = frozenset({"evt.req"} if other_reuses_domain_topic else {"evt.other"})
    return SpecModel(
        boundary_decls=(
            BoundaryDecl("ESC", _PUBLISH_TOPIC, _CODE, "ST_A", "evt.req"),
            BoundaryDecl("ESC", _PUBLISH_TOPIC, _CODE, "ST_B", "evt.res"),
        ),
        topic_consumers={_PUBLISH_TOPIC: frozenset({"ESC", "OTHER"})},
        process_event_topics={"ESC": frozenset({"evt.req", "evt.res"}), "OTHER": other_topics},
    )


def test_dispatch_filter_escape_passes_when_b1_and_b2_hold() -> None:
    spec = _events_spec()
    workers = WorkerModel(
        raises=(WorkerRaise(_CODE, "events", 262),),
        dispatch_filters={_PUBLISH_TOPIC: frozenset({"evt.req", "evt.res"})},
    )
    result = evaluate(spec, workers)
    assert result.ok, result.render()
    assert _CODE in result.consumption_covered


def test_dispatch_filter_b1_drift_fails() -> None:
    # Worker filter diverges from the boundary-activity event_topic set (missing evt.res).
    spec = _events_spec()
    workers = WorkerModel(
        raises=(WorkerRaise(_CODE, "events", 262),),
        dispatch_filters={_PUBLISH_TOPIC: frozenset({"evt.req"})},
    )
    result = evaluate(spec, workers)
    assert not result.ok
    assert any("b1" in v for v in result.violations)


def test_dispatch_filter_b2_leak_fails() -> None:
    # Another process reuses an escalation domain topic -> a publish failure there would wrongly
    # route as ERR_EVENT_PUBLISH_FAILED.
    spec = _events_spec(other_reuses_domain_topic=True)
    workers = WorkerModel(
        raises=(WorkerRaise(_CODE, "events", 262),),
        dispatch_filters={_PUBLISH_TOPIC: frozenset({"evt.req", "evt.res"})},
    )
    result = evaluate(spec, workers)
    assert not result.ok
    assert any("b2" in v for v in result.violations)


# ---------------------------------------------------------------------------
# Clause (c) + T-E classification + fail-closed
# ---------------------------------------------------------------------------


def test_dead_model_is_warn_only_then_strict() -> None:
    spec = SpecModel(
        boundary_decls=(BoundaryDecl("P1", "op.x", "ERR_UNRAISED", "ST", None),),
        topic_consumers={"op.x": frozenset({"P1"})},
        process_event_topics={},
    )
    workers = WorkerModel(raises=(), dispatch_filters={})
    warn = evaluate(spec, workers)
    assert warn.ok
    assert warn.dead_models == frozenset({"ERR_UNRAISED"})
    assert any("ERR_UNRAISED" in w for w in warn.warnings)
    strict = evaluate(spec, workers, strict_dead_models=True)
    assert not strict.ok
    assert any("ERR_UNRAISED" in v for v in strict.violations)


def test_is_te_gated_matches_adr_pattern() -> None:
    assert is_te_gated("ERR_CANCEL_MANTER_NOT_HUMAN")
    assert is_te_gated("ERR_DECRED_NOT_HUMAN")
    assert is_te_gated("ERR_CONTRACT_SUSPENSION_NOT_HUMAN")
    # HABILITADO em 25/08/2026 (TE_ENABLED_CODES): sai do gate e CONTINUA auditado pelo
    # harness. Ver `is_te_gated` e `test_detector_contem_o_gate`.
    assert not is_te_gated("ERR_AUTH_DENIAL_INCOMPLETE")
    assert is_te_gated("ERR_OUTRO_DENIAL_BLOCK_HIPOTETICO_NOT_HUMAN")
    assert not is_te_gated("ERR_EVENT_PUBLISH_FAILED")
    assert not is_te_gated("ERR_PAGTO_ORDEM_INVALIDA")


def test_te_gated_covered_code_is_deferred_not_enabled() -> None:
    # A correctly-raised, consumption-covered guard code is NOT a violation, but is deferred.
    spec = SpecModel(
        boundary_decls=(BoundaryDecl("CANCEL", "op.c", "ERR_CANCEL_MANTER_NOT_HUMAN", "ST", None),),
        topic_consumers={"op.c": frozenset({"CANCEL"})},
        process_event_topics={},
    )
    workers = WorkerModel(
        raises=(WorkerRaise("ERR_CANCEL_MANTER_NOT_HUMAN", "cancel", 357),), dispatch_filters={}
    )
    result = evaluate(spec, workers)
    assert result.ok
    assert result.consumption_covered == frozenset({"ERR_CANCEL_MANTER_NOT_HUMAN"})
    assert result.te_deferred == frozenset({"ERR_CANCEL_MANTER_NOT_HUMAN"})
    assert result.tier0_enabled == frozenset()  # NOT enabled pre-T-E


def test_unresolvable_raise_fails_closed() -> None:
    workers = WorkerModel(raises=(), dispatch_filters={}, unresolved_raises=(("events", 200),))
    result = evaluate(_empty_spec(), workers)
    assert not result.ok
    assert any("unresolvable" in v for v in result.violations)


def test_unresolved_filter_fails_closed() -> None:
    workers = WorkerModel(raises=(), dispatch_filters={}, unresolved_filters=("_SOME_FILTER (not found)",))
    result = evaluate(_empty_spec(), workers)
    assert not result.ok
    assert any("dispatch filter unresolved" in v for v in result.violations)


# ---------------------------------------------------------------------------
# Parsers: build_spec_model (external-only) + build_worker_model (AST resolution)
# ---------------------------------------------------------------------------

_BPMN_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
                  xmlns:camunda="http://camunda.org/schema/1.0/bpmn">
  <bpmn:error id="Err_A" name="A" errorCode="ERR_A"/>
  <bpmn:error id="Err_B" name="B" errorCode="ERR_B"/>
  <bpmn:process id="P1">
    <bpmn:serviceTask id="ST_Ext" camunda:type="external" camunda:topic="topic.a">
      <bpmn:extensionElements>
        <camunda:inputOutput>
          <camunda:inputParameter name="event_topic">evt.a</camunda:inputParameter>
        </camunda:inputOutput>
      </bpmn:extensionElements>
    </bpmn:serviceTask>
    <bpmn:userTask id="UT_Human"/>
    <bpmn:boundaryEvent id="BE_Ext" attachedToRef="ST_Ext">
      <bpmn:errorEventDefinition id="ED_A" errorRef="Err_A"/>
    </bpmn:boundaryEvent>
    <bpmn:boundaryEvent id="BE_Human" attachedToRef="UT_Human">
      <bpmn:errorEventDefinition id="ED_B" errorRef="Err_B"/>
    </bpmn:boundaryEvent>
  </bpmn:process>
</bpmn:definitions>
"""


def test_build_spec_model_counts_external_boundaries_only(tmp_path: Path) -> None:
    (tmp_path / "p.bpmn").write_text(_BPMN_FIXTURE, encoding="utf-8")
    spec = build_spec_model(tmp_path)
    # ERR_B's boundary is attached to a userTask -> excluded (mirrors fraude's non-external catch).
    assert spec.spec_codes() == frozenset({"ERR_A"})
    assert spec.topic_consumers == {"topic.a": frozenset({"P1"})}
    assert spec.process_event_topics == {"P1": frozenset({"evt.a"})}
    assert len(spec.boundary_decls) == 1
    assert spec.boundary_decls[0].event_topic == "evt.a"


def test_build_spec_model_parse_error_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "bad.bpmn").write_text("<not-well-formed", encoding="utf-8")
    spec = build_spec_model(tmp_path)
    assert spec.parse_errors
    result = evaluate(spec, WorkerModel(raises=(), dispatch_filters={}))
    assert not result.ok
    assert any("parse error" in v.lower() for v in result.violations)


def test_build_worker_model_resolves_cross_module_constant(tmp_path: Path) -> None:
    (tmp_path / "codes.py").write_text('ERR_FOO = "ERR_FOO_VALUE"\n', encoding="utf-8")
    (tmp_path / "w.py").write_text(
        "from codes import ERR_FOO\n\n\ndef f():\n    raise WorkerBpmnError(ERR_FOO, 'm')\n",
        encoding="utf-8",
    )
    model = build_worker_model(tmp_path)
    assert WorkerRaise("ERR_FOO_VALUE", "w", 5) in model.raises
    assert not model.unresolved_raises


def test_build_worker_model_flags_dynamic_raise(tmp_path: Path) -> None:
    (tmp_path / "w.py").write_text("def f(code):\n    raise WorkerBpmnError(code)\n", encoding="utf-8")
    model = build_worker_model(tmp_path)
    assert model.unresolved_raises == (("w", 2),)
    assert not model.raises


def test_build_worker_model_resolves_registry_filter_from_real_events() -> None:
    # The real events.py defines `_ESCALATION_PUBLISH_BPMN_ERROR_TOPICS` as a frozenset of 4 domain
    # topics — the gate must resolve it (not leave it unresolved).
    model = build_worker_model(_WORKERS_DIR)
    assert not model.unresolved_filters, model.unresolved_filters
    filt = model.dispatch_filters.get("operadora.events.publish")
    assert filt is not None
    assert filt == frozenset(
        {
            "agents.events.escalation.requested",
            "agents.events.escalation.sla_breached",
            "agents.events.escalation.resolved",
            "agents.events.process_completed",
        }
    )
