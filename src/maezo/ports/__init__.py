"""ERP-neutral application ports — the payer core's isolation boundary (ADR-0037, MZO-030).

**What this package is.** Five `typing.Protocol` seams plus the value types and the single
failure taxonomy they share. Together they are the ONLY vocabulary the payer core needs in order
to receive work, gate on consent, read clinical context, read population features and publish
outcomes — expressed purely as payer needs and opaque references.

    maezo.ports.work_items          WorkItemSource          intake of canonical work items
    maezo.ports.consent             ConsentDecisionSource   consent intake + point-read gate
    maezo.ports.clinical_context    ClinicalContextPort     XRD-06, read-only clinical context
    maezo.ports.population_features PopulationFeaturePort   XRD-07, k-suppressed population reads
    maezo.ports.outcomes            OutcomePublisherPort    honest outcome egress
    maezo.ports.envelope            the 28 frozen envelope fields, in the pinned order
    maezo.ports.errors              ONE closed refusal taxonomy + the pinned timeout default

**Leaf-domain, by fence.** Nothing here imports a broker, a schema registry, an HTTP/SQL client, a
cloud SDK, a clinical-record library — nor any other `maezo` package. `maezo.ports` depends on the
standard library and `typing` and nothing else, so the payer core it serves stays portable off the
infrastructure that happens to sit behind these seams today.
`tests/unit/ports/test_ports_purity.py` enforces this with an AST fence over every module in the
package (the same technique as `tests/unit/tools/workers/test_worker_handler_purity.py`).

**Invariants the whole package holds (each structurally tested).**

1. *ERP-neutral.* No Kafka/Avro/Glue/AWS/HTTP/FHIR/Athena/vendor type or identifier on any port
   surface. Topic names, codecs, endpoints and credentials belong to the adapters (MZO-050+,
   still gated), never here.
2. *Purpose-bound always; consent-anchored where a subject exists.* EVERY protected read declares
   `purpose_of_use`, REQUIRED and keyword-only, so mypy strict rejects any call site that omits it.
   The per-subject clinical reads ALSO name the `consent_decision_ref` that authorised them. The
   population reads do not, and must not: a decision reference names a decision taken for one
   subject, and a population read has no subject — requiring one would be an invariant no adapter
   could honour and an authority no audit record could verify. Consent is enforced on those reads
   by the provider's own filter, whose application and snapshot instant come back in the response
   (`ConsentFilter`). Both halves of this are structurally tested, in both directions.
3. *Success or refusal, never a raise.* Policy-shaped failures come back as a closed
   `PortFailureReason` on a `PortResult`. No exception type is defined in this package at all.
4. *Deterministic deadlines.* Every request/response call takes an explicit `timeout_seconds`
   bounded by the pinned `DEFAULT_PORT_TIMEOUT_SECONDS`; a breach is the `TIMEOUT` reason code,
   not an escaping `asyncio.TimeoutError`. No port retries internally — retry is caller policy.
5. *Opaque references, no PHI.* Every reference is a `str` this package never parses, prefixes or
   validates, and no value type carries a patient-identifying field name. Identity semantics are
   MZO-020's DPO/Legal-gated scope, which DL-0040 records as still OPEN.

**Not in this package (deliberately).** No adapter, no consumer, no mapping, no client, no
`maezo.domain`. Only the seams.
"""

from maezo.ports.clinical_context import ClinicalContextPort, CodedSummary, SummaryPage
from maezo.ports.consent import (
    CanonicalConsentEvent,
    ConsentDecision,
    ConsentDecisionSource,
    ConsentDelivery,
)
from maezo.ports.envelope import (
    ENVELOPE_FIELD_COUNT,
    ENVELOPE_FIELD_ORDER,
    CanonicalEnvelope,
    SourcePosition,
)
from maezo.ports.errors import (
    DEFAULT_PORT_TIMEOUT_SECONDS,
    PortFailure,
    PortFailureReason,
    PortResult,
)
from maezo.ports.outcomes import CanonicalOutcome, OutcomeAck, OutcomePublisherPort
from maezo.ports.population_features import (
    AggregateCell,
    AggregateResult,
    ConsentFilter,
    FeatureSetDefinition,
    FeatureSetDescriptor,
    KAnonymityPolicy,
    PopulationFeaturePort,
)
from maezo.ports.work_items import CanonicalWorkItem, WorkItemDelivery, WorkItemSource

__all__ = [
    "DEFAULT_PORT_TIMEOUT_SECONDS",
    "ENVELOPE_FIELD_COUNT",
    "ENVELOPE_FIELD_ORDER",
    "AggregateCell",
    "AggregateResult",
    "CanonicalConsentEvent",
    "CanonicalEnvelope",
    "CanonicalOutcome",
    "CanonicalWorkItem",
    "ClinicalContextPort",
    "CodedSummary",
    "ConsentDecision",
    "ConsentDecisionSource",
    "ConsentDelivery",
    "ConsentFilter",
    "FeatureSetDefinition",
    "FeatureSetDescriptor",
    "KAnonymityPolicy",
    "OutcomeAck",
    "OutcomePublisherPort",
    "PopulationFeaturePort",
    "PortFailure",
    "PortFailureReason",
    "PortResult",
    "SourcePosition",
    "SummaryPage",
    "WorkItemDelivery",
    "WorkItemSource",
]
