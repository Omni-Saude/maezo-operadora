"""Gated A2A delegation seam — action class `delegacao_a2a` (C3, design §6.1).

Gates `a2a/dispatcher.py:277` `DelegationDispatcher.delegate` — the edge design §6.1 describes as
"instrui, não decide, but it crosses an agent boundary and carries context, so it is a tool
effect in the XRD-09 sense". Live call sites: `agents/helena/delegation.py:137`,
`agents/carolina/delegation.py:185`, `agents/andre/delegation.py:233,:407`, reached from the
worker dossier handlers (`credenciamento.py:637`, `adequacao.py`, `pagto.py`).

=================================================================================================
WHY THIS ONE IS A SUBCLASS AND THE OTHER SIX ARE STRUCTURAL DECORATORS
=================================================================================================
The other six seams are declared as `Protocol`s, so a structurally-identical decorator is a
type-level no-op at every injection site. `DelegationDispatcher` is NOT a Protocol — it is a
concrete class, and it is spelled as the annotation in eight places
(`agents/{helena,carolina,andre}/delegation.py`, `tools/workers/{credenciamento,adequacao,pagto}
.py`, `runtime/worker_runtime/service.py` ×2). A structural decorator would therefore have forced
a Protocol introduction plus eight annotation edits across worker and agent modules — real churn,
on files this leg has no other reason to touch, for a wave whose whole claim is invisibility.

Subclassing keeps every existing annotation valid and the blast radius at ZERO files. Two things
make it safe rather than clever, and both are pinned by tests:

  1. `delegate` is the ONLY public method of `DelegationDispatcher` (everything else is `_`
     prefixed). `test_delegation_dispatcher_public_surface_is_only_delegate` asserts exactly that,
     so the day someone adds a second public method the gate fails loudly instead of silently
     inheriting an ungated path.
  2. `GatedSeam.__init__` deliberately does NOT call `DelegationDispatcher.__init__`: this object
     holds no registry, handler map, audit emitter or fact producer of its own. Every base
     attribute is reachable only from `delegate` and its private helpers, and `delegate` is fully
     overridden — it never calls `super().delegate()`, it calls `self._inner.delegate()`.

DENIAL SHAPE. `DEGRADACAO_SEM_DOSSIE`: "the delegating side degrades exactly as it does when the
dossier seam is absent — never a fabricated dossier". Re-derived, that means a RAISE, not a
`DelegationResult` rejection: `credenciamento.py:644` catches `Exception` and returns
`{"dossier_prepared": False, "dossier_gap": "delegation_failed"}`, the human User Task still
opens, and the handler never raises onward (DL-0037). Manufacturing a rejection instead would
require a new `RejectionReason` member — new vocabulary, which this wave may not add.

The envelope never enters a decision (I-3): `task_id`, `case_meta` and the dossier body stay in
this frame and are handed to the inner dispatcher only after the gate returns.
"""

from __future__ import annotations

from typing import Any

from maezo.a2a.delegation import DelegationEnvelope
from maezo.a2a.dispatcher import DelegationDispatcher, DelegationResult
from maezo.gateway.seams._base import GatedSeam, SeamContext, gate

_OP_DELEGATE = "a2a.delegate"


class GatedDelegationDispatcher(GatedSeam, DelegationDispatcher):
    """Gating decorator over a `DelegationDispatcher`, by subclass — see the module docstring."""

    __slots__ = ()

    async def delegate(self, envelope: DelegationEnvelope) -> DelegationResult:
        await gate(self._seam, _OP_DELEGATE)
        result: DelegationResult = await self._inner.delegate(envelope)
        return result


def gate_a2a(inner: Any, seam: SeamContext) -> GatedDelegationDispatcher:
    """The ONLY sanctioned way to build one (called from `gateway.tool_registry`)."""
    return GatedDelegationDispatcher(inner, seam=seam)


__all__ = ["GatedDelegationDispatcher", "gate_a2a"]
