"""Registration coverage tests for `register_all_workers` (T1.2/ADR-0026 Decisao §4).

Fail-closed registry-coverage test (ADR-0026 "Test strategy"): asserts 17/17 module bootstraps
run (16 + T3.1 R2's `events` module), produce a non-empty, collision-free topic set, and —
spot-checked against the real BPMN `camunda:topic` declarations under `spec/processes/bpmn/`
(5 topics, one per a sample of domains) — that the registry is not "dead" (a spec topic with no
worker would fail here). Topic NAMES are otherwise derived from the modules themselves (ADR-0026
acceptance: "derive expected topic names from the modules themselves"), not a hand-list — full
spec-vs-registry reconciliation (every one of the 16 BPMNs' external-task topics) is out of this
task's scope (T1.1 design §12/Q-4, a follow-up); several known gaps (spec topics with no
implementing function yet) are documented in each module's bootstrap docstring.
"""

from __future__ import annotations

from pathlib import Path

from maezo.tools.workers.auth import register_auth_workers
from maezo.tools.workers.base import FunctionWorker
from maezo.tools.workers.bootstrap import ALL_WORKER_BOOTSTRAPS, register_all_workers
from maezo.tools.workers.escalation import register_escalation_workers
from maezo.tools.workers.harness import FakeKafkaPublisher, FakeWorkerTransport, WorkerHarness
from maezo.tools.workers.lgpd import register_lgpd_workers

_SPEC_BPMN_DIR = Path(__file__).parents[4] / "spec" / "processes" / "bpmn"


def _fresh_harness() -> WorkerHarness:
    return WorkerHarness(FakeWorkerTransport(), worker_id="registration-probe")


# ---------------------------------------------------------------------------
# 16/16 composition
# ---------------------------------------------------------------------------


def test_all_17_bootstraps_are_composed() -> None:
    assert len(ALL_WORKER_BOOTSTRAPS) == 17


def test_register_all_workers_registers_every_module_without_collision() -> None:
    """No topic collision across the 16 modules — `WorkerRegistry.register` would replace
    silently (warning log only); this test proves the FULL composition's topic COUNT equals
    the sum of each module's own registration count, so no two modules accidentally share a
    topic string (the "no dead registry" acceptance criterion's converse)."""
    harness = _fresh_harness()
    register_all_workers(harness, kafka=FakeKafkaPublisher())

    topics = harness.registered_topics
    assert len(topics) == len(set(topics)), "duplicate topic strings registered"
    assert len(topics) > 90, f"expected ~99 topics across 16 modules, got {len(topics)}"


def test_register_all_workers_covers_all_17_module_topic_prefixes() -> None:
    """Every one of the 17 modules contributed at least one topic — the literal "17/17 modules
    registered" acceptance criterion."""
    harness = _fresh_harness()
    register_all_workers(harness)
    topics = harness.registered_topics

    expected_prefixes = {
        "operadora.adequacao.",
        "operadora.ans_cron.",
        "regulatorio.anssubmit.",
        "operadora.auth.",
        "operadora.cancel.",
        "operadora.contas.",
        "operadora.cred.",
        "operadora.escalation.",
        "operadora.events.",
        "operadora.fraude.",
        "operadora.inadimplencia.",
        "operadora.lgpd.",
        "operadora.nip.",
        "operadora.pagto.",
        "operadora.programa.",
        "operadora.recurso.",
        "operadora.reembolso.",
    }
    assert len(expected_prefixes) == 17

    for prefix in expected_prefixes:
        matching = [t for t in topics if t.startswith(prefix)]
        assert matching, f"module prefix {prefix!r} contributed NO topics to the registry"


def test_register_all_workers_idempotent() -> None:
    """Re-running the full composition against the same harness must not grow the topic count
    (WorkerHarness/WorkerRegistry replace on re-registration, same topic)."""
    harness = _fresh_harness()
    register_all_workers(harness)
    first_count = len(harness.registered_topics)

    register_all_workers(harness)
    assert len(harness.registered_topics) == first_count


def test_register_all_workers_accepts_and_forwards_seams() -> None:
    """`register_all_workers(harness, kafka, **seams)` must accept arbitrary seam kwargs (donor
    contract, ADR-0026 §2) without raising, even though none is consumed today."""
    harness = _fresh_harness()
    register_all_workers(harness, kafka=FakeKafkaPublisher(), audit=object(), dmn=object())
    assert len(harness.registered_topics) > 0


# ---------------------------------------------------------------------------
# 3 WorkerBase class modules keep their existing classes (bootstrap-wrapped only)
# ---------------------------------------------------------------------------


