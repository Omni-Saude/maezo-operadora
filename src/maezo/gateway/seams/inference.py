"""Gated LLM inference seam — action class `inferencia_llm` (C2, design §6.1), SPLIT BY ZONE.

Satisfies the surface every graph uses of `runtime/inference.py:899`'s `InferenceProvider`:
`generate(prompt, *, phi=…, agent_id=…, tenant_id=…)` (15 call sites, all `self._llm.generate`),
plus the three read-only accessors the composition roots touch (`provider_name`, `model_id`,
`health_check`) so the gated object is a drop-in wherever the raw provider went.

TWO OPERATIONS, ONE METHOD. `inference.generate` vs `inference.generate_phi` is chosen from the
call's `phi=` flag, exactly as the catalogue declares (`effect_classes.py` C2 block). That is how
the shadow telemetry distinguishes a PHI-bearing generation from a general one WITHOUT the prompt
ever leaving this frame — the prompt is not a field `EffectCall` has (I-3).

=================================================================================================
I-6: ADR-0006 ZONE ROUTING STAYS INDEPENDENT OF THE PEP
=================================================================================================
`PhiZoneRoutingError` (`runtime/inference.py:118`, raised at `:1059`) is the structural refusal
that keeps PHI out of a non-BR-resident model. It is NOT re-implemented, consulted, or
short-circuited here:

  * the wrapper NEVER decides on `phi`; it only picks which catalogue token to record under;
  * the wrapper NEVER catches `PhiZoneRoutingError` — the delegation is bare, so the error
    propagates to the node exactly as it did before this seam existed;
  * with the chokepoint fully NEUTRALIZED (no gate at all), `phi=True` against a general-zone
    provider still refuses. `tests/unit/gateway/seams/test_seam_proofs.py:671::
    test_phi_zone_routing_fail_close_is_independent_of_the_pep` proves both directions — gated
    and un-gated — against the same provider.

That is the whole content of I-6 for this seam: the chokepoint can only SUBTRACT permission, and
removing it must not restore any.

`agent_id`/`tenant_id` are metering correlation ids (`runtime/inference.py:1036-1043`: "purely
observational: never affects routing"). They are forwarded to the inner provider UNCHANGED and
are NEVER read as the decision's principal or tenant — those are closure-bound in `SeamContext`
(adversary A-8), and a fence test asserts no gated wrapper derives a principal from a call arg.
"""

from __future__ import annotations

from typing import Any

from maezo.gateway.seams._base import GatedSeam, SeamContext, gate
from maezo.runtime.inference import InferenceProvider

_OP_GENERATE = "inference.generate"
_OP_GENERATE_PHI = "inference.generate_phi"


class GatedInferenceProvider(GatedSeam, InferenceProvider):
    """Gating decorator over an `InferenceProvider` — BY SUBCLASS, for the same reason as A2A.

    `InferenceProvider` is a concrete class, not a Protocol, and it is spelled as the annotation
    at the handler factories (`make_carolina_handler`, `make_andre_handler`,
    `make_rafael_handler`) and on `Harness(inference=…)`. Subclassing keeps every one of those
    valid and the blast radius at zero files; a structural decorator would have forced a Protocol
    introduction plus annotation edits across the agent delegation modules.

    Safe, and pinned: `InferenceProvider`'s public surface is exactly
    `{generate, health_check, model_id, provider_name}` — all four overridden below — and
    `GatedSeam.__init__` deliberately does NOT call `InferenceProvider.__init__`, because this
    object owns no settings, no router and no concrete provider of its own; every one of those
    lives in `self._inner`. `test_inference_provider_public_surface_is_fully_overridden` fails the
    day a fifth public member appears, rather than letting it silently reach the raw provider.
    """

    __slots__ = ()

    @property
    def provider_name(self) -> str:
        return str(self._inner.provider_name)

    @property
    def model_id(self) -> str | None:
        model_id: str | None = self._inner.model_id
        return model_id

    def health_check(self) -> dict[str, str]:
        result: dict[str, str] = self._inner.health_check()
        return result

    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
    ) -> str:
        await gate(self._seam, _OP_GENERATE_PHI if phi else _OP_GENERATE)
        # BARE delegation: `PhiZoneRoutingError` and every provider error propagate untouched (I-6).
        result: str = await self._inner.generate(prompt, phi=phi, agent_id=agent_id, tenant_id=tenant_id)
        return result


def gate_inference(inner: Any, seam: SeamContext) -> GatedInferenceProvider:
    """The ONLY sanctioned way to build one (called from `gateway.tool_registry`)."""
    return GatedInferenceProvider(inner, seam=seam)


__all__ = ["GatedInferenceProvider", "gate_inference"]
