"""Adapters — infrastructure-facing implementations of the `maezo.ports` seams (ADR-0037).

An adapter is the ONLY place in this repository where a broker, a schema registry, a cloud SDK or
a contract-owner wire shape may appear. The payer core depends on `maezo.ports`; the ports depend
on nothing; the adapters depend on both the ports and the infrastructure — never the reverse
(ADR-0037 XRD-06/XRD-07, immutable prohibition #2: no vendor type in the payer core).

An adapter exception may NEVER cross a port boundary: it must be mapped onto the closed
`maezo.ports.errors.PortFailureReason` taxonomy by the implementation that returns a `PortResult`
(see `maezo.ports.errors`).
"""

from __future__ import annotations
