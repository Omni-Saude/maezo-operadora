"""T3.3 chaos / cross-process resilience suites (docs/design/T3.3-chaos-resilience.md).

Isolated from `tests/integration/processes/` (which drives the real CIB Seven engine) — the
suites under this package are the **non-engine, seam-level fault-injection** suites (W0 harness +
W1-seam-faults): they need a real Postgres (audit-lane migrations 0001->0005 applied) but NOT a
running CIB Seven engine. Fault injection happens at the Python seam (monkeypatched
`PostgresAuditSink`/`CibSevenTransport` call sites), never a real container kill — the
container-control suites (B1c/B2/B3, real pod-kill / daemon-restart / engine-restart) are a
separate, later, serialized wave (W2) per the ratified design's §5 policy.
"""

from __future__ import annotations
