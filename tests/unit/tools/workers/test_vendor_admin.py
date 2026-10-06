"""Unit tests for `maezo.tools.workers.vendor_admin` — `vendor.membership.publication`.

No engine, no live Kafka, no live PostgreSQL — the same posture as the job's own suite
(`tests/unit/gateway/human/test_vendor_membership_publication_job.py`, whose fakes these import):
every test drives `register_vendor_workers`/`make_vendor_publication_handler` directly against
`ExternalTask` + injected fakes. The vendor publication job's BUSINESS invariants are pinned
there; this file pins the CADENCE-WORKER contract landed under grant OWNER-FENCE-DISPATCH-001:

  - registration is the raw `harness.register()` path (topic dispatch-reachable, NOT in the
    `WorkerRegistry`) under the EMPTY fetch scope — the worker reads no process variables;
  - registration is idempotent, through `register_vendor_workers` AND the full composition;
  - the fail-closed wiring guard: unwired OR partially wired ⇒ typed `unavailable()` refusal on
    every tick, zero publication — the worker never fabricates;
  - a fully wired job runs END-TO-END through the worker path (handler → job → access plane),
    with the honest per-tick counts and the ledger's second-tick idempotency.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from maezo.gateway.human.queue import ReadRefusalError
from maezo.gateway.human.read_credentials import unavailable
from maezo.gateway.human.read_profile import parse_model
from maezo.gateway.human.vendor_membership_publication_job import (
    VendorMembershipAccessPayload,
    VendorPublicationLedger,
    access_payload,
)
from maezo.portal.contracts.models import SubjectBinding
from maezo.tools.workers.bootstrap import ALL_WORKER_BOOTSTRAPS, register_all_workers
from maezo.tools.workers.harness import ExternalTask, FakeWorkerTransport, WorkerHarness
from maezo.tools.workers.vendor_admin import (
    VENDOR_PUBLICATION_TOPIC,
    register_vendor_workers,
)
from tests.unit.gateway.human.test_vendor_membership_publication_job import (
    AccessPlane,
    UnitChannels,
    channel_row,
    record,
)

# `asyncio_mode = "auto"` (pyproject.toml) collects async def tests automatically.


def _task(
    *,
    task_id: str = "vendor-task-1",
    topic: str = VENDOR_PUBLICATION_TOPIC,
    business_key: str = "VW-channel-a",
) -> ExternalTask:
    return ExternalTask(
        task_id=task_id,
        topic=topic,
        process_instance_id="proc-1",
        business_key=business_key,
        worker_id="w-1",
        variables={},
        # Generic fixtures model top-level fetchAndLock provenance (R228-D01).
        process_definition_key="VW-VENDOR-PLANE",
        activity_id="ST_VendorMembershipPublication",
    )


class _Reader:
    """The current membership bound to every listed channel (the job test's Reader shape)."""

    def __init__(self, current: Any) -> None:
        self.current = current

    async def membership(self, tenant: str, channel_ref: str) -> Any:
        return self.current


def _register(
    harness: WorkerHarness,
    tmp_path: Any,
    *,
    plane: AccessPlane,
    rows: tuple[Any, ...] | None = (channel_row(),),
    current: Any | None = None,
) -> None:
    """Fully wire the vendor worker on `harness`: real ledger on tmp_path, fake sources."""
    register_vendor_workers(
        harness,
        vendor_channels=UnitChannels(rows),
        vendor_memberships=_Reader(record() if current is None else current),
        vendor_publisher=plane,
        vendor_ledger=VendorPublicationLedger(tmp_path / "ledger.json", "test-tenant"),
    )


def _handler(harness: WorkerHarness) -> Any:
    """The handler the harness will actually dispatch for the vendor topic (dispatch truth)."""
    return harness._handlers[VENDOR_PUBLICATION_TOPIC]


# ---------------------------------------------------------------------------
# Registration shape (raw handler, empty fetch scope, correct topic)
# ---------------------------------------------------------------------------


def test_registers_the_composition_topic_and_topic_only() -> None:
    """The vendor worker registers EXACTLY its one cadence topic, on the raw `harness.register()`
    path (dispatch-reachable via `_handlers`, but NOT a `WorkerRegistry` member — mirrors
    `operadora.events.publish`)."""
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="vendor-probe")
    register_vendor_workers(harness)

    assert VENDOR_PUBLICATION_TOPIC == "vendor.membership.publication"
    assert harness.registered_topics == [VENDOR_PUBLICATION_TOPIC]
    assert harness.registry.get(VENDOR_PUBLICATION_TOPIC) is None


def test_composition_includes_the_vendor_topic() -> None:
    """`register_all_workers` (the daemon's ONE bootstrap call) serves the vendor cadence — the
    18th member of `ALL_WORKER_BOOTSTRAPS` reaches a live harness through the composition."""
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="composition-probe")
    register_all_workers(harness)

    assert VENDOR_PUBLICATION_TOPIC in harness.registered_topics
    assert register_vendor_workers in ALL_WORKER_BOOTSTRAPS


def test_registration_is_idempotent() -> None:
    """Re-registering never grows the topic set (last registration wins, same as every bootstrap)
    — through the module bootstrap AND through the full composition."""
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="vendor-probe")
    register_vendor_workers(harness)
    first = harness.registered_topics

    register_vendor_workers(harness)
    assert harness.registered_topics == first

    register_all_workers(harness)
    register_all_workers(harness)
    assert harness.registered_topics.count(VENDOR_PUBLICATION_TOPIC) == 1


