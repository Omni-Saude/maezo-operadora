"""Protocol-preserving GATED seam decorators — the effect chokepoint's per-call layer (Onda 1).

ONE decorator per injected `Protocol`, not one per call site. Design R-7 is the reason: the
agent graphs already funnel every effect through a small set of injected seams (`self._dmn`,
`self._cibseven`, `self._fhir`, `self._whatsapp`, `self._llm`, `self._population`, the A2A
dispatcher), so the wrapper count is bounded by the number of PROTOCOLS, not by the number of
call sites — which is exactly why this package changes ZERO node code. Every decorator here
satisfies its Protocol STRUCTURALLY (`agents/*/graph.py`'s `FhirReader`, `WhatsAppSender`,
`PatientSummaryReader`, `SummaryReader`, `PopulationFeatureClient`; `tools/mcp_cibseven/
transport.py`'s `CibSevenTransport`/`HistoryQueryingTransport`; `tools/workers/dmn_transport.py`'s
`DmnTransport`), so injecting the gated object where the raw one used to go is a type-level no-op.

WHERE THEY COME FROM. Never constructed by a graph, a node, or a composition root directly:
`maezo.gateway.tool_registry` is the ONLY sanctioned constructor (design §5.5), and the
`effect_seams_gated` readiness check at each root asserts, at runtime, that every effect seam a
graph received is one of these.

WHAT A DECORATOR DOES, IN ORDER (design §5.4, mirroring `transport.py:1130-1133`'s "gate seams
checked before anything durable is written" ordering):

  1. `decide_effect(...)` with BOUNDED TOKENS ONLY. The payload — the prompt, the recipient, the
     patient id, the process variables — stays in the wrapper's own frame and is handed to the
     inner seam only after the decision. `EffectCall` has no field it could travel in (I-3).
  2. `log_effect_decision(...)`: exactly ONE shadow line per call, always, allow or deny. This is
     the Phase-1 evidence source (§9.2) — a call that decides without recording is invisible.
  3. On an ENFORCED deny: raise the CLASS's DECLARED denial shape, read out of
     `effect_classes.denial_shape_for` — never a refusal invented at the call site (§6.1, A-12).
  4. `audita_antes` classes: the pre-effect record, BEFORE delegating (I-1). Inert today — every
     class ships `audita_antes=False` pending the Q-9 SRE latency sign-off.
  5. Delegate to the inner seam and return its value unchanged.

SHIPS INERT. The shipped manifest is `status: DRAFT` / `modo: shadow` / every class
`enforcement: shadow`, so step 3 is unreachable and steps 1/2 add no I/O (`action_approvals` is
`lru_cache`d; the decision is dict lookups). `tests/unit/gateway/seams/test_seam_parity.py`
proves it per seam: gateway-live vs gateway-neutralized produce IDENTICAL outcomes — the same
proof shape the worker leg already carries (`harness.py:1583-1585`).
"""

from maezo.gateway.seams._base import (
    EFFECT_SEAM_KEYS,
    EffectDeniedError,
    GatedSeam,
    PreEffectAuditHook,
    SeamContext,
    denial_for,
    gate,
    is_gated_seam,
)
from maezo.gateway.seams.a2a import GatedDelegationDispatcher
from maezo.gateway.seams.cibseven import GatedCibSevenTransport
from maezo.gateway.seams.dmn import GatedDmnTransport
from maezo.gateway.seams.fhir import GatedFhirReader
from maezo.gateway.seams.inference import GatedInferenceProvider
from maezo.gateway.seams.population import GatedPopulationFeatureClient
from maezo.gateway.seams.whatsapp import GatedWhatsAppSender

__all__ = [
    "EFFECT_SEAM_KEYS",
    "EffectDeniedError",
    "GatedCibSevenTransport",
    "GatedDelegationDispatcher",
    "GatedDmnTransport",
    "GatedFhirReader",
    "GatedInferenceProvider",
    "GatedPopulationFeatureClient",
    "GatedSeam",
    "GatedWhatsAppSender",
    "PreEffectAuditHook",
    "SeamContext",
    "denial_for",
    "gate",
    "is_gated_seam",
]
