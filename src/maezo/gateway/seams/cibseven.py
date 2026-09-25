"""Gated CIB Seven transport seam — `consulta_processo` (C0) + `correlacao_processo` (C3).

Satisfies `tools/mcp_cibseven/transport.py:151`'s `CibSevenTransport` Protocol and — when the
inner transport does — `:167`'s `HistoryQueryingTransport`. This is the seam adversary A-1 names:
every graph already holds a raw `CibSevenTransport` and can call `correlate_message` /
`get_process_status` on it with no fence, because only `start_process_instance` is statically
fenced (`scripts/ci/check_start_process_fence.py:68`). After this wrapper, the object the graph
holds IS the gate.

=================================================================================================
WHY `start_process_instance` IS A PURE PASS-THROUGH — the one deliberate hole, disclosed
=================================================================================================
`cibseven.start_process` IS catalogued (`inicio_processo_regulatorio`, C3), but this wrapper does
NOT gate `start_process_instance`, and the manifest's agent-start surface therefore stays
`choked: false`. That is a truth claim, not an omission:

`start_process_instance` is only ever reached from INSIDE `start_process_idempotent`
(`transport.py:1212-1391`; the CI fence makes every other call site a build failure). By the time
control gets there, step 1 has ALREADY written the durable ADR-0007 claim (`:1303-1307`). A
refusal raised at `:1352` lands in the `except CibSevenError` at `:1353`, which for a gated
family logs `cibseven_start_claim_orphaned` and re-raises — i.e. a POLICY denial would wedge a
business key exactly as an engine outage does. Gating there means changing
`start_process_idempotent`'s internals, which design §5.8 / I-6 forbids and the B2 brief repeats.
The honest Phase-0 posture is therefore: the start operation is catalogued and reachable by the
decision core, but no seam wires it, so it produces no shadow evidence and its surface is not
flipped. Wiring it is a design act (a decision point BEFORE the claim, inside the fence), not a
wrapper act — recorded for the Onda-1 PR ledger and §9.2's residuals list.

RESIDUAL ON THE READ LEG, ALSO DISCLOSED. `find_active_instance` IS gated, and it is called at
`transport.py:1340` — step 3, i.e. AFTER the claim. Under a future `consulta_processo` flip a
denial there propagates out of `start_process_idempotent` and NO instance is created, which is
the fail-closed direction the `LEITURA_INCONCLUSIVA` shape demands. For a STRICT family it would
also leave a claim with no engine instance and no `cibseven_start_claim_orphaned` announcement
(that log line only covers the `start_process_instance` branch). Inert today (nothing enforces);
it is a precondition on the `consulta_processo` flip, not a Phase-0 defect.

THE DENIAL MUST NOT READ AS "NO ACTIVE INSTANCE" (§6.1's highest-risk C0 item). `LEITURA_
INCONCLUSIVA` RAISES a `CibSevenError` subclass; it never returns `None`. Returning `None` from a
denied `find_active_instance` would make an un-answerable question look like a definite "nothing
is running" — which is precisely the anti-dupla-terminação hole the class exists to protect.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from maezo.gateway.seams._base import GatedSeam, SeamContext, gate

if TYPE_CHECKING:  # pragma: no cover - types only; the runtime import stays branch-local
    from maezo.tools.mcp_cibseven.transport import (
        HistoricProcessVariables,
        ProcessInstance,
        ProcessStatus,
    )

_OP_FIND_ACTIVE = "cibseven.find_active_instance"
_OP_FIND_ANY = "cibseven.find_any_instance"
_OP_CORRELATE = "cibseven.correlate_message"
_OP_STATUS = "cibseven.get_process_status"
_OP_READ_HISTORIC_VARIABLES = "cibseven.read_historic_variables"


class GatedCibSevenTransport(GatedSeam):
    """Protocol-preserving decorator over a `CibSevenTransport`."""

    __slots__ = ()

    async def find_active_instance(self, business_key: str) -> ProcessInstance | None:
        await gate(self._seam, _OP_FIND_ACTIVE)
        active: ProcessInstance | None = await self._inner.find_active_instance(business_key)
        return active

    async def start_process_instance(
        self,
        process_key: str,
        business_key: str,
        variables: dict[str, Any],
    ) -> ProcessInstance:
        """PASS-THROUGH, deliberately ungated — see the module docstring's disclosed hole."""
        started: ProcessInstance = await self._inner.start_process_instance(
            process_key, business_key, variables
        )
        return started

    async def correlate_message(
        self,
        message_name: str,
        business_key: str,
        variables: dict[str, Any],
        *,
        correlation_keys: dict[str, Any] | None = None,
        all_matching: bool = False,
    ) -> None:
        await gate(self._seam, _OP_CORRELATE)
        await self._inner.correlate_message(
            message_name,
            business_key,
            variables,
            correlation_keys=correlation_keys,
            all_matching=all_matching,
        )

    async def get_process_status(self, business_key: str) -> ProcessStatus:
        await gate(self._seam, _OP_STATUS)
        status: ProcessStatus = await self._inner.get_process_status(business_key)
        return status

    async def close(self) -> None:
        """Lifecycle, not an effect: no catalogue operation, no decision, no telemetry line."""
        await self._inner.close()


