"""Worker: `vendor.membership.publication` — cadência do publication job vendor (VW1-P0).

Runs `gateway/human/vendor_membership_publication_job.py#VendorMembershipPublicationJob` once per
fetched task: every vendor channel whose membership record is not committed in this exact state
gets its access projection published to the access plane. The job itself owns every business
invariant (fail-closed sources, durable 0600 ledger, "ausente = unknown ≠ zero"); this module is
ONLY the harness cadence seam for the deployment that adopts the vendor plane.

Landed under grant OWNER-FENCE-DISPATCH-001 (docs/audits/VENDOR-XP-2026-10/COORDINATION.md state
log): PW1-A's bootstrap fence is the one crossed here — the job shipped as "never a
bootstrap-registered worker" (its own module docstring, PW1-B/PW1-C fences stay untouched) and
this wave registers the cadence WITHOUT changing a single line of the job: the sources, the
ledger and the canonical payload are exactly the job's own.

WHY A RAW `harness.register()` HANDLER, NOT a `FunctionWorker` (mirrors `events.py`'s rationale,
with a different load-bearing reason): the job's inputs are INJECTED SOURCE SEAMS (channel
reader, membership reader, access-plane publisher, durable ledger), not process variables, and
its `run()` is async against those seams. The dict-first `FunctionWorker.execute(variables)`
boundary is synchronous and sees only `task.variables` — there are no process variables to see.
The registration therefore declares the EMPTY fetch scope (`variables=()` — deliberately
distinct from legacy `None`, which the engine reads as "return ALL variables"): this worker
needs nothing from the instance, so it fetches nothing.

kafka accepted-but-unused (`del kafka`, the `ans_cron.py` precedent): the composition contract
every bootstrap shares is `register_<domain>_workers(harness, kafka=None, **seams)` and
`register_all_workers` calls it POSITIONALLY — but this worker's only egress is the access-plane
`VendorAccessPublisher` seam, never the broker. No `vendor.*` fact is published to Kafka in this
wave; a future bridge topic would be a separate, evidenced decision.

Fail-closed wiring guard (the load-bearing one — "fonte ausente ⇒ recusa tipada, nunca fabrica
publicação"): the job is constructed ONLY when ALL FOUR seams are present. Unwired OR partially
wired, the handler raises `read_credentials.unavailable()` — the same typed
`ReadRefusalError("read_dependency_unavailable")` refusal the job itself raises for an absent or
empty store — BEFORE touching any source, so a half-wired worker can never publish with a
defaulted echo publisher or dedupe against a default ledger. When fully wired, every absent
source stays the JOB's own honest refusal (a `None` store is UNKNOWN and refuses; a channel
whose record cannot be read refuses; outcome-unknown publish refuses) — the harness ladder
reports it as a typed failure/retry, never as a completed task. This worker never fabricates a
publication: with no sources there is nothing to count, and the refusal is the count.

No BPMN source, by design: no `spec/processes/bpmn/**` external task declares
`vendor.membership.publication` and none may be added in this wave (grant scope: no new
BPMN/DMN/route). The topic exists so the adopting deployment gets the standard harness cadence —
the same posture `register_all_workers` already tolerates for documented spec gaps. Topic naming
keeps the two-level domain prefix convention (`operadora.*`/`regulatorio.*`/`agents.*`) with the
vendor plane's own `vendor.` root: this is not an SP-OP-* operadora process, it is the vendor
identity plane's administrative cadence (VW1).

Idempotent like every bootstrap: `WorkerHarness.register` replaces on re-registration, same
topic — safe to call more than once against the same harness.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from maezo.gateway.human.read_credentials import unavailable
from maezo.gateway.human.vendor_membership_publication_job import VendorMembershipPublicationJob

if TYPE_CHECKING:
    from maezo.tools.workers.harness import ExternalTask, KafkaPublisher, TaskHandler, WorkerHarness

#: The vendor plane's own cadence topic (module docstring: naming rationale, no BPMN source).
VENDOR_PUBLICATION_TOPIC = "vendor.membership.publication"


def make_vendor_publication_handler(job: VendorMembershipPublicationJob | None) -> TaskHandler:
    """Create the `vendor.membership.publication` handler.

    `job=None` (unwired or partially wired seams) refuses typed BEFORE any source is touched —
    the wiring guard IS the fail-closed default. A fully wired job keeps its own refusal
    semantics; the handler adds only the honest output variables:

      vendor_membership_published: int  — records the job actually published this tick.
      vendor_membership_unchanged: int  — records already committed in this exact state.

    No BPMN gateway routes on either variable (no BPMN declares this topic at all — module
    docstring); they are the cadence tick's record, never a routing decision. The task argument
    is deliberately unread: the job's inputs are injected seams, never process variables.
    """

    async def handler(task: ExternalTask) -> Mapping[str, Any]:
        if job is None:
            # Unwired/partially wired deployment: the sources are UNKNOWN, never zero. Same typed
            # refusal the job raises for an absent store; the harness ladder owns retry/incident.
            raise unavailable()
        result = await job.run()
        return {
            "vendor_membership_published": result.published,
            "vendor_membership_unchanged": result.unchanged,
        }

    return handler


def _vendor_publication_job(seams: Mapping[str, Any]) -> VendorMembershipPublicationJob | None:
    """Build the job from its four seams, or `None` when the wiring is not complete."""
    channels = seams.get("vendor_channels")
    memberships = seams.get("vendor_memberships")
    publisher = seams.get("vendor_publisher")
    ledger = seams.get("vendor_ledger")
    if channels is None or memberships is None or publisher is None or ledger is None:
        return None
    return VendorMembershipPublicationJob(
        channels=channels,
        memberships=memberships,
        publisher=publisher,
        ledger=ledger,
    )


def register_vendor_workers(
    harness: WorkerHarness,
    kafka: KafkaPublisher | None = None,
    **seams: Any,
) -> None:
    """Register the vendor membership publication cadence worker on `harness`.

    Raw-handler registration (`harness.register`, not `harness.register_worker` — module
    docstring for why the dict-first `FunctionWorker` boundary does not apply) under the EMPTY
    fetch scope (`variables=()`): the worker reads no process variables, so it fetches none.

    Seams (all-or-nothing, module docstring): `vendor_channels` (`VendorChannelReader`),
    `vendor_memberships` (`VendorMembershipReader`), `vendor_publisher` (`VendorAccessPublisher`),
    `vendor_ledger` (`VendorPublicationLedger`). Any missing seam ⇒ the handler refuses typed on
    every tick — registration still succeeds so the topic is visible and the refusal is loud.
    """
    del kafka  # unused — egress is the access-plane publisher seam, never the broker (docstring)
    harness.register(
        VENDOR_PUBLICATION_TOPIC,
        make_vendor_publication_handler(_vendor_publication_job(seams)),
        variables=(),
    )
