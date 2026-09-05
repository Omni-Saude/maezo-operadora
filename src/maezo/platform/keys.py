"""SHARED business-key segment normalisation — strict-refusal, position-preserving (CC-15).

WHY THIS MODULE EXISTS. `key_segment`/`is_blank` were born in `agents/andre/keys.py` to close the
M-8 defect: Andre's two business-key families (`ADEQ`/`PAGTO`) each had two composers that
silently disagreed on how to normalise a segment, so an idempotent start/delegation keyed by one
composer could not find the instance keyed by the other. The Agent Fleet Audit (CC-15) found the
SAME shape of defect recurring in a THIRD family — `PAGTO-KEY-NORMALISATION` (gap register,
`GAP-REGISTER.yaml`) — precisely because the fix lived inside one agent's package instead of a
place every agent and worker could reach. This module is the fix for THAT: the lowest point in the
dependency graph both agents and workers can import from without an inversion.

THE RULE THIS MODULE ENFORCES — POSITION-PRESERVING, REFUSE-ON-BLANK, NEVER SEGMENT-DROPPING.
Every segment of a composed key holds a FIXED position; an empty segment is a caller-visible
condition (`is_blank` returns `True`, callers compose `ValueError` around it), never something
silently dropped or coerced into an empty string that quietly shifts the segments after it. A
composer built out of `key_segment` can never mint a degenerate key (`PAGTO-{tenant}--`) or
collapse two structurally different inputs onto the same string — the exact M-8 collision.

CONTRACT.
  - `key_segment(value)`: normalise ANY value into a business-key segment via
    `str(value or "").strip()`. `None`, `0`, `""`, and whitespace-only strings all normalise to
    `""` (an explicit `None` must never become the literal `"None"`); every other value goes
    through `str()` so an `int` id composes identically no matter which call site holds it.
  - `is_blank(value)`: `True` iff `key_segment(value)` is `""` — the single predicate callers use
    to decide whether to raise instead of composing a degenerate/ambiguous key.
  - Neither function drops, reorders, or truncates a segment. Refusal is the caller's job (raise a
    typed error on `is_blank`); this module only ever normalises and reports.

Leaf module by design: no imports from `maezo.agents.*` or `maezo.tools.*` — everything above this
module may depend on it, it depends on nothing above it.

`maezo.agents.andre.keys` re-exports `key_segment`/`is_blank` from here for backward
compatibility (`adequacao_business_key`/`pagto_business_key`, Andre's own composers, stay there).
Workers import directly from this module — `maezo.tools.workers.contas`/`recurso` used to import
`key_segment` from `maezo.agents.andre.keys`, a worker depending on an agent package; that
inversion is what CC-15 corrects.
"""

from __future__ import annotations

from typing import Any

__all__ = ["is_blank", "key_segment"]


def key_segment(value: Any) -> str:
    """Normalise ANY value into a business-key segment: `str(value or "").strip()`.

    Single normalisation for every business-key composer in the fleet — the M-8 divergence was
    precisely that one call site normalised and another did not. `None` and `0` and `""` all
    normalise to `""` (an explicit `None` must never become the literal `"None"`), and every other
    value goes through `str()` so an `int` id composes identically no matter which site holds it.
    """
    return str(value or "").strip()


def is_blank(value: Any) -> bool:
    """True iff `value` is absent / empty / whitespace-only after `key_segment` normalisation."""
    return not key_segment(value)