class GatedHistoryQueryingCibSevenTransport(GatedCibSevenTransport):
    """Adds `find_any_instance` — built ONLY when the inner transport actually has it.

    Load-bearing: `_require_strict_gate_seams` (`transport.py:1035-1049`) probes the injected
    transport with `isinstance(..., HistoryQueryingTransport)` and REFUSES a strict-family start
    when it fails. A wrapper that always exposed `find_any_instance` would make a mis-wired root
    pass that probe and fail later — turning a fail-closed refusal into a fail-late one, i.e. a
    behaviour change on the one path whose whole point is not having any.
    """

    __slots__ = ()

    async def find_any_instance(self, business_key: str, *, process_key: str = "") -> ProcessInstance | None:
        await gate(self._seam, _OP_FIND_ANY)
        found: ProcessInstance | None = await self._inner.find_any_instance(
            business_key, process_key=process_key
        )
        return found


class GatedHistoryReadingCibSevenTransport(GatedHistoryQueryingCibSevenTransport):
    """Adds `read_historic_variables` (GAP-XHITL-4) — built ONLY when the inner has BOTH legs.

    Same rule as the parent: the gated object exposes a history read only when the inner really
    has it, so a caller's `isinstance(..., HistoricVariableReadingTransport)` probe stays honest.
    The read is a C0 `consulta_processo` operation — it moves nothing in the engine.
    """

    __slots__ = ()

    async def read_historic_variables(
        self, process_instance_id: str, names: tuple[str, ...]
    ) -> HistoricProcessVariables | None:
        await gate(self._seam, _OP_READ_HISTORIC_VARIABLES)
        found: HistoricProcessVariables | None = await self._inner.read_historic_variables(
            process_instance_id, names
        )
        return found


def gate_cibseven(inner: Any, seam: SeamContext) -> GatedCibSevenTransport:
    """The ONLY sanctioned way to build one. Preserves the inner's history-reading Protocols."""
    from maezo.tools.mcp_cibseven.transport import (
        HistoricVariableReadingTransport,
        HistoryQueryingTransport,
    )

    if isinstance(inner, HistoryQueryingTransport):
        if isinstance(inner, HistoricVariableReadingTransport):
            return GatedHistoryReadingCibSevenTransport(inner, seam=seam)
        return GatedHistoryQueryingCibSevenTransport(inner, seam=seam)
    return GatedCibSevenTransport(inner, seam=seam)


__all__ = [
    "GatedCibSevenTransport",
    "GatedHistoryQueryingCibSevenTransport",
    "GatedHistoryReadingCibSevenTransport",
    "gate_cibseven",
]
