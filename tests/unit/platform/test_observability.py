"""Unit tests for maezo.platform.observability (ADR-0010, ADR-0014).

TDD London School — tests written before implementation.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# setup_observability tests
# ---------------------------------------------------------------------------


def test_setup_observability_initializes_without_error() -> None:
    """setup_observability() should run without raising exceptions in dev mode."""
    from maezo.platform.observability import setup_observability

    # Should not raise — dev mode with OTLP exporter disabled
    setup_observability(service_name="test-service", otlp_endpoint=None)


def test_setup_observability_returns_tracer_provider() -> None:
    """setup_observability() should return a configured TracerProvider."""
    from opentelemetry.sdk.trace import TracerProvider

    from maezo.platform.observability import setup_observability

    provider = setup_observability(service_name="test-service", otlp_endpoint=None)
    assert isinstance(provider, TracerProvider)


def test_setup_observability_configures_service_name() -> None:
    """setup_observability() should set the service.name resource attribute."""
    from maezo.platform.observability import setup_observability

    provider = setup_observability(service_name="maezo-test", otlp_endpoint=None)
    resource = provider.resource
    attributes = resource.attributes

    assert attributes["service.name"] == "maezo-test"  # type: ignore[index]


# ---------------------------------------------------------------------------
# Worker metrics tests
# ---------------------------------------------------------------------------


def test_worker_metrics_registered_in_collector() -> None:
    """MetricsCollector should expose worker_execution_time and worker_error_count."""
    from maezo.runtime.metrics import MetricsCollector

    collector = MetricsCollector()
    metric_names = {m.name for m in collector.registry.collect()}

    # Base metrics from ADR-0010
    assert "maezo_agent_latency_seconds" in metric_names

    # Worker-specific metrics (M11)
    assert "maezo_worker_execution_time_seconds" in metric_names
    assert "maezo_worker_error_count" in metric_names


def test_worker_execution_time_histogram() -> None:
    """worker_execution_time_seconds histogram should accept observations."""
    from maezo.runtime.metrics import MetricsCollector

    collector = MetricsCollector()

    collector.worker_execution_time.labels(  # type: ignore[attr-defined]
        worker="test_worker", topic="operadora.test"
    ).observe(0.05)
    collector.worker_execution_time.labels(  # type: ignore[attr-defined]
        worker="test_worker", topic="operadora.test"
    ).observe(0.15)

    samples = []
    for metric in collector.registry.collect():
        for sample in metric.samples:
            if "worker_execution_time" in sample.name:
                samples.append((sample.name, sample.labels, sample.value))

    assert len(samples) > 0
    # Verify we have observations
    sum_vals = [v for n, lbl, v in samples if n.endswith("_sum")]
    count_vals = [v for n, lbl, v in samples if n.endswith("_count")]
    assert sum(sum_vals) == pytest.approx(0.20, rel=0.01)
    assert sum(count_vals) == 2.0


def test_worker_error_count_counter() -> None:
    """worker_error_count counter should increment correctly."""
    from maezo.runtime.metrics import MetricsCollector

    collector = MetricsCollector()

    collector.worker_error_count.labels(  # type: ignore[attr-defined]
        worker="failing_worker", topic="operadora.fail", error_type="RuntimeError"
    ).inc()
    collector.worker_error_count.labels(  # type: ignore[attr-defined]
        worker="failing_worker", topic="operadora.fail", error_type="RuntimeError"
    ).inc()

    samples = []
    for metric in collector.registry.collect():
        for sample in metric.samples:
            if "worker_error_count" in sample.name:
                samples.append((sample.name, sample.labels, sample.value))

    total = sum(v for n, lbl, v in samples if n.endswith("_total") and lbl.get("worker") == "failing_worker")
    assert total == 2.0


# ---------------------------------------------------------------------------
# WorkerBase metrics emission tests
# ---------------------------------------------------------------------------


def test_worker_base_emits_execution_time_metric() -> None:
    """WorkerBase.run() should emit worker_execution_time_seconds metric."""
    from maezo.runtime.metrics import MetricsCollector
    from maezo.tools.workers.base import WorkerBase

    collector = MetricsCollector()

    class MetricEmittingWorker(WorkerBase):
        def execute(self, process_vars: dict) -> dict:
            return {"status": "ok"}

    worker = MetricEmittingWorker(topic="operadora.test.metrics")

    # Patch to use our collector
    with patch(
        "maezo.platform.observability._get_metrics_collector",
        return_value=collector,
    ):
        result = worker.run({"tenant_id": "amh"})

    assert result == {"status": "ok"}

    # Check that execution time was recorded
    metric_names = {m.name for m in collector.registry.collect()}
    assert "maezo_worker_execution_time_seconds" in metric_names


def test_worker_base_emits_error_count_on_failure() -> None:
    """WorkerBase.run() should emit worker_error_count on failure."""
    from maezo.runtime.metrics import MetricsCollector
    from maezo.tools.workers.base import WorkerBase

    collector = MetricsCollector()

    class AlwaysFailingWorker(WorkerBase):
        def execute(self, process_vars: dict) -> dict:
            raise ValueError("simulated worker failure")

    worker = AlwaysFailingWorker(topic="operadora.test.error", max_retries=1)

    with (
        patch(
            "maezo.platform.observability._get_metrics_collector",
            return_value=collector,
        ),
        pytest.raises(ValueError, match="simulated worker failure"),
    ):
        worker.run({"tenant_id": "amh"})

    # Check error count was recorded
    samples = []
    for metric in collector.registry.collect():
        for sample in metric.samples:
            if "worker_error_count" in sample.name:
                samples.append(sample)

    total = sum(
        s.value for s in samples if s.name.endswith("_total") and s.labels.get("error_type") == "ValueError"
    )
    assert total >= 1.0


# ---------------------------------------------------------------------------
# Agent-turn telemetry (G3 — the #222 PHI-fence bar for a completed agent turn)
# ---------------------------------------------------------------------------

from maezo.gateway.pseudonymizer import PHI_FIELDS  # noqa: E402
from maezo.platform.observability import (  # noqa: E402
    TURN_CORRELATION_PREFIX,
    TURN_TELEMETRY_FIELDS,
    record_agent_turn,
    turn_conversation_digest,
)


def _capture_record(**kwargs: object) -> dict[str, object]:
    """Call `record_agent_turn(**kwargs)` with the module logger patched; return the emitted fields.

    Patches the module logger rather than using `structlog.testing.capture_logs`: `setup_observability`
    (exercised by tests above in this file) binds observability's module logger under
    `cache_logger_on_first_use=True`, after which `capture_logs` cannot intercept it. Patching the
    logger is isolation-proof and inspects the emitted kwargs directly."""
    with patch("maezo.platform.observability.logger") as mock_logger:
        record_agent_turn(**kwargs)  # type: ignore[arg-type]
    mock_logger.info.assert_called_once()
    call = mock_logger.info.call_args
    assert call.args[0] == "agent_turn_completed"
    return dict(call.kwargs)


def test_turn_telemetry_field_contract_is_closed_and_disjoint_from_phi() -> None:
    """The pinned-field contract, by set-equality. The record carries exactly these five fields —
    four COUNTS/label and one HASH — and NONE of them is a PHI field. Hardcoded (not derived from
    the constant it pins) with provenance: this is the closed set `record_agent_turn` emits."""
    expected = {
        "agent_id",  # an agent NAME, not PHI
        "input_message_count",
        "output_message_count",
        "produced_message_count",
        "conversation_digest",  # a HASH of the thread id, never the raw id
    }
    assert expected == TURN_TELEMETRY_FIELDS
    # The load-bearing invariant: no pinned field is a tenant-PHI field. PHI_FIELDS is the
    # canonical set (gateway/pseudonymizer.py); reusing it here is what makes this a fence, not a
    # comment. A field named `cpf`/`nome`/`telefone`/`email` could never enter the record.
    assert TURN_TELEMETRY_FIELDS.isdisjoint(PHI_FIELDS)


def test_turn_telemetry_hashes_the_conversation_ref_and_never_emits_it_raw() -> None:
    """THE safety property. Given a RAW PHI-shaped conversation ref (a phone-bearing WhatsApp
    conversation id), the emitted record must contain the stable HASH and the raw id must appear
    NOWHERE in it. This is the RED control's target: neuter `turn_conversation_digest` to the
    identity function and the raw phone lands in `conversation_digest`, failing the absence
    assertions below."""
    raw_ref = "wa:amh:+5511998887766"  # a raw, phone-bearing conversation id — must never leak

    record = _capture_record(
        agent_id="helena",
        input_message_count=1,
        output_message_count=2,
        conversation_ref=raw_ref,
    )

    # The emitted key set is exactly the pinned contract.
    assert set(record) == TURN_TELEMETRY_FIELDS
    # The digest is present, is the stable hash, and is MARKED as a correlation token.
    assert record["conversation_digest"] == turn_conversation_digest(raw_ref)
    assert str(record["conversation_digest"]).startswith(TURN_CORRELATION_PREFIX)
    # The raw id — and the raw phone inside it — appear in NO field of the emitted record.
    for key, value in record.items():
        assert value != raw_ref, key
        assert not (isinstance(value, str) and "5511998887766" in value), key


def test_turn_telemetry_counts_are_content_free() -> None:
    """The counts describe the turn's SHAPE, never its content. `produced` is output minus input,
    floored at zero, and the emitted key set is exactly the pinned contract."""
    record = _capture_record(
        agent_id="rafael",
        input_message_count=3,
        output_message_count=5,
        conversation_ref="hk1_" + "a" * 64,  # an already-keyed PHI-safe thread id
    )
    assert record["input_message_count"] == 3
    assert record["output_message_count"] == 5
    assert record["produced_message_count"] == 2  # 5 - 3, the turn's output size
    assert set(record) == TURN_TELEMETRY_FIELDS


def test_turn_telemetry_digest_is_stable_deterministic_and_one_way() -> None:
    """The correlation token is deterministic (two turns of one conversation correlate), marked,
    and does NOT contain the input — a one-way hash, not an encoding."""
    ref = "ESC-amh-inad-2026-000123"
    first = turn_conversation_digest(ref)
    assert first == turn_conversation_digest(ref)  # deterministic
    assert first.startswith(TURN_CORRELATION_PREFIX)
    assert ref not in first  # one-way: the business key is not recoverable by reading the token


def test_turn_telemetry_none_ref_emits_a_null_digest_not_a_hash_of_none() -> None:
    """A turn with no thread id (no checkpointer wired) emits `conversation_digest=None`, never a
    hash of the literal string 'None' — the absence is explicit, not a spurious token."""
    record = _capture_record(
        agent_id=None,
        input_message_count=1,
        output_message_count=1,
        conversation_ref=None,
    )
    assert record["conversation_digest"] is None
    assert record["produced_message_count"] == 0  # 1 - 1, floored


def test_turn_telemetry_never_raises_into_the_turn() -> None:
    """Best-effort by construction: a telemetry defect must never break a turn that already
    completed. A count that is not an int cannot compute `produced`; the guard swallows it and the
    call returns normally rather than propagating."""
    # A non-int output count makes `output - input` raise inside the body; the call must still
    # return None without re-raising (the turn is already done).
    record_agent_turn(
        agent_id="helena",
        input_message_count=1,
        output_message_count="not-an-int",  # type: ignore[arg-type]
        conversation_ref=None,
    )
