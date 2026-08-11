"""Gated DMN evaluation seam — action class `avaliacao_dmn` (C0, design §6.1).

Satisfies `tools/workers/dmn_transport.py:170`'s `DmnTransport` Protocol (`evaluate` + `close`).
Design R-7 is why ONE wrapper suffices: Scout A counted ~10+ "DMN sites" per graph, but those are
call sites of a PRIVATE per-graph helper — the actual transport call appears EXACTLY ONCE per
graph, behind `_evaluate_dmn` (rafael `graph.py:546,:548`; marina `:804,:806`; helena `:730,:761`).
The wrapper count is bounded by Protocols, not call sites.

THE DENIAL TYPE IS LOAD-BEARING HERE, MORE THAN ANYWHERE ELSE. Every `_evaluate_dmn` helper
catches `(DmnEvaluationError, DmnNoResultError)` and NOTHING wider — e.g. `rafael/graph.py:549`:

    except (DmnEvaluationError, DmnNoResultError) as exc:
        return {"error": f"DMN `{table}` indisponivel: {exc}"}

A plain `RuntimeError` refusal would sail past that handler and kill the turn. So the
`ROTA_DMN_INDISPONIVEL` shape raises a `DmnEvaluationError` SUBCLASS (`seams/_base.py`'s
`_dmn_denial_type`), the node takes its DECLARED DMN-unavailable path (ADR-0028 fail-closed ->
human), and it never fabricates a favourable outcome — which is exactly the denial-mutation proof
shape §6.1's C0 row states.

`decision_key` and `variables` stay in this frame: the DMN inputs carry case facts and are never
put into an `EffectCall` (I-3). One catalogue operation covers every table — the table name is a
per-call datum, not a governance axis, and `avaliacao_dmn` is the class the manifest declares.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from maezo.gateway.seams._base import GatedSeam, SeamContext, gate

if TYPE_CHECKING:  # pragma: no cover - types only; no runtime `maezo.tools` import at module scope
    from maezo.tools.workers.dmn_transport import DmnVersion

_OP_EVALUATE = "dmn.evaluate"


class GatedDmnTransport(GatedSeam):
    """Protocol-preserving decorator over a `DmnTransport`."""

    __slots__ = ()

    async def evaluate(
        self,
        decision_key: str,
        variables: dict[str, Any],
        *,
        tenant: str | None = None,
    ) -> tuple[list[dict[str, Any]], DmnVersion]:
        """Gate, then delegate.

        `tenant` here is the DMN transport's OWN parameter — the engine's tenant-scoped
        decision-definition selector (`dmn_transport.py:174-177`), `None` for every T1.5-migrated
        table. It is NOT the policy tenant and is NEVER read as one: the tenant the decision runs
        under is closure-bound in `SeamContext` (adversary A-8). Same spelling, different axis;
        the fence test that forbids a `tenant`/`principal` ARGUMENT from reaching a decision
        allowlists this one by name for exactly that reason.
        """
        await gate(self._seam, _OP_EVALUATE)
        evaluated: tuple[list[dict[str, Any]], DmnVersion] = await self._inner.evaluate(
            decision_key, variables, tenant=tenant
        )
        return evaluated

    async def close(self) -> None:
        """Lifecycle, not an effect: no catalogue operation, no decision, no telemetry line."""
        await self._inner.close()


def gate_dmn(inner: Any, seam: SeamContext) -> GatedDmnTransport:
    """The ONLY sanctioned way to build one (called from `gateway.tool_registry`)."""
    return GatedDmnTransport(inner, seam=seam)


__all__ = ["GatedDmnTransport", "gate_dmn"]