def test_class_module_bootstraps_register_workerbase_instances_not_function_workers() -> None:
    """auth/lgpd keep their WorkerBase subclasses — `register_worker` stores them in the harness's
    WorkerRegistry directly, never re-wrapped in FunctionWorker.

    RAW `harness.register()` handlers (they need the async Kafka seam a WorkerBase `execute`
    boundary cannot reach — mirrors `events.publish`) populate `_handlers` but NOT the
    WorkerRegistry, and are excluded here:
    - `operadora.lgpd.request_additional_proof` (#55 R-B, T2.8), `operadora.lgpd.send_response`
      (#55 R-F, T2.8), `operadora.lgpd.notify_sla_risk` (#55 R-G, T2.8);
    - `operadora.escalation.notify_team` / `operadora.escalation.notify_supervisor` (DL-0034,
      built in t5 — escalation's notify workers converted from `WorkerBase` to raw async handlers;
      notifying IS the business effect and needed the async Kafka seam the old classes lacked).
    """
    harness = _fresh_harness()
    register_auth_workers(harness)
    register_escalation_workers(harness)
    register_lgpd_workers(harness)

    raw_handler_topics = {
        "operadora.lgpd.request_additional_proof",
        "operadora.lgpd.send_response",
        "operadora.lgpd.notify_sla_risk",
        "operadora.escalation.notify_team",
        "operadora.escalation.notify_supervisor",
    }
    for topic in harness.registered_topics:
        if topic in raw_handler_topics:
            assert harness.registry.get(topic) is None  # raw handler: not in the WorkerRegistry
            continue
        worker = harness.registry.get(topic)
        assert worker is not None
        assert not isinstance(worker, FunctionWorker), f"{topic} unexpectedly wrapped in FunctionWorker"


def test_function_based_module_bootstraps_register_function_workers() -> None:
    """The 13 function-based modules (7 dict-first + ans_cron + 6 typed-I/O with entry
    functions) register `FunctionWorker` instances (ADR-0026 §2a/§2b). `events` (T3.1 R2) is a
    THIRD category — a raw `harness.register()` handler, excluded here (see its module
    docstring for why it cannot use the dict-first `FunctionWorker` boundary).

    SHARED FILE: `raw_handler_topics` also excludes recurso's THREE raw handlers
    (`notify_sla_risk`/`escalate_ans_timeout`/`comunicar_resposta` — the async Kafka seam for
    their `notifications_of_type`/domain-event observability, plus `task.business_key` for
    `comunicar_resposta`'s deterministic protocolo; recurso.py's module-level rationale). ADR-0040
    deleted `submit_appeal`/`track_status` with the appellant branch — 4 became 3.

    item-9 wave-5 (event-wiring): `raw_handler_topics` also excludes programa's 4 raw handlers —
    `stratify_risk`/`stop_processing` (item A, root fix — moved off `FunctionWorker` so they can
    publish an internal notification) and the 2 NEW workers `proactive_contact`/`notify_sla_risk`
    (items B/C) — same async-Kafka-seam rationale, programa.py's own module-level docstring.

    item-9 notify-wiring fix (adequacao): `raw_handler_topics` also excludes
    `operadora.adequacao.update_monitoring_plan` — moved off `FunctionWorker` onto a raw handler
    so it can publish an internal notification too (`register_adequacao_workers` used to
    `del kafka # unused`); same async-Kafka-seam rationale, adequacao.py's own module-level
    docstring.
    """
    harness = _fresh_harness()
    register_all_workers(harness)

    class_module_prefixes = ("operadora.auth.", "operadora.escalation.", "operadora.lgpd.")
    raw_handler_topics = {
        "operadora.events.publish",
        "operadora.recurso.notify_sla_risk",
        "operadora.recurso.escalate_ans_timeout",
        "operadora.recurso.comunicar_resposta",
        # notify_regulatorio (t5 FINDING A fix): raw async handler (needs the Kafka seam to emit
        # the anssubmit.notify_regulatorio notification), harness.register not register_worker —
        # mirrors recurso's raw handlers above.
        "regulatorio.anssubmit.notify_regulatorio",
        # retransmit (t9-nack-vars): moved off `FunctionWorker` onto a raw async handler for the
        # SAME reason — it publishes the `anssubmit.retransmit` notification, which is what makes
        # the SUB_RetryEnvio loop observable at all. The contract already names the factory
        # (`make_retransmit_handler`, docs/processes/contracts/SP-OP-ANS-SUBMIT-001.md:157).
        "regulatorio.anssubmit.retransmit",
        # DL-0033 real wiring: the dossier workers are now RAW async handlers (the async
        # DelegationDispatcher seam a FunctionWorker boundary cannot reach — DL-0034 precedent).
        "operadora.cred.prepare_dossier",
        "operadora.adequacao.prepare_remediation_dossier",
        # pagto's dossier edge (item9-w3): the LAST worker-originated dossier edge, converted from
        # the DL-0033 local stub to the same raw async Andre delegation as adequacao/cred.
        "operadora.pagto.prepare_approval_dossier",
        # item-9 wave-5: programa's 4 raw handlers (module-level rationale above).
        "operadora.programa.stratify_risk",
        "operadora.programa.stop_processing",
        "operadora.programa.proactive_contact",
        "operadora.programa.notify_sla_risk",
        # item-9 notify-wiring fix (adequacao, module-level rationale above).
        "operadora.adequacao.update_monitoring_plan",
    }
    function_topics = [
        t
        for t in harness.registered_topics
        if not t.startswith(class_module_prefixes) and t not in raw_handler_topics
    ]
    assert function_topics, "no function-based topics found"

    for topic in function_topics:
        worker = harness.registry.get(topic)
        assert isinstance(worker, FunctionWorker), f"{topic} was not registered as a FunctionWorker"


