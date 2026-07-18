"""Per-task DMN-version provenance collector (T-B, T1.10; ADR-0007 / ADR-0028).

The problem this closes (design `docs/design/audit-emit-path-wiring.md` §3.1–3.2): a worker
evaluates a DMN table via `dmn_transport.evaluate_sync(...)` and gets back a `DmnVersion`, but
that version is **discarded** inside the worker function — it never reaches the harness, which
only sees the returned output dict. The harness needs those versions to populate
`AuditRecord.dmn_versions` (the ADR-0007 non-repudiation tuple: *which DMN version drove this
money/routing decision*).

Threading the version up through every `fn(variables) -> dict` return boundary would touch ~11
functions across 9 modules (several on the money path) and would leak audit metadata into BPMN
process variables — rejected (design §3.2). Instead this module holds a **module-level
`ContextVar` bound to a mutable dict** used as a per-task collector:

  - `WorkerHarness._handle` opens a fresh collector via `collect_dmn_versions()` around the
    handler dispatch (one collector per task — reset-per-task; each `_handle` runs as its own
    `asyncio.create_task`, so each gets an isolated `contextvars` copy and cannot race another
    task's collector).
  - `evaluate_sync` (the ONE sync funnel every worker DMN eval goes through) calls
    `record_dmn_version(version)` on each successful evaluation, which writes
    `collector[version.key] = version.to_audit_dict()`.

**Why a mutable container survives the sync/async thread bridge.** The harness dispatches sync
workers via `asyncio.to_thread`, which runs them under `contextvars.copy_context()`. A context
copy shares the *same dict object* the var is bound to (only the var→object binding is copied,
not the object), so in-thread `collector[key] = ...` mutations are visible to `_handle` after the
handler returns. A ContextVar holding an *immutable* value would NOT work here; the mutable-dict
container is deliberate (design §3.2).

**Fail-open by construction — best-effort ENRICHMENT, not the fail-closed audit itself.** When
no collector is set (`default=None`: unit tests calling a worker directly, tooling, any code path
outside an audited dispatch), `record_dmn_version` is a silent no-op — a DMN eval that never
reaches an audited dispatch simply yields empty `dmn_versions`. This is acceptable: `dmn_versions`
is provenance *enrichment* of the audit row, distinct from the fail-CLOSED emit-before-complete
guarantee the harness enforces (design §5, review point #5). The audit row is still written; it
just may carry `{}` for `dmn_versions` if the effect consulted no DMN table (many worker topics
consult none).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from maezo.tools.workers.dmn_transport import DmnVersion

# Module-level collector. `None` means "not inside an audited dispatch" — see module docstring.
# The value is a MUTABLE dict deliberately (the container survives the to_thread context copy).
_DMN_AUDIT_COLLECTOR: ContextVar[dict[str, Any] | None] = ContextVar("dmn_audit_collector", default=None)


def record_dmn_version(version: DmnVersion) -> None:
    """Record one consulted DMN version into the current task's collector, if one is set.

    Keyed on `version.key` (the decision-definition key) so evaluating the same table twice in
    one task collapses to a single provenance entry (last write wins — versions are stable within
    a dispatch since `CibSevenDmnTransport` caches the resolved version). No-op when no collector
    is bound (default `None`) — see the module docstring's fail-open note.
    """
    collector = _DMN_AUDIT_COLLECTOR.get()
    if collector is not None:
        collector[version.key] = version.to_audit_dict()


@contextmanager
def collect_dmn_versions() -> Iterator[dict[str, Any]]:
    """Bind a fresh per-task DMN-version collector for the duration of the block.

    Yields the mutable dict that `record_dmn_version` writes into. Resets the `ContextVar` to its
    previous binding on exit (reset-per-task, design §3.2 / MUST-FIX 1) — so a collector never
    leaks across tasks even on the same OS thread / event loop.
    """
    collector: dict[str, Any] = {}
    token = _DMN_AUDIT_COLLECTOR.set(collector)
    try:
        yield collector
    finally:
        _DMN_AUDIT_COLLECTOR.reset(token)