def test_fetch_scope_is_explicitly_empty_never_legacy_all() -> None:
    """The registration declares the EMPTY variable scope (`variables=()`), which the transport
    sends as `"variables": []` — NOT legacy `None`, which the engine reads as "return ALL
    variables". The cadence worker reads no process variables, so it fetches none."""
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="vendor-probe")
    register_vendor_workers(harness)

    subscriptions = {subscription.topic_name: subscription for subscription in harness._topic_subscriptions()}
    assert subscriptions[VENDOR_PUBLICATION_TOPIC].variables == ()


# ---------------------------------------------------------------------------
# Fail-closed wiring guard (fonte ausente ⇒ recusa tipada, nunca fabrica publicação)
# ---------------------------------------------------------------------------


async def test_unwired_worker_refuses_typed_and_never_publishes() -> None:
    """The registered worker with NO seams refuses typed on every tick: the same
    `unavailable()` refusal the job raises for an absent store, raised BEFORE any source is
    touched — zero publication is structural (there is no publisher to publish with)."""
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="vendor-probe")
    register_vendor_workers(harness)

    with pytest.raises(ReadRefusalError) as refusal:
        await _handler(harness)(_task())
    assert str(refusal.value) == str(unavailable())


async def test_partially_wired_worker_refuses_typed(tmp_path: Any) -> None:
    """ALL FOUR seams or none: a channels reader without a publisher (the dangerous half-wiring —
    a defaulted echo publisher would fabricate successes) refuses typed, and the wired reader is
    never even consulted."""
    consulted: list[str] = []

    class ProbeChannels:
        async def channels(self, tenant: str) -> tuple[Any, ...] | None:
            consulted.append(tenant)
            return (channel_row(),)

    harness = WorkerHarness(FakeWorkerTransport(), worker_id="vendor-probe")
    register_vendor_workers(
        harness,
        vendor_channels=ProbeChannels(),
        vendor_ledger=VendorPublicationLedger(tmp_path / "ledger.json", "test-tenant"),
    )

    with pytest.raises(ReadRefusalError):
        await _handler(harness)(_task())
    assert consulted == []  # the guard fires BEFORE any source is touched


@pytest.mark.parametrize("rows", [None, ()])
async def test_absent_store_through_the_worker_path_refuses_and_never_publishes(
    rows: Any, tmp_path: Any
) -> None:
    """Fully wired but the channel store is ABSENT (`None`) or EMPTY: the JOB's own fail-closed
    refusal surfaces through the worker path, with zero bytes reaching the access plane —
    "ausente = unknown ≠ zero", unchanged by the cadence wiring."""
    plane = AccessPlane()
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="vendor-probe")
    _register(harness, tmp_path, plane=plane, rows=rows)

    with pytest.raises(ReadRefusalError):
        await _handler(harness)(_task())
    assert plane.payloads == []


# ---------------------------------------------------------------------------
# The job runs through the worker path (unit, no live Kafka/Postgres)
# ---------------------------------------------------------------------------


async def test_publication_runs_through_the_worker_path_with_honest_counts(tmp_path: Any) -> None:
    """End-to-end through the HANDLER (the harness's own dispatch entry): the first tick
    publishes the canonical vendor projection and reports it honestly; a SECOND harness wired to
    the SAME ledger file dedupes against it and reports `unchanged` — the cadence is a no-op
    when nothing changed, exactly the job's own durable-ledger contract."""
    plane = AccessPlane()
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="vendor-probe")
    _register(harness, tmp_path, plane=plane)

    first = await _handler(harness)(_task())
    assert first == {"vendor_membership_published": 1, "vendor_membership_unchanged": 0}
    assert len(plane.payloads) == 1

    # A fresh worker-process shape over the SAME durable ledger: nothing republishes.
    replay_plane = AccessPlane()
    replay = WorkerHarness(FakeWorkerTransport(), worker_id="vendor-probe-2")
    _register(replay, tmp_path, plane=replay_plane)
    second = await _handler(replay)(_task(task_id="vendor-task-2"))
    assert second == {"vendor_membership_published": 0, "vendor_membership_unchanged": 1}
    assert replay_plane.payloads == []


async def test_published_payload_is_the_canonical_vendor_projection(tmp_path: Any) -> None:
    """The payload that crossed the worker path IS the job's canonical, content-addressed access
    projection (`audience == "vendor"` — the admission the comms recipient literal carries since
    OWNER-FENCE-DISPATCH-001)."""
    plane = AccessPlane()
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="vendor-probe")
    _register(harness, tmp_path, plane=plane)

    result = await _handler(harness)(_task())
    assert result == {"vendor_membership_published": 1, "vendor_membership_unchanged": 0}
    assert len(plane.payloads) == 1
    assert plane.payloads[0] == access_payload(record())
    projection = json.loads(plane.payloads[0])
    assert projection["audience"] == "vendor"
    parsed = parse_model(VendorMembershipAccessPayload, projection)
    assert parsed.audience == "vendor" and parsed.state == "active"


async def test_stray_audience_refuses_through_the_worker_path(tmp_path: Any) -> None:
    """A non-vendor record on a channel row is UNKNOWN to this plane (the job refuses it) — the
    cadence wiring does not loosen the job's own admission gate."""
    plane = AccessPlane()
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="vendor-probe")
    stray = record(audience="provider", subject_bindings=(SubjectBinding(kind="provider", resource_ref="p"),))
    _register(harness, tmp_path, plane=plane, current=stray)

    with pytest.raises(ReadRefusalError):
        await _handler(harness)(_task())
    assert plane.payloads == []