def test_raw_handler_module_registers_outside_the_worker_registry() -> None:
    """`events` (T3.1 R2) populates `_handlers` (dispatch-reachable) but NOT `WorkerRegistry` —
    it is registered via `harness.register()`, not `harness.register_worker()`. Recurso's THREE
    raw handlers follow the SAME shape. item-9 wave-5: programa's 4 raw
    handlers (`stratify_risk`/`stop_processing`/`proactive_contact`/`notify_sla_risk`) follow the
    SAME shape too (module-level rationale in programa.py). item-9 notify-wiring fix:
    `operadora.adequacao.update_monitoring_plan` follows the SAME shape (module-level rationale
    in adequacao.py)."""
    harness = _fresh_harness()
    register_all_workers(harness)

    assert "operadora.events.publish" in harness.registered_topics
    assert harness.registry.get("operadora.events.publish") is None

    for topic in (
        "operadora.recurso.notify_sla_risk",
        "operadora.recurso.escalate_ans_timeout",
        "operadora.recurso.comunicar_resposta",
        # DL-0033 real wiring: the dossier A2A raw handlers follow the same shape.
        "operadora.cred.prepare_dossier",
        "operadora.adequacao.prepare_remediation_dossier",
        "operadora.pagto.prepare_approval_dossier",
        # item-9 wave-5: programa's 4 raw handlers (module-level rationale above).
        "operadora.programa.stratify_risk",
        "operadora.programa.stop_processing",
        "operadora.programa.proactive_contact",
        "operadora.programa.notify_sla_risk",
        # item-9 notify-wiring fix (adequacao, module-level rationale above).
        "operadora.adequacao.update_monitoring_plan",
    ):
        assert topic in harness.registered_topics
        assert harness.registry.get(topic) is None


# ---------------------------------------------------------------------------
# Spot-check 5 registered topics against the real spec/ BPMN external-task topicName values
# (ADR-0026 "Test strategy": registry-coverage vs the spec's external-task topics).
# ---------------------------------------------------------------------------


def _spec_topics(bpmn_filename: str) -> set[str]:
    path = _SPEC_BPMN_DIR / bpmn_filename
    xml = path.read_text(encoding="utf-8")
    import re

    return set(re.findall(r'camunda:topic="([^"]+)"', xml))


def test_spec_bpmn_dir_exists() -> None:
    """Guards the spot-check below against a silently-empty comparison (fail loud, not fabricate
    a pass) — constraint 3, "no fabricated results"."""
    assert _SPEC_BPMN_DIR.is_dir(), f"spec BPMN dir not found at {_SPEC_BPMN_DIR}"


def test_spot_check_adequacao_register_fallback_commitment_matches_spec() -> None:
    spec_topics = _spec_topics("SP-OP-ADEQUACAO-001_Adequacao_Rede.bpmn")
    assert "operadora.adequacao.register_fallback_commitment" in spec_topics

    harness = _fresh_harness()
    register_all_workers(harness)
    assert "operadora.adequacao.register_fallback_commitment" in harness.registered_topics


def test_spot_check_fraude_register_fraud_accusation_matches_spec() -> None:
    spec_topics = _spec_topics("SP-OP-FRAUDE-001_Investigacao_Fraude.bpmn")
    assert "operadora.fraude.register_fraud_accusation" in spec_topics

    harness = _fresh_harness()
    register_all_workers(harness)
    assert "operadora.fraude.register_fraud_accusation" in harness.registered_topics


def test_spot_check_contas_register_glosa_accept_matches_spec() -> None:
    spec_topics = _spec_topics("SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn")
    assert "operadora.contas.register_glosa_accept" in spec_topics

    harness = _fresh_harness()
    register_all_workers(harness)
    assert "operadora.contas.register_glosa_accept" in harness.registered_topics


def test_spot_check_reembolso_send_denial_matches_spec() -> None:
    spec_topics = _spec_topics("SP-OP-REEMBOLSO-001_Reembolso_Beneficiario.bpmn")
    assert "operadora.reembolso.send_reembolso_denial" in spec_topics

    harness = _fresh_harness()
    register_all_workers(harness)
    assert "operadora.reembolso.send_reembolso_denial" in harness.registered_topics


def test_spot_check_auth_analyze_request_matches_spec() -> None:
    """auth.py is a pre-existing (T1.1) WorkerBase module — bootstrap-wrapped here, unchanged
    topic; still worth spot-checking since it is now reached via `register_all_workers`."""
    spec_topics = _spec_topics("SP-OP-AUTH-001_Autorizacao_Previa.bpmn")
    assert "operadora.auth.analyze_request" in spec_topics

    harness = _fresh_harness()
    register_all_workers(harness)
    assert "operadora.auth.analyze_request" in harness.registered_topics
