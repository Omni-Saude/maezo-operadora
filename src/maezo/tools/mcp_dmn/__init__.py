"""DEPRECATED (T1.5, ADR-0028 §"Fate of mcp_dmn") — do NOT use for any runtime decision.

The in-process XML `DmnServer` here fails OPEN on no-match (`return {}`) and cannot evaluate
FEEL comparison (`>=`,`<=`,`>`,`<`), range (`[a..b]`), list (`"A","B"`) or `not(...)` tests —
13 of the 55 deployed decisions are mis-evaluated or unsupported by it, and another 13
list-disjunction tables silently return the WRONG (catch-all) row (ADR-0028 §"comparison-operator
inventory", live-verified). It never had a production consumer (ADR-0028 Contexto fact 1).

Runtime DMN evaluation is ENGINE-SIDE ONLY: use
`maezo.tools.workers.dmn_transport.DmnTransport` (`CibSevenDmnTransport` in production,
`FakeDmnTransport` — fail-closed on unregistered keys — in tests). See ADR-0028 and the T1.5
migration (all former Python DMN re-implementations now call the engine through that seam).

Importing this package emits a loud `DeprecationWarning` (ADR-0028 §"Fate": the fail-open
evaluator must not remain importable WITHOUT loud deprecation). The module is retained only so
any out-of-tree import fails loudly-but-informatively instead of with a bare ImportError;
removal outright is a follow-up once the deprecation has been through one release cycle.
"""

import warnings

from maezo.tools.mcp_dmn.server import DmnServer

warnings.warn(
    "maezo.tools.mcp_dmn is DEPRECATED (T1.5/ADR-0028): the in-process XML DmnServer fails "
    "OPEN on no-match and cannot evaluate FEEL comparison/range/list tests. Use "
    "maezo.tools.workers.dmn_transport (engine-side, fail-closed) instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["DmnServer"]
